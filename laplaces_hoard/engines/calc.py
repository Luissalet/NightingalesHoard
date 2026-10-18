"""Exact numeric evaluation: arithmetic, percentages, number theory.

Every literal in the input is converted to an exact SymPy `Rational` /
`Integer` (never a lossy Python `float`), so `0.1 + 0.2` is exactly
`3/10`, not `0.30000000000000004`. Expressions never touch `eval` or
`sympify`; see `safe_ast.py` for the whitelisted parser.

`compute()` is pure; the HTTP layer runs it inside the timeout worker
(`sandbox.py`) because `9**9**9**9` or `factorial(10**9)` never return.
"""
from __future__ import annotations

import re
from typing import Any, Optional

import sympy

from .safe_ast import UnsafeExpressionError, parse_expression

__all__ = ["compute", "UnsafeExpressionError", "CalcError", "format_decimal", "MAX_OUTPUT_CHARS"]

MAX_OUTPUT_CHARS = 2000


class CalcError(ValueError):
    """The expression parsed but has no usable value (division by zero, NaN...)."""


def _parse_hint(expression: str) -> str:
    hints = []
    if "%" in expression:
        hints.append("for 'p% of x' write pct(p, x) (e.g. pct(15, 2347)); a bare % is the modulo operator")
    if re.search(r"\d\s*[x×]\s*\d", expression):
        hints.append("use * for multiplication")
    hints.append("write it in Python-like syntax, e.g. 2**10 or 2^10, sqrt(2), 1/3 + 1/6")
    return "; ".join(hints)


_THOUSANDS_DOT = re.compile(r"(?<!\d)(\d{1,3})\.(\d{3})(?!\d)")


def _thousands_separator_warning(expression: str) -> Optional[str]:
    """Flag a number like "1.000" that parses fine as 1.0 but might have
    been meant as one thousand (a dot used as a thousands separator).

    Never changes the result - calc's numbers are exact and unambiguous
    once parsed - only adds a warning next to a value someone might
    misread."""
    # Only numbers that look like thousands, not like a real 3-decimal
    # value: a group ending in 0 ("1.000", "2.500" - nobody writes 2.500
    # for two and a half) or two or more digits before the dot ("12.345").
    # "1.035" (a rate), "3.142", "0.125" are left alone: flagging every
    # 3-decimal number would bury the one warning that matters.
    matches = [(a, b) for a, b in _THOUSANDS_DOT.findall(expression)
               if a[0] != "0" and (b.endswith("0") or len(a) >= 2)]
    if not matches:
        return None
    a, b = matches[0]
    examples = ", ".join(f"{x}.{y}" for x, y in matches[:3])
    return (
        f"'{examples}' was read with '.' as the decimal point ({a}.{b} = {int(a)}.{b.rstrip('0') or '0'}, not {a}{b}); "
        f"write {a}{b} instead of {a}.{b} if you meant it as a thousands separator"
    )


def format_decimal(value: sympy.Expr, precision: int) -> str:
    """`N(value, precision)` as text, without the trailing zeros SymPy pads with."""
    text = str(sympy.N(value, precision))
    mantissa, sep, exponent = text.partition("e")
    if "." in mantissa and not any(ch in mantissa for ch in "I*"):
        mantissa = mantissa.rstrip("0").rstrip(".")
    return mantissa + (sep + exponent if sep else "")


def _cap(text: str) -> tuple[str, bool]:
    if len(text) <= MAX_OUTPUT_CHARS:
        return text, False
    return text[:MAX_OUTPUT_CHARS] + "…", True


def compute(expression: str, precision: int = 15) -> dict[str, Any]:
    try:
        precision = max(1, min(int(precision), 1000))
    except (TypeError, ValueError):
        precision = 15
    raw: dict = {}
    try:
        result, symbols = parse_expression(expression, allow_symbols=False, raw_result=raw)
    except UnsafeExpressionError as exc:
        if "could not parse" in str(exc):
            raise UnsafeExpressionError(f"{exc}. Hint: {_parse_hint(expression)}") from exc
        raise
    if symbols:
        # allow_symbols=False guarantees this never happens; kept for safety.
        raise UnsafeExpressionError("calc does not accept variables; use the math tool")

    if raw:
        return _raw_result(expression, raw, precision)

    if isinstance(result, list):
        if re.search(r"\d,\d", expression):
            raise UnsafeExpressionError(
                "calc evaluates one expression, not a list; if this was meant to be one decimal "
                "number, use '.' instead of ',' (3.5, not 3,5) - a comma between digits is read as "
                "separating two values"
            )
        raise UnsafeExpressionError(
            "calc evaluates one expression, not a list; for several values use sum(...), mean(...), "
            "max(...) etc., or make one call per value"
        )

    if isinstance(result, sympy.logic.boolalg.BooleanAtom):
        value = bool(result)
        return {
            "input": expression, "exact": str(value), "decimal": None, "digits": precision,
            "is_exact": True, "latex": r"\text{" + str(value) + "}", "value": value,
        }
    if isinstance(result, sympy.core.relational.Relational):
        raise CalcError("this comparison could not be decided exactly; compare the two sides with separate calc calls")

    exact = result
    if not getattr(exact, "is_number", False):
        raise CalcError("the expression did not reduce to a number")
    if exact.has(sympy.zoo, sympy.nan) or exact in (sympy.zoo, sympy.nan):
        raise CalcError("division by zero or undefined result")

    try:
        decimal_text = format_decimal(exact, precision)
    except Exception as exc:  # noqa: BLE001
        raise CalcError(f"could not evaluate expression: {exc}") from exc
    if decimal_text in ("nan", "zoo"):
        raise CalcError("division by zero or undefined result")

    # is_exact: the decimal shown *is* the value (no rounding happened)
    is_exact = False
    if exact.is_rational:
        try:
            is_exact = sympy.Rational(decimal_text) == exact
        except (TypeError, ValueError):
            is_exact = False

    digit_count = None
    if exact.is_Integer and exact != 0:
        digit_count = int(sympy.integer_log(abs(int(exact)), 10)[0]) + 1
    if digit_count is not None and digit_count > MAX_OUTPUT_CHARS:
        # far too long to be useful (and str() of a >4300-digit int is refused
        # by Python itself): give the rounded value plus the digit count
        exact_text, exact_truncated = decimal_text, True
        latex_text, latex_truncated = "", True
    else:
        try:
            exact_text, exact_truncated = _cap(sympy.sstr(exact))
            latex_text, latex_truncated = _cap(sympy.latex(exact))
        except ValueError:  # huge rational parts: same int->str limit
            exact_text, exact_truncated = decimal_text, True
            latex_text, latex_truncated = "", True
    out: dict[str, Any] = {
        "input": expression,
        "exact": exact_text,
        "decimal": decimal_text,
        "digits": precision,
        "is_exact": is_exact,
        "latex": None if latex_truncated else latex_text,
    }
    if exact_truncated:
        out["exact_truncated"] = True
        if digit_count is not None:
            out["exact_digit_count"] = digit_count
    warning = _thousands_separator_warning(expression)
    if warning:
        out["warning"] = warning
    return out


def _raw_result(expression: str, raw: dict, precision: int) -> dict[str, Any]:
    kind = raw["kind"]
    args = raw["args"]
    if len(args) != 1:
        raise UnsafeExpressionError(f"{kind}() takes exactly one integer argument")
    if kind == "isprime":
        n = _require_int(args[0], "isprime")
        value = bool(sympy.isprime(n))
        return {
            "input": expression,
            "exact": str(value),
            "decimal": None,
            "digits": precision,
            "is_exact": True,
            "latex": None,
            "value": value,
        }
    if kind == "nextprime":
        n = _require_int(args[0], "nextprime")
        value = int(sympy.nextprime(n))
        return {
            "input": expression,
            "exact": str(value),
            "decimal": str(value),
            "digits": precision,
            "is_exact": True,
            "latex": sympy.latex(sympy.Integer(value)),
            "value": value,
        }
    if kind == "factorint":
        n = _require_int(args[0], "factorint")
        factors = sympy.factorint(n)
        factors_str = {str(k): int(v) for k, v in factors.items()}
        exact = " * ".join(
            f"{k}^{v}" if v > 1 else str(k) for k, v in factors_str.items()
        ) or "1"
        return {
            "input": expression,
            "exact": exact,
            "decimal": None,
            "digits": precision,
            "is_exact": True,
            "latex": None,
            "factors": factors_str,
        }
    raise UnsafeExpressionError(f"unknown raw function: {kind}")


def _require_int(value: sympy.Expr, fn: str) -> int:
    if not getattr(value, "is_number", False) or not value.is_integer:
        raise UnsafeExpressionError(f"{fn}() needs an integer argument")
    return int(value)

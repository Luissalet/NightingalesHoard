"""Whitelist AST -> SymPy converter.

Never calls `eval` or `sympify` on raw user text. The expression string is
parsed with the stdlib `ast` module and only a small, explicit set of node
types is walked; every leaf and every function call is matched against an
allowlist before being turned into a SymPy object by hand. Anything else
(attribute access, subscripts, lambdas, comprehensions, imports, starred
args, keyword args, unknown names) raises `UnsafeExpressionError` before any
SymPy code runs.

Used by both the `calc` engine (numeric-only) and the `symbolic` (math)
engine (adds free symbols and `==`/`<`/`>` as Eq/relational).
"""
from __future__ import annotations

import ast
import re
import inspect
from typing import Callable, Optional

import sympy
from sympy import Symbol

CONSTANTS: dict[str, sympy.Expr] = {
    "pi": sympy.pi,
    "e": sympy.E,
    "tau": 2 * sympy.pi,
    "inf": sympy.oo,
    "oo": sympy.oo,
    "I": sympy.I,
    "nan": sympy.nan,
}


class UnsafeExpressionError(ValueError):
    """Raised when the expression uses a construct outside the whitelist."""


def _pct(p, x):
    return (p / 100) * x


def _pct_change(old, new):
    return ((new - old) / old) * 100


def _ratio(a, b):
    return a / b


def _values(name: str, args: tuple) -> list:
    """Accept both f(1, 2, 3) and f([1, 2, 3]) for the aggregate helpers."""
    if len(args) == 1 and isinstance(args[0], list):
        args = tuple(args[0])
    if not args:
        raise UnsafeExpressionError(f"{name}() needs at least one value")
    if any(isinstance(a, list) for a in args):
        raise UnsafeExpressionError(f"{name}() takes numbers or one list of numbers")
    return list(args)


def _mean(*args):
    vals = _values("mean", args)
    return sympy.Add(*vals) / len(vals)


def _median(*args):
    vals = sorted(_values("median", args), key=lambda v: sympy.N(v))
    n = len(vals)
    mid = n // 2
    if n % 2:
        return vals[mid]
    return (vals[mid - 1] + vals[mid]) / 2


def _log(x, base):
    return sympy.log(x, base)


def _root(x, n):
    return sympy.root(x, n)


def _atan2(y, x):
    return sympy.atan2(y, x)


def _mod(a, b):
    return sympy.Mod(a, b)


def _round(x, *n):
    """Exact rounding, half away from zero (how people and spreadsheets round).

    round(2.5) = 3, round(-2.5) = -3, round(1.005, 2) = 1.01 — the literal is
    already an exact rational, so no binary-float surprise can creep in.
    """
    if len(n) > 1:
        raise UnsafeExpressionError("round() takes a value and an optional number of decimals")
    digits = n[0] if n else sympy.Integer(0)
    if not getattr(digits, "is_integer", False):
        raise UnsafeExpressionError("round(x, n): n must be a whole number of decimals")
    if not getattr(x, "is_real", False):
        raise UnsafeExpressionError("round() needs a real number")
    scale = sympy.Integer(10) ** digits
    q = x * scale
    rounded = sympy.sign(q) * sympy.floor(sympy.Abs(q) + sympy.Rational(1, 2))
    return rounded / scale


def _isprime(n):
    return sympy.Integer(1) if sympy.isprime(n) else sympy.Integer(0)


# Functions available inside a `calc` expression. Each maps to a callable
# that takes already-converted SymPy arguments and returns a SymPy object
# (or, for a few number-theory helpers, a plain Python object handled by
# the caller separately — see FUNCTIONS_RAW in calc.py).
FUNCTIONS: dict[str, Callable] = {
    "sqrt": sympy.sqrt,
    "cbrt": sympy.cbrt,
    "root": _root,
    "exp": sympy.exp,
    "ln": sympy.log,
    "log": _log,
    "log10": lambda x: sympy.log(x, 10),
    "log2": lambda x: sympy.log(x, 2),
    "sin": sympy.sin,
    "cos": sympy.cos,
    "tan": sympy.tan,
    "asin": sympy.asin,
    "acos": sympy.acos,
    "atan": sympy.atan,
    "atan2": _atan2,
    "sinh": sympy.sinh,
    "cosh": sympy.cosh,
    "tanh": sympy.tanh,
    "floor": sympy.floor,
    "ceil": sympy.ceiling,
    "round": _round,
    "abs": sympy.Abs,
    "min": lambda *a: sympy.Min(*_values("min", a)),
    "max": lambda *a: sympy.Max(*_values("max", a)),
    "sum": lambda *a: sympy.Add(*_values("sum", a)),
    "mean": _mean,
    "median": _median,
    "factorial": sympy.factorial,
    "binomial": sympy.binomial,
    "gcd": lambda *a: sympy.gcd(_values("gcd", a)),
    "lcm": lambda *a: sympy.lcm(_values("lcm", a)),
    "mod": _mod,
    "pct": _pct,
    "pct_change": _pct_change,
    "ratio": _ratio,
}

# These return a non-Expr Python value (bool/int/dict) and are handled
# specially by calc.py rather than folded into the SymPy tree.
RAW_FUNCTIONS = {"isprime", "nextprime", "factorint"}

ALL_FUNCTION_NAMES = set(FUNCTIONS) | RAW_FUNCTIONS

_ALLOWED_NODES = (
    ast.Expression,
    ast.Constant,
    ast.Name,
    ast.Load,
    ast.BinOp,
    ast.UnaryOp,
    ast.Compare,
    ast.Call,
    ast.Tuple,
    ast.List,
    ast.Add,
    ast.Sub,
    ast.Mult,
    ast.Div,
    ast.FloorDiv,
    ast.Mod,
    ast.Pow,
    ast.BitXor,
    ast.USub,
    ast.UAdd,
    ast.Lt,
    ast.Gt,
    ast.LtE,
    ast.GtE,
    ast.Eq,
    ast.NotEq,
)

MAX_EXACT_POWER_BITS = 20_000_000  # ~6 million decimal digits


def _safe_pow(a, b):
    """a ** b, refusing exact powers whose result alone would need gigabytes.

    A timeout is not enough here: 2**(10**10) allocates 1.25 GB before any
    timer fires. Symbolic or float powers are unaffected.
    """
    if getattr(a, "is_Rational", False) and getattr(b, "is_Integer", False) and abs(a) not in (0, 1):
        import math as _m

        p, q = abs(a.p), a.q
        bits = abs(int(b)) * max(_m.log2(p) if p > 1 else 0.0, _m.log2(q) if q > 1 else 0.0)
        if bits > MAX_EXACT_POWER_BITS:
            raise UnsafeExpressionError(
                f"the exact result of this power would have about {int(bits * 0.30103):,} digits; "
                "that is too large to compute exactly (use log10 of it, e.g. b*log10(a), instead)"
            )
    return a ** b


_BINOPS = {
    ast.Add: lambda a, b: a + b,
    ast.Sub: lambda a, b: a - b,
    ast.Mult: lambda a, b: a * b,
    ast.Div: lambda a, b: a / b,
    ast.FloorDiv: lambda a, b: sympy.floor(a / b),
    ast.Mod: lambda a, b: sympy.Mod(a, b),
    ast.Pow: _safe_pow,
    # `^` means power to every human and every calculator; nobody asks for XOR here.
    ast.BitXor: _safe_pow,
}

_CMPOPS = {
    ast.Lt: sympy.Lt,
    ast.Gt: sympy.Gt,
    ast.LtE: sympy.Le,
    ast.GtE: sympy.Ge,
    ast.Eq: sympy.Eq,
    ast.NotEq: sympy.Ne,
}


def normalize_single_equals(text: str) -> str:
    """Turn a bare `lhs = rhs` (one equation, not `==`/`<=`/`>=`/`!=`) into `lhs == rhs`.

    `ast.parse(..., mode="eval")` cannot parse `=` at all (it is a statement
    token), so a spec input like `2*x + 1 = 5` needs this rewrite before
    parsing. Only rewrites when there is exactly one bare `=`.
    """
    out = []
    i = 0
    n = len(text)
    bare_positions = []
    while i < n:
        c = text[i]
        if c in "<>=!":
            if i + 1 < n and text[i + 1] == "=":
                out.append(text[i : i + 2])
                i += 2
                continue
            if c == "=":
                bare_positions.append(len(out))
            out.append(c)
            i += 1
            continue
        out.append(c)
        i += 1
    if len(bare_positions) == 1:
        idx = bare_positions[0]
        out[idx] = "=="
    return "".join(out)


def _syntax_hint(text: str) -> str:
    """What people (and models) most often type that is not Python syntax."""
    hints = []
    if re.search(r"\d[A-Za-z_(]", text):
        hints.append("write '*' explicitly for multiplication (5*x, 2*sqrt(3)), not '5x'")
    if re.search(r"\d,\d", text):
        hints.append("use '.' as the decimal separator (3.5, not 3,5)")
    return f" - {'; '.join(hints)}" if hints else ""


def decimal_comma_hint(text: str) -> str:
    """A suffix for errors that a decimal comma ("1.234,56", "3,5") can cause."""
    if re.search(r"\d,\d", text):
        return (" - if a comma was meant as a decimal point, use '.' instead and drop any thousands "
                "separator (1234.56, not 1.234,56)")
    return ""


def parse_expression(
    text: str,
    *,
    allow_symbols: bool = False,
    known_symbols: Optional[dict[str, Symbol]] = None,
    raw_result: Optional[dict] = None,
):
    """Parse `text` through a whitelisted AST walk and return a SymPy object.

    `allow_symbols=True` lets undeclared names become free `Symbol`s (used
    by the `math` engine); otherwise an unknown name is a hard error, which
    is what keeps `calc` numeric-only.

    `raw_result`, if given, is a dict this call may fill with `{"kind": ...}`
    when the whole expression is a single RAW_FUNCTIONS call (isprime,
    nextprime, factorint), since those don't return a SymPy Expr.
    """
    text = text.strip()
    if not text:
        raise UnsafeExpressionError("empty expression")
    text = normalize_single_equals(text)
    try:
        tree = ast.parse(text, mode="eval")
    except SyntaxError as exc:
        raise UnsafeExpressionError(f"could not parse expression: {exc.msg}{_syntax_hint(text)}") from exc

    symbols: dict[str, Symbol] = dict(known_symbols or {})

    def visit(node: ast.AST):
        if not isinstance(node, _ALLOWED_NODES):
            raise UnsafeExpressionError(
                f"disallowed syntax: {type(node).__name__}"
            )
        if isinstance(node, ast.Expression):
            return visit(node.body)
        if isinstance(node, ast.Constant):
            value = node.value
            if isinstance(value, bool) or value is None:
                raise UnsafeExpressionError("booleans/None are not allowed")
            if isinstance(value, (int, float)):
                segment = ast.get_source_segment(text, node) or repr(value)
                segment = segment.strip()
                try:
                    return sympy.Rational(segment) if isinstance(value, float) else sympy.Integer(value)
                except (ValueError, TypeError):
                    return sympy.nsimplify(value, rational=True)
            raise UnsafeExpressionError(f"literal not allowed: {value!r}")
        if isinstance(node, ast.Name):
            if node.id in CONSTANTS:
                return CONSTANTS[node.id]
            if node.id in ALL_FUNCTION_NAMES:
                raise UnsafeExpressionError(f"'{node.id}' must be called, e.g. {node.id}(...)")
            if allow_symbols:
                return symbols.setdefault(node.id, Symbol(node.id))
            raise UnsafeExpressionError(f"unknown identifier: {node.id}")
        if isinstance(node, ast.UnaryOp):
            val = visit(node.operand)
            if isinstance(node.op, ast.USub):
                return -val
            if isinstance(node.op, ast.UAdd):
                return val
            raise UnsafeExpressionError("unsupported unary operator")
        if isinstance(node, ast.BinOp):
            op_type = type(node.op)
            if op_type not in _BINOPS:
                raise UnsafeExpressionError(f"unsupported operator: {op_type.__name__}")
            left = visit(node.left)
            right = visit(node.right)
            if isinstance(left, list) or isinstance(right, list):
                raise UnsafeExpressionError("arithmetic on a list is not supported; use sum(...), mean(...) etc.")
            return _BINOPS[op_type](left, right)
        if isinstance(node, ast.Compare):
            if len(node.ops) != 1 or len(node.comparators) != 1:
                raise UnsafeExpressionError("chained comparisons are not supported")
            op_type = type(node.ops[0])
            if op_type not in _CMPOPS:
                raise UnsafeExpressionError("unsupported comparison")
            left = visit(node.left)
            right = visit(node.comparators[0])
            return _CMPOPS[op_type](left, right)
        if isinstance(node, ast.Call):
            if node.keywords:
                raise UnsafeExpressionError("keyword arguments are not allowed")
            if not isinstance(node.func, ast.Name):
                raise UnsafeExpressionError("only plain function calls are allowed")
            name = node.func.id
            args = []
            for a in node.args:
                if isinstance(a, ast.Starred):
                    raise UnsafeExpressionError("*args is not allowed")
                args.append(visit(a))
            if name in RAW_FUNCTIONS:
                if raw_result is None:
                    raise UnsafeExpressionError(f"'{name}' is not allowed here")
                if node is not tree.body:
                    raise UnsafeExpressionError(
                        f"{name}(...) must be the whole expression, e.g. {name}(97); compute other parts in a separate call"
                    )
                raw_result["kind"] = name
                raw_result["args"] = args
                return sympy.Integer(0)
            if name not in FUNCTIONS:
                raise UnsafeExpressionError(f"unknown function: {name}")
            try:
                return FUNCTIONS[name](*args)
            except UnsafeExpressionError:
                raise
            except TypeError as exc:
                # never leak the private helper's name (e.g. "_pct" for
                # pct()) in the error a person or model sees
                try:
                    params = str(inspect.signature(FUNCTIONS[name]))
                except (TypeError, ValueError):
                    params = ""
                raise UnsafeExpressionError(
                    f"wrong number of arguments for {name}{params}: got {len(args)}{decimal_comma_hint(text)}"
                ) from exc
            except (ValueError, AttributeError) as exc:
                raise UnsafeExpressionError(f"wrong arguments for {name}(): {exc}") from exc
        if isinstance(node, (ast.Tuple, ast.List)):
            return [visit(e) for e in node.elts]
        raise UnsafeExpressionError(f"disallowed syntax: {type(node).__name__}")

    result = visit(tree)
    return result, symbols

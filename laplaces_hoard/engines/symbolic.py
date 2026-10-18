"""Symbolic mathematics: simplify, solve, calculus, series, matrices.

Every operation runs in a separate worker process (see `worker.py`) with a
hard timeout, because SymPy can hang on pathological input. `_execute` is
the module-level, picklable entry point the worker process calls; `run` is
what the API/MCP layer calls from the parent process.
"""
from __future__ import annotations

import re
from typing import Any

import sympy
from sympy import Symbol

from .safe_ast import UnsafeExpressionError, parse_expression
from . import sandbox

__all__ = ["run", "SymbolicError", "OPERATIONS"]

OPERATIONS = (
    "simplify", "expand", "factor", "solve", "nsolve", "diff", "integrate",
    "limit", "series", "summation", "product", "matrix", "dsolve",
    "apart", "together", "inequality",
)


class SymbolicError(ValueError):
    pass


def _parse(text: str, known: dict[str, Symbol]) -> Any:
    result, syms = parse_expression(text, allow_symbols=True, known_symbols=known)
    known.update(syms)
    return result


def _as_equation(expr: Any) -> tuple[Any, Any]:
    """If `expr` is a SymPy Relational (from `==`), return (lhs, rhs); else (expr, 0)."""
    if isinstance(expr, sympy.core.relational.Relational):
        return expr.lhs, expr.rhs
    return expr, sympy.Integer(0)


MAX_RESULT_CHARS = 1500
MAX_SOLUTIONS = 20


def _var(payload: dict, known: dict[str, Symbol], op: str) -> Symbol:
    """The variable to act on: the one given, or the only free symbol there is."""
    name = payload.get("variable")
    if name:
        name = str(name).strip()
        return known.get(name) or Symbol(name)
    free = [v for k, v in known.items()]
    if len(free) == 1:
        return free[0]
    if not free:
        raise SymbolicError(f"{op} needs a variable, and the expression has none")
    names = ", ".join(sorted(known))
    raise SymbolicError(f"{op} needs 'variable': the expression has several symbols ({names})")


def _s(expr) -> str:
    text = sympy.sstr(expr)
    return text if len(text) <= MAX_RESULT_CHARS else text[:MAX_RESULT_CHARS] + "…"


def _numeric(v) -> Any:
    """A float when the value is real (tiny imaginary round-off dropped), else a string."""
    try:
        n = sympy.N(v)
        if not n.is_number:
            return None
        re_, im_ = n.as_real_imag()
        re_f, im_f = float(re_), float(im_)
        if abs(im_f) <= 1e-12 * max(1.0, abs(re_f)):
            return re_f
        return f"{re_f:.15g} {'+' if im_f >= 0 else '-'} {abs(im_f):.15g}*I"
    except (TypeError, ValueError):
        return None


def _residual_ok(lhs, rhs, subs: dict) -> bool:
    try:
        diff = sympy.simplify((lhs - rhs).subs(subs))
        if diff == 0:
            return True
        val = sympy.N(diff)
        return bool(val.is_number and abs(complex(val)) < 1e-8)
    except Exception:  # noqa: BLE001
        return False


def _op_simplify(payload: dict) -> dict:
    known: dict = {}
    expr = _parse(payload["expression"], known)
    result = sympy.simplify(expr)
    return {"result": _s(result), "latex": sympy.latex(result)}


def _op_expand(payload: dict) -> dict:
    known: dict = {}
    expr = _parse(payload["expression"], known)
    result = sympy.expand(expr)
    return {"result": _s(result), "latex": sympy.latex(result)}


def _op_factor(payload: dict) -> dict:
    known: dict = {}
    expr = _parse(payload["expression"], known)
    result = sympy.factor(expr)
    return {"result": _s(result), "latex": sympy.latex(result)}


def _op_apart(payload: dict) -> dict:
    known: dict = {}
    expr = _parse(payload["expression"], known)
    var = known.get(payload.get("variable")) if payload.get("variable") else None
    result = sympy.apart(expr, var) if var is not None else sympy.apart(expr)
    return {"result": _s(result), "latex": sympy.latex(result)}


def _op_together(payload: dict) -> dict:
    known: dict = {}
    expr = _parse(payload["expression"], known)
    result = sympy.together(expr)
    return {"result": _s(result), "latex": sympy.latex(result)}


def _op_solve(payload: dict) -> dict:
    known: dict = {}
    exprs = payload["expressions"]
    eqs = [_parse(e, known) for e in exprs]
    lhs_rhs = [_as_equation(e) for e in eqs]
    sympy_eqs = [sympy.Eq(l, r) for l, r in lhs_rhs]

    variables = payload.get("variables") or list(known.keys())
    syms = [known[v] for v in variables if v in known]
    if not syms:
        syms = list(known.values())
    if not syms:
        raise SymbolicError("no variable to solve for")

    domain = payload.get("domain", "real")

    solutions = sympy.solve(sympy_eqs, syms, dict=True)
    if domain == "real":
        filtered = []
        for sol in solutions:
            ok = True
            for v in sol.values():
                if v.is_number and v.has(sympy.I) and not isinstance(_numeric(v), float):
                    ok = False
                    break
            if ok:
                filtered.append(sol)
        solutions = filtered
    more = len(solutions) > MAX_SOLUTIONS
    solutions = solutions[:MAX_SOLUTIONS]

    results = []
    all_verified = True
    for sol in solutions:
        subs = {k: v for k, v in sol.items()}
        checks = [_residual_ok(l, r, subs) for l, r in lhs_rhs]
        verified = all(checks)
        all_verified = all_verified and verified
        results.append(
            {
                "values": {str(k): _s(v) for k, v in sol.items()},
                "numeric": {str(k): _numeric(v) for k, v in sol.items() if v.is_number},
                "verified": verified,
            }
        )
    if not solutions:
        all_verified = False

    return {
        "result": results,
        "variables": [str(s) for s in syms],
        "verified": all_verified if results else None,
        "count": len(results),
        "has_more": more,
        "domain": domain,
        "steps_hint": "Solved with SymPy solve(); each solution is substituted back "
        "into the original equation(s) to compute 'verified'.",
    }


def _op_nsolve(payload: dict) -> dict:
    known: dict = {}
    expr = _parse(payload["expression"], known)
    lhs, rhs = _as_equation(expr)
    var = _var(payload, known, "nsolve")
    x0 = float(payload.get("x0", 0))
    try:
        root = sympy.nsolve(lhs - rhs, var, x0)
    except Exception as exc:  # noqa: BLE001
        raise SymbolicError(f"nsolve did not converge from x0={x0}: {exc}") from exc
    return {
        "result": _s(root),
        "numeric": float(root),
        "latex": sympy.latex(root),
        "variable": str(var),
    }


def _op_diff(payload: dict) -> dict:
    known: dict = {}
    expr = _parse(payload["expression"], known)
    var = _var(payload, known, "diff")
    order = int(payload.get("order", 1))
    result = sympy.diff(expr, var, order)
    return {"result": _s(result), "latex": sympy.latex(result)}


def _op_integrate(payload: dict) -> dict:
    known: dict = {}
    expr = _parse(payload["expression"], known)
    var = _var(payload, known, "integrate")
    lower = payload.get("lower")
    upper = payload.get("upper")
    if lower is not None and upper is not None:
        lower_v = _parse(str(lower), known)
        upper_v = _parse(str(upper), known)
        result = sympy.integrate(expr, (var, lower_v, upper_v))
        numeric = None
        try:
            numeric = float(sympy.N(result)) if result.is_number else None
        except Exception:  # noqa: BLE001
            numeric = None
        return {"result": _s(result), "latex": sympy.latex(result), "numeric": numeric, "definite": True}
    result = sympy.integrate(expr, var)
    return {"result": _s(result) + " + C", "latex": sympy.latex(result) + " + C", "definite": False}


def _op_limit(payload: dict) -> dict:
    known: dict = {}
    expr = _parse(payload["expression"], known)
    var = _var(payload, known, "limit")
    point = _parse(str(payload["point"]), known)
    direction = payload.get("direction", "+-")
    dir_map = {"+": "+", "-": "-", "+-": "+-"}
    d = dir_map.get(direction, "+-")
    if d == "+-":
        result = sympy.limit(expr, var, point)
    else:
        result = sympy.limit(expr, var, point, dir=d)
    return {"result": _s(result), "latex": sympy.latex(result)}


def _op_series(payload: dict) -> dict:
    known: dict = {}
    expr = _parse(payload["expression"], known)
    var = _var(payload, known, "series")
    point = _parse(str(payload.get("point", 0)), known)
    order = int(payload.get("order", 6))
    result = sympy.series(expr, var, point, order).removeO()
    full = sympy.series(expr, var, point, order)
    return {"result": _s(full), "latex": sympy.latex(full), "polynomial": _s(result)}


def _op_summation(payload: dict) -> dict:
    known: dict = {}
    expr = _parse(payload["expression"], known)
    var = _var(payload, known, "summation")
    lower = _parse(str(payload["lower"]), known)
    upper = _parse(str(payload["upper"]), known)
    result = sympy.summation(expr, (var, lower, upper))
    numeric = None
    try:
        numeric = float(sympy.N(result)) if result.is_number else None
    except Exception:  # noqa: BLE001
        pass
    return {"result": _s(result), "latex": sympy.latex(result), "numeric": numeric}


def _op_product(payload: dict) -> dict:
    known: dict = {}
    expr = _parse(payload["expression"], known)
    var = _var(payload, known, "product")
    lower = _parse(str(payload["lower"]), known)
    upper = _parse(str(payload["upper"]), known)
    result = sympy.product(expr, (var, lower, upper))
    numeric = None
    try:
        numeric = float(sympy.N(result)) if result.is_number else None
    except Exception:  # noqa: BLE001
        pass
    return {"result": _s(result), "latex": sympy.latex(result), "numeric": numeric}


def _parse_matrix(rows: list[list[str]], known: dict) -> sympy.Matrix:
    parsed = [[_parse(str(cell), known) for cell in row] for row in rows]
    return sympy.Matrix(parsed)


def _op_matrix(payload: dict) -> dict:
    known: dict = {}
    op = payload["matrix_op"]
    mat = _parse_matrix(payload["matrix"], known)
    if op == "det":
        result = mat.det()
        return {"result": _s(result), "latex": sympy.latex(result)}
    if op == "inv":
        try:
            result = mat.inv()
        except sympy.matrices.exceptions.NonInvertibleMatrixError as exc:
            raise SymbolicError(f"matrix is not invertible: {exc}") from exc
        return {"result": _s(result), "latex": sympy.latex(result)}
    if op == "rank":
        return {"result": str(mat.rank())}
    if op == "rref":
        r, pivots = mat.rref()
        return {"result": _s(r), "latex": sympy.latex(r), "pivots": list(pivots)}
    if op == "eigenvals":
        vals = mat.eigenvals()
        return {
            "result": {sympy.sstr(k): int(v) for k, v in vals.items()},
            "numeric": {sympy.sstr(k): _numeric(k) for k in vals},
            "note": "keys are eigenvalues, values their algebraic multiplicity",
        }
    if op == "transpose":
        result = mat.T
        return {"result": _s(result), "latex": sympy.latex(result)}
    if op == "multiply":
        mat2 = _parse_matrix(payload["matrix2"], known)
        try:
            result = mat * mat2
        except sympy.ShapeError as exc:
            raise SymbolicError(f"incompatible matrix shapes: {exc}") from exc
        return {"result": _s(result), "latex": sympy.latex(result)}
    raise SymbolicError(f"unknown matrix operation: {op}")


def _op_dsolve(payload: dict) -> dict:
    """First-order ODE dy/dx = f(x, y).

    Convention (documented boundary): `expression` is the right-hand side of
    `dy/dx = expression`, written in ordinary symbols `x` (independent) and
    `y` (dependent) — not SymPy's `y(x)`/`Derivative` syntax, which the
    safe-AST whitelist deliberately does not expose. This covers the
    separable/linear first-order equations SymPy solves well without
    widening the parser to arbitrary function application.
    """
    known: dict = {}
    x = Symbol(payload.get("variable", "x"))
    y_name = payload.get("function", "y")
    known[str(x)] = x
    known[y_name] = Symbol(y_name)
    rhs = _parse(payload["expression"], known)
    x_sym = known[str(x)]
    y_plain = known[y_name]
    y_func = sympy.Function(y_name)(x_sym)
    rhs_f = rhs.subs(y_plain, y_func)
    eq = sympy.Eq(sympy.Derivative(y_func, x_sym), rhs_f)
    try:
        sol = sympy.dsolve(eq, y_func)
    except Exception as exc:  # noqa: BLE001
        raise SymbolicError(f"could not solve this ODE: {exc}") from exc
    return {"result": _s(sol), "latex": sympy.latex(sol)}


def _op_inequality(payload: dict) -> dict:
    known: dict = {}
    expr = _parse(payload["expression"], known)
    var = _var(payload, known, "inequality")
    if not isinstance(expr, sympy.core.relational.Relational):
        raise SymbolicError("expression must contain <, >, <=, >= or ==")
    result = sympy.solve_univariate_inequality(expr, var, relational=False)
    return {"result": _s(result), "latex": sympy.latex(result)}


_DISPATCH = {
    "simplify": _op_simplify,
    "expand": _op_expand,
    "factor": _op_factor,
    "solve": _op_solve,
    "nsolve": _op_nsolve,
    "diff": _op_diff,
    "integrate": _op_integrate,
    "limit": _op_limit,
    "series": _op_series,
    "summation": _op_summation,
    "product": _op_product,
    "matrix": _op_matrix,
    "dsolve": _op_dsolve,
    "apart": _op_apart,
    "together": _op_together,
    "inequality": _op_inequality,
}


def _execute(op: str, payload: dict) -> dict:
    """Top-level, picklable entry point run inside the worker process."""
    fn = _DISPATCH.get(op)
    if fn is None:
        raise SymbolicError(f"unknown operation: {op}")
    try:
        return fn(payload)
    except (UnsafeExpressionError, SymbolicError):
        raise
    except sympy.SympifyError as exc:
        raise SymbolicError(str(exc)) from exc
    except Exception as exc:  # noqa: BLE001
        raise SymbolicError(f"{type(exc).__name__}: {exc}") from exc


_CELL_OPS = ("simplify", "expand", "factor", "apart", "together", "solve", "diff", "integrate", "limit", "series")


def _split_args(text: str) -> list[str]:
    parts, depth, cur = [], 0, []
    for ch in text:
        if ch in "([":
            depth += 1
        elif ch in ")]":
            depth -= 1
        if ch == "," and depth == 0:
            parts.append("".join(cur).strip())
            cur = []
        else:
            cur.append(ch)
    parts.append("".join(cur).strip())
    return [p for p in parts if p]


def _cell_int(text: str, usage: str) -> int:
    try:
        return int(text.strip())
    except ValueError as exc:
        raise SymbolicError(f"{usage}: the order must be a whole number, got {text!r}") from exc


def parse_cell(text: str) -> tuple[str, dict[str, Any]]:
    """Notebook shorthand for the math engine, e.g. `factor(x**3 - x)`,
    `diff(sin(x)*x, x)`, `integrate(x**2, x, 0, 2)`, `x**2 = 4` (solve) or a
    bare expression (simplify). Returns (operation, payload)."""
    text = text.strip()
    m = re.match(r"^([a-z]+)\s*\((.*)\)$", text, flags=re.S)
    if m and m.group(1) in _CELL_OPS and _wraps_whole(text, m.start(2) - 1) and _split_args(m.group(2)):
        op, args = m.group(1), _split_args(m.group(2))
        if op == "solve":
            payload: dict[str, Any] = {"expressions": [args[0]]}
            if len(args) > 1:
                payload["variables"] = args[1:]
            return op, payload
        payload = {"expression": args[0]}
        if op in ("simplify", "expand", "factor", "together"):
            return op, payload
        if op == "apart":
            if len(args) > 1:
                payload["variable"] = args[1]
            return op, payload
        if len(args) > 1:
            payload["variable"] = args[1]
        if op == "diff" and len(args) > 2:
            payload["order"] = _cell_int(args[2], "diff(expr, var, order)")
        if op == "integrate" and len(args) > 3:
            payload["lower"], payload["upper"] = args[2], args[3]
        if op == "limit":
            payload["point"] = args[2] if len(args) > 2 else "0"
        if op == "series":
            if len(args) > 2:
                payload["point"] = args[2]
            if len(args) > 3:
                payload["order"] = _cell_int(args[3], "series(expr, var, point, order)")
        return op, payload
    if re.search(r"<|>", text):
        return "inequality", {"expression": text}
    if text.replace("==", "=").replace("!=", "").count("=") == 1:
        return "solve", {"expressions": [text]}
    return "simplify", {"expression": text}


def _wraps_whole(text: str, open_idx: int) -> bool:
    """True when the '(' at open_idx is closed by the final ')' of text."""
    depth = 0
    for i in range(open_idx, len(text)):
        if text[i] == "(":
            depth += 1
        elif text[i] == ")":
            depth -= 1
            if depth == 0:
                return i == len(text) - 1
    return False


def run(operation: str, timeout: float = 10.0, **payload: Any) -> dict[str, Any]:
    """Run one symbolic operation in the shared timeout worker (see `sandbox.py`)."""
    if operation not in OPERATIONS:
        raise SymbolicError(f"unknown operation: {operation}; choose one of {', '.join(OPERATIONS)}")
    return sandbox.run(f"math.{operation}", payload, error_cls=SymbolicError, timeout=timeout)


def shutdown() -> None:
    sandbox.shutdown()

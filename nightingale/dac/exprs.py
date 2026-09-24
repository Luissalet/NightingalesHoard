"""A tiny, safe expression language for dashboard-spec `if` conditions and
`{{ }}` templates. No `eval`/`exec`: `safe_eval` walks a restricted `ast`
tree and only ever compares/combines plain Python values it looked up
itself, so a dashboard's YAML can never run arbitrary code.

Conditions: `==`, `!=`, `in`, `not in`, `and`, `or`, `not`, string/number/
bool/list literals, and dotted names resolved against the render context
(`filters.region`, `each`).
"""

from __future__ import annotations

import ast
import re
from typing import Any

__all__ = ["ExprError", "safe_eval", "render_template", "template_names"]

_TEMPLATE_RE = re.compile(r"\{\{\s*([a-zA-Z0-9_.]+)\s*\}\}")
_ALLOWED_COMPARE = (ast.Eq, ast.NotEq, ast.In, ast.NotIn)


class ExprError(ValueError):
    pass


def _lookup(name: str, context: dict[str, Any]) -> Any:
    parts = name.split(".")
    value: Any = context
    for i, part in enumerate(parts):
        if isinstance(value, dict):
            if part not in value:
                raise ExprError(f"unknown variable: {name!r}")
            value = value[part]
        else:
            raise ExprError(f"unknown variable: {name!r}")
    return value


def _name_of(node: ast.AST) -> str:
    if isinstance(node, ast.Name):
        return node.id
    if isinstance(node, ast.Attribute):
        return f"{_name_of(node.value)}.{node.attr}"
    raise ExprError("only plain names like 'filters.region' or 'each' are allowed here")


def _eval(node: ast.AST, context: dict[str, Any]) -> Any:
    if isinstance(node, ast.Expression):
        return _eval(node.body, context)
    if isinstance(node, ast.Constant):
        return node.value
    if isinstance(node, (ast.List, ast.Tuple)):
        return [_eval(el, context) for el in node.elts]
    if isinstance(node, (ast.Name, ast.Attribute)):
        return _lookup(_name_of(node), context)
    if isinstance(node, ast.BoolOp):
        values = [_eval(v, context) for v in node.values]
        return all(values) if isinstance(node.op, ast.And) else any(values)
    if isinstance(node, ast.UnaryOp) and isinstance(node.op, ast.Not):
        return not _eval(node.operand, context)
    if isinstance(node, ast.Compare):
        left = _eval(node.left, context)
        for op, comparator in zip(node.ops, node.comparators):
            right = _eval(comparator, context)
            if not isinstance(op, _ALLOWED_COMPARE):
                raise ExprError(f"operator not allowed: {type(op).__name__}")
            if isinstance(op, ast.Eq):
                ok = left == right
            elif isinstance(op, ast.NotEq):
                ok = left != right
            elif isinstance(op, ast.In):
                ok = left in right
            else:
                ok = left not in right
            if not ok:
                return False
            left = right
        return True
    raise ExprError(f"expression not allowed: {type(node).__name__}")


def safe_eval(expr: str, context: dict[str, Any]) -> bool:
    try:
        tree = ast.parse(expr, mode="eval")
    except SyntaxError as exc:
        raise ExprError(f"bad expression syntax: {exc.msg}") from exc
    return bool(_eval(tree, context))


def template_names(text: str) -> list[str]:
    return _TEMPLATE_RE.findall(text or "")


def render_template(text: str, context: dict[str, Any]) -> str:
    if not isinstance(text, str) or "{{" not in text:
        return text

    def repl(m: re.Match) -> str:
        value = _lookup(m.group(1), context)
        return "" if value is None else str(value)

    return _TEMPLATE_RE.sub(repl, text)

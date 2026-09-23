"""Quality rules: declarative checks compiled to a "failing rows" SQL query
(or a scalar check for row_count) and run against a dataset's current
version. Pure SQL-building, no FastAPI, easy to unit test.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

__all__ = ["QualityError", "RULE_KINDS", "build_check", "RuleCheck"]


def q(name: str) -> str:
    return '"' + str(name).replace('"', '""') + '"'


def lit(value: Any) -> str:
    if value is None:
        return "NULL"
    if isinstance(value, bool):
        return "TRUE" if value else "FALSE"
    if isinstance(value, (int, float)):
        return repr(value)
    return "'" + str(value).replace("'", "''") + "'"


class QualityError(ValueError):
    pass


@dataclass
class RuleCheck:
    """A rule compiled to SQL: either a `failing_sql` (rows that violate the
    rule) or a `scalar_sql` (single-row aggregate check, for row_count)."""

    failing_sql: str | None = None
    scalar_sql: str | None = None
    scalar_ok: Any = None  # callable(value) -> bool, only used with scalar_sql


def _require(p: dict, key: str) -> Any:
    if key not in p or p[key] in (None, ""):
        raise QualityError(f"'{key}' is required")
    return p[key]


def _col_list(p: dict) -> list[str]:
    """Every rule kind names the column(s) it checks — `unique` naturally
    takes several, the rest just one — but callers reach for either word
    regardless (a single-column `unique` rule reads just as naturally as
    "column": "pedido" as "columns": ["pedido"]). `columns` is canonical;
    `column` is accepted the same way, and either may be a single name or a
    list, so every rule kind takes both uniformly."""
    raw = p.get("columns", p.get("column"))
    if raw in (None, "", []):
        raise QualityError("'columns' (or 'column') is required")
    cols = [str(c) for c in raw] if isinstance(raw, (list, tuple)) else [str(raw)]
    cols = [c for c in cols if c]
    if not cols:
        raise QualityError("'columns' (or 'column') is required")
    return cols


def _one_col(p: dict) -> str:
    cols = _col_list(p)
    if len(cols) != 1:
        raise QualityError("this rule checks exactly one column; got more than one in 'column'/'columns'")
    return cols[0]


def _not_null(table: str, p: dict) -> RuleCheck:
    col = q(_one_col(p))
    return RuleCheck(failing_sql=f"SELECT * FROM {q(table)} WHERE {col} IS NULL")


def _unique(table: str, p: dict) -> RuleCheck:
    cols = _col_list(p)
    part = ", ".join(q(c) for c in cols)
    return RuleCheck(
        failing_sql=(
            f"WITH __c AS (SELECT {part}, COUNT(*) AS __n FROM {q(table)} GROUP BY {part} HAVING COUNT(*) > 1) "
            f"SELECT t.* FROM {q(table)} t JOIN __c USING ({part})"
        )
    )


def _accepted_values(table: str, p: dict) -> RuleCheck:
    col = q(_one_col(p))
    values = _require(p, "values")
    in_list = ", ".join(lit(v) for v in values)
    return RuleCheck(failing_sql=f"SELECT * FROM {q(table)} WHERE {col} IS NOT NULL AND {col} NOT IN ({in_list})")


def _range(table: str, p: dict) -> RuleCheck:
    col = q(_one_col(p))
    conds = []
    if p.get("min") is not None:
        conds.append(f"{col} < {lit(p['min'])}")
    if p.get("max") is not None:
        conds.append(f"{col} > {lit(p['max'])}")
    if not conds:
        raise QualityError("range needs 'min' and/or 'max'")
    return RuleCheck(failing_sql=f"SELECT * FROM {q(table)} WHERE {col} IS NOT NULL AND ({' OR '.join(conds)})")


def _regex(table: str, p: dict) -> RuleCheck:
    col = q(_one_col(p))
    pattern = _require(p, "pattern")
    return RuleCheck(
        failing_sql=f"SELECT * FROM {q(table)} WHERE {col} IS NOT NULL AND NOT regexp_matches({col}, {lit(pattern)})"
    )


def _row_count(table: str, p: dict) -> RuleCheck:
    lo, hi = p.get("min"), p.get("max")
    if lo is None and hi is None:
        raise QualityError("row_count needs 'min' and/or 'max'")

    def ok(n: int) -> bool:
        if lo is not None and n < lo:
            return False
        if hi is not None and n > hi:
            return False
        return True

    return RuleCheck(scalar_sql=f"SELECT COUNT(*) FROM {q(table)}", scalar_ok=ok)


def _freshness(table: str, p: dict) -> RuleCheck:
    col = q(_one_col(p))
    max_age_days = _require(p, "max_age_days")
    return RuleCheck(
        scalar_sql=f"SELECT DATE_DIFF('day', MAX({col}), CURRENT_DATE) FROM {q(table)}",
        scalar_ok=lambda n: n is not None and n <= max_age_days,
    )


def _referential(table: str, p: dict) -> RuleCheck:
    col_name = _one_col(p)
    col = q(col_name)
    ref_table = _require(p, "ref_table")
    ref_col = q(p.get("ref_column") or col_name)
    return RuleCheck(
        failing_sql=(
            f"SELECT t.* FROM {q(table)} t WHERE t.{col} IS NOT NULL "
            f"AND NOT EXISTS (SELECT 1 FROM {q(ref_table)} r WHERE r.{ref_col} = t.{col})"
        )
    )


def _custom_sql(table: str, p: dict) -> RuleCheck:
    from ..sqlgate import gate_sql

    sql = _require(p, "sql").replace("__table__", q(table))
    gate_sql(p["sql"])
    return RuleCheck(failing_sql=sql)


RULE_BUILDERS = {
    "not_null": _not_null,
    "unique": _unique,
    "accepted_values": _accepted_values,
    "range": _range,
    "regex": _regex,
    "row_count": _row_count,
    "freshness": _freshness,
    "referential": _referential,
    "custom_sql": _custom_sql,
}
RULE_KINDS = tuple(RULE_BUILDERS)


def build_check(kind: str, table: str, params: dict) -> RuleCheck:
    builder = RULE_BUILDERS.get(kind)
    if not builder:
        raise QualityError(f"unknown rule kind: {kind}; choose from {RULE_KINDS}")
    return builder(table, dict(params or {}))

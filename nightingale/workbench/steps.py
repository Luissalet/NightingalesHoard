"""Transform step definitions: each step turns (previous columns, params) into
a single read-only SELECT statement over `__prev__` (the previous version's
table) and, for `join`/`union`, a second dataset's current table.

Every step is deterministic SQL; applying it is always `CREATE TABLE
new_version AS <select_sql>` (see `engine.py`). Keeping this as pure string
building (no FastAPI, no DuckDB connection) makes each step independently
testable and makes the exported recipe a readable SQL script: the same
`select_sql` this module returns is what `recipe export` prints.

The design (explicit column lists rather than DuckDB's `* EXCLUDE`/`* RENAME`
shorthands) is intentional: it reads the same in every DuckDB version and in
the exported recipe script.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Callable

__all__ = ["StepError", "STEP_BUILDERS", "build_step_sql", "STEP_KINDS"]


class StepError(ValueError):
    pass


def q(name: str) -> str:
    """Quote a SQL identifier."""
    return '"' + str(name).replace('"', '""') + '"'


def lit(value: Any) -> str:
    """A SQL string literal."""
    if value is None:
        return "NULL"
    if isinstance(value, bool):
        return "TRUE" if value else "FALSE"
    if isinstance(value, (int, float)):
        return repr(value)
    return "'" + str(value).replace("'", "''") + "'"


def _require(params: dict, key: str) -> Any:
    if key not in params or params[key] in (None, ""):
        raise StepError(f"'{key}' is required")
    return params[key]


def _cols_except(columns: list[str], names: list[str]) -> list[str]:
    drop = {n for n in names}
    missing = [n for n in names if n not in columns]
    if missing:
        raise StepError(f"unknown column(s): {missing}; available: {columns}")
    return [c for c in columns if c not in drop]


@dataclass
class StepContext:
    """Everything a step builder needs beyond its own params."""

    prev_table: str
    columns: list[str]
    resolve_dataset_table: Callable[[str], str]  # dataset name -> its current version's table name


# ---------------------------------------------------------------------------
# Step builders. Each returns the SELECT statement (no trailing semicolon).
# ---------------------------------------------------------------------------


def _filter(ctx: StepContext, p: dict) -> str:
    expr = _require(p, "expr")
    return f"SELECT * FROM {q(ctx.prev_table)} WHERE ({expr})"


def _select(ctx: StepContext, p: dict) -> str:
    cols = _require(p, "columns")
    missing = [c for c in cols if c not in ctx.columns]
    if missing:
        raise StepError(f"unknown column(s): {missing}; available: {ctx.columns}")
    return f"SELECT {', '.join(q(c) for c in cols)} FROM {q(ctx.prev_table)}"


def _drop(ctx: StepContext, p: dict) -> str:
    cols = _require(p, "columns")
    keep = _cols_except(ctx.columns, cols)
    if not keep:
        raise StepError("dropping all columns would leave an empty dataset")
    return f"SELECT {', '.join(q(c) for c in keep)} FROM {q(ctx.prev_table)}"


def _rename(ctx: StepContext, p: dict) -> str:
    mapping: dict = _require(p, "mapping")
    missing = [c for c in mapping if c not in ctx.columns]
    if missing:
        raise StepError(f"unknown column(s): {missing}; available: {ctx.columns}")
    parts = []
    for c in ctx.columns:
        new_name = mapping.get(c)
        parts.append(f"{q(c)} AS {q(new_name)}" if new_name else q(c))
    return f"SELECT {', '.join(parts)} FROM {q(ctx.prev_table)}"


_CAST_TYPES = {"integer": "BIGINT", "double": "DOUBLE", "varchar": "VARCHAR", "text": "VARCHAR",
               "date": "DATE", "timestamp": "TIMESTAMP", "boolean": "BOOLEAN", "decimal": "DECIMAL(18,4)"}


def _cast(ctx: StepContext, p: dict) -> str:
    col = _require(p, "column")
    to = str(_require(p, "to")).lower()
    if col not in ctx.columns:
        raise StepError(f"unknown column: {col}")
    sql_type = _CAST_TYPES.get(to)
    if not sql_type:
        raise StepError(f"unsupported target type: {to}; choose one of {sorted(_CAST_TYPES)}")
    spanish = bool(p.get("spanish_number"))
    date_format = p.get("date_format")
    if spanish and sql_type in ("BIGINT", "DOUBLE", "DECIMAL(18,4)"):
        norm = f"REPLACE(REPLACE(TRIM({q(col)}), '.', ''), ',', '.')"
        expr = f"TRY_CAST(NULLIF({norm}, '') AS {sql_type})"
    elif date_format and sql_type in ("DATE", "TIMESTAMP"):
        expr = f"TRY_STRPTIME({q(col)}, {lit(date_format)})"
        if sql_type == "DATE":
            expr = f"CAST({expr} AS DATE)"
    else:
        expr = f"TRY_CAST({q(col)} AS {sql_type})"
    parts = [f"{expr} AS {q(col)}" if c == col else q(c) for c in ctx.columns]
    return f"SELECT {', '.join(parts)} FROM {q(ctx.prev_table)}"


def _fill_null(ctx: StepContext, p: dict) -> str:
    col = _require(p, "column")
    strategy = p.get("strategy", "value")
    if col not in ctx.columns:
        raise StepError(f"unknown column: {col}")
    if strategy == "value":
        fill = lit(_require(p, "value"))
        expr = f"COALESCE({q(col)}, {fill})"
        parts = [f"{expr} AS {q(col)}" if c == col else q(c) for c in ctx.columns]
        return f"SELECT {', '.join(parts)} FROM {q(ctx.prev_table)}"
    if strategy == "mean":
        expr = f"COALESCE({q(col)}, (SELECT AVG({q(col)}) FROM {q(ctx.prev_table)}))"
    elif strategy == "median":
        expr = f"COALESCE({q(col)}, (SELECT MEDIAN({q(col)}) FROM {q(ctx.prev_table)}))"
    elif strategy == "mode":
        expr = f"COALESCE({q(col)}, (SELECT MODE({q(col)}) FROM {q(ctx.prev_table)}))"
    elif strategy == "forward":
        parts = [f"LAST_VALUE({q(col)} IGNORE NULLS) OVER (ORDER BY __rn) AS {q(col)}" if c == col else q(c)
                  for c in ctx.columns]
        return (
            f"WITH __base AS (SELECT *, row_number() OVER () AS __rn FROM {q(ctx.prev_table)}) "
            f"SELECT {', '.join(parts)} FROM __base"
        )
    else:
        raise StepError(f"unknown fill strategy: {strategy}; choose value/mean/median/mode/forward")
    parts = [f"{expr} AS {q(col)}" if c == col else q(c) for c in ctx.columns]
    return f"SELECT {', '.join(parts)} FROM {q(ctx.prev_table)}"


def _drop_duplicates(ctx: StepContext, p: dict) -> str:
    subset = p.get("subset") or ctx.columns
    missing = [c for c in subset if c not in ctx.columns]
    if missing:
        raise StepError(f"unknown column(s): {missing}")
    part_by = ", ".join(q(c) for c in subset)
    cols = ", ".join(q(c) for c in ctx.columns)
    return (
        f"WITH __base AS (SELECT *, row_number() OVER (PARTITION BY {part_by} ORDER BY {part_by}) AS __rn "
        f"FROM {q(ctx.prev_table)}) SELECT {cols} FROM __base WHERE __rn = 1"
    )


def _derive(ctx: StepContext, p: dict) -> str:
    name = _require(p, "name")
    expr = _require(p, "expr")
    if name in ctx.columns:
        parts = [f"({expr}) AS {q(name)}" if c == name else q(c) for c in ctx.columns]
        return f"SELECT {', '.join(parts)} FROM {q(ctx.prev_table)}"
    return f"SELECT *, ({expr}) AS {q(name)} FROM {q(ctx.prev_table)}"


def _split_column(ctx: StepContext, p: dict) -> str:
    col = _require(p, "column")
    delimiter = p.get("delimiter", ",")
    into = _require(p, "into")
    if col not in ctx.columns:
        raise StepError(f"unknown column: {col}")
    extras = ", ".join(f"NULLIF(split_part({q(col)}, {lit(delimiter)}, {i + 1}), '') AS {q(name)}"
                        for i, name in enumerate(into))
    return f"SELECT *, {extras} FROM {q(ctx.prev_table)}"


_TEXT_OPS = {
    "trim": lambda c: f"TRIM({c})",
    "upper": lambda c: f"UPPER({c})",
    "lower": lambda c: f"LOWER({c})",
    "title": lambda c: f"INITCAP({c})",
    "strip_accents": lambda c: f"strip_accents({c})",
    "collapse_spaces": lambda c: f"regexp_replace(TRIM({c}), '\\s+', ' ', 'g')",
}


def _text(ctx: StepContext, p: dict) -> str:
    col = _require(p, "column")
    op = _require(p, "op")
    if col not in ctx.columns:
        raise StepError(f"unknown column: {col}")
    fn = _TEXT_OPS.get(op)
    if not fn:
        raise StepError(f"unknown text op: {op}; choose one of {sorted(_TEXT_OPS)}")
    target = p.get("new_column") or col
    expr = fn(q(col))
    if target == col:
        parts = [f"{expr} AS {q(col)}" if c == col else q(c) for c in ctx.columns]
        return f"SELECT {', '.join(parts)} FROM {q(ctx.prev_table)}"
    return f"SELECT *, {expr} AS {q(target)} FROM {q(ctx.prev_table)}"


def _replace(ctx: StepContext, p: dict) -> str:
    col = _require(p, "column")
    pattern = _require(p, "pattern")
    replacement = p.get("replacement", "")
    regex = bool(p.get("regex"))
    if col not in ctx.columns:
        raise StepError(f"unknown column: {col}")
    expr = f"regexp_replace({q(col)}, {lit(pattern)}, {lit(replacement)}, 'g')" if regex \
        else f"REPLACE({q(col)}, {lit(pattern)}, {lit(replacement)})"
    parts = [f"{expr} AS {q(col)}" if c == col else q(c) for c in ctx.columns]
    return f"SELECT {', '.join(parts)} FROM {q(ctx.prev_table)}"


def _bin(ctx: StepContext, p: dict) -> str:
    col = _require(p, "column")
    new_col = p.get("new_column") or f"{col}_bin"
    if col not in ctx.columns:
        raise StepError(f"unknown column: {col}")
    edges = p.get("edges")
    labels = p.get("labels")
    if edges:
        cases = []
        for i in range(len(edges) - 1):
            lo, hi = edges[i], edges[i + 1]
            label = labels[i] if labels and i < len(labels) else f"[{lo}, {hi})"
            cases.append(f"WHEN {q(col)} >= {lit(lo)} AND {q(col)} < {lit(hi)} THEN {lit(label)}")
        expr = f"CASE {' '.join(cases)} ELSE NULL END"
    else:
        n_bins = int(p.get("bins", 5))
        expr = (
            f"CASE WHEN {q(col)} IS NULL THEN NULL ELSE "
            f"LEAST({n_bins - 1}, FLOOR(({q(col)} - m.lo) / NULLIF(m.hi - m.lo, 0) * {n_bins}))::INT END"
        )
        return (
            f"SELECT t.*, {expr} AS {q(new_col)} FROM {q(ctx.prev_table)} t, "
            f"(SELECT MIN({q(col)}) AS lo, MAX({q(col)}) AS hi FROM {q(ctx.prev_table)}) m"
        )
    return f"SELECT *, {expr} AS {q(new_col)} FROM {q(ctx.prev_table)}"


_DATE_PARTS = {"year", "month", "day", "dow", "quarter", "week", "hour", "minute"}


def _date_parts(ctx: StepContext, p: dict) -> str:
    col = _require(p, "column")
    parts = _require(p, "parts")
    if col not in ctx.columns:
        raise StepError(f"unknown column: {col}")
    bad = [x for x in parts if x not in _DATE_PARTS]
    if bad:
        raise StepError(f"unknown date part(s): {bad}; choose from {sorted(_DATE_PARTS)}")
    extras = ", ".join(f"EXTRACT({part} FROM {q(col)}) AS {q(col + '_' + part)}" for part in parts)
    return f"SELECT *, {extras} FROM {q(ctx.prev_table)}"


_AGG_FNS = {"sum", "avg", "min", "max", "count", "count_distinct", "median", "stddev"}


def _group(ctx: StepContext, p: dict) -> str:
    group_by = _require(p, "group_by")
    aggs = _require(p, "aggregations")
    missing = [c for c in group_by if c not in ctx.columns]
    if missing:
        raise StepError(f"unknown group_by column(s): {missing}")
    select_parts = [q(c) for c in group_by]
    for agg in aggs:
        col = agg.get("column")
        fn = agg.get("fn", "sum")
        alias = agg.get("alias") or f"{fn}_{col or 'rows'}"
        if fn not in _AGG_FNS:
            raise StepError(f"unknown aggregation: {fn}; choose from {sorted(_AGG_FNS)}")
        if fn == "count" and not col:
            select_parts.append(f"COUNT(*) AS {q(alias)}")
            continue
        if col not in ctx.columns:
            raise StepError(f"unknown aggregation column: {col}")
        sql_fn = {"count_distinct": "APPROX_COUNT_DISTINCT", "stddev": "STDDEV_SAMP"}.get(fn, fn.upper())
        select_parts.append(f"{sql_fn}({q(col)}) AS {q(alias)}")
    group_sql = ", ".join(q(c) for c in group_by) if group_by else None
    sql = f"SELECT {', '.join(select_parts)} FROM {q(ctx.prev_table)}"
    if group_sql:
        sql += f" GROUP BY {group_sql}"
    return sql


def _pivot(ctx: StepContext, p: dict) -> str:
    on = _require(p, "on")
    value = _require(p, "value")
    fn = p.get("fn", "sum").upper()
    group_by = p.get("group_by") or []
    if on not in ctx.columns or value not in ctx.columns:
        raise StepError(f"unknown column(s): {[c for c in (on, value) if c not in ctx.columns]}")
    sql = f"PIVOT {q(ctx.prev_table)} ON {q(on)} USING {fn}({q(value)})"
    if group_by:
        sql += f" GROUP BY {', '.join(q(c) for c in group_by)}"
    return sql


def _unpivot(ctx: StepContext, p: dict) -> str:
    on = _require(p, "on")
    name_col = p.get("name_col", "key")
    value_col = p.get("value_col", "value")
    missing = [c for c in on if c not in ctx.columns]
    if missing:
        raise StepError(f"unknown column(s): {missing}")
    return (
        f"UNPIVOT {q(ctx.prev_table)} ON {', '.join(q(c) for c in on)} "
        f"INTO NAME {q(name_col)} VALUE {q(value_col)}"
    )


_JOIN_KINDS = {"inner": "INNER", "left": "LEFT", "right": "RIGHT", "full": "FULL OUTER"}


def _join(ctx: StepContext, p: dict) -> str:
    other = _require(p, "other_dataset")
    on = _require(p, "on")  # list of {left, right}
    how = str(p.get("how", "left")).lower()
    if how not in _JOIN_KINDS:
        raise StepError(f"unknown join kind: {how}; choose from {sorted(_JOIN_KINDS)}")
    other_table = ctx.resolve_dataset_table(other)
    return _join_explicit(ctx, other_table, on, how)


def _join_explicit(ctx: StepContext, other_table: str, on: list[dict], how: str) -> str:
    left_cols = [f"l.{q(c)}" for c in ctx.columns]
    right_join_cols = {pair["right"] for pair in on}
    # right columns are exposed with a prefix to avoid name clashes, except the join keys (redundant with left)
    right_select = f"r.* EXCLUDE ({', '.join(q(c) for c in right_join_cols)})" if right_join_cols else "r.*"
    conds = " AND ".join(f"l.{q(pair['left'])} = r.{q(pair['right'])}" for pair in on)
    return (
        f"SELECT {', '.join(left_cols)}, {right_select} FROM {q(ctx.prev_table)} l "
        f"{_JOIN_KINDS[how]} JOIN {q(other_table)} r ON {conds}"
    )


def _union(ctx: StepContext, p: dict) -> str:
    other = _require(p, "other_dataset")
    distinct = bool(p.get("distinct"))
    other_table = ctx.resolve_dataset_table(other)
    op = "UNION BY NAME" if distinct else "UNION ALL BY NAME"
    return f"SELECT * FROM {q(ctx.prev_table)} {op} SELECT * FROM {q(other_table)}"


def _sort(ctx: StepContext, p: dict) -> str:
    by = _require(p, "by")
    parts = []
    for entry in by:
        col = entry["column"] if isinstance(entry, dict) else entry
        desc = entry.get("desc") if isinstance(entry, dict) else False
        if col not in ctx.columns:
            raise StepError(f"unknown column: {col}")
        parts.append(f"{q(col)} {'DESC' if desc else 'ASC'}")
    return f"SELECT * FROM {q(ctx.prev_table)} ORDER BY {', '.join(parts)}"


def _sample(ctx: StepContext, p: dict) -> str:
    n = p.get("n")
    frac = p.get("frac")
    seed = int(p.get("seed", 42))
    if n:
        return f"SELECT * FROM {q(ctx.prev_table)} USING SAMPLE {int(n)} ROWS (reservoir, {seed})"
    if frac:
        pct = float(frac) * 100
        return f"SELECT * FROM {q(ctx.prev_table)} USING SAMPLE {pct} PERCENT (bernoulli, {seed})"
    raise StepError("sample needs 'n' or 'frac'")


_WINDOW_FNS = {"lag", "lead", "rolling_mean", "rolling_sum", "row_number", "rank"}


def _window(ctx: StepContext, p: dict) -> str:
    fn = _require(p, "fn")
    if fn not in _WINDOW_FNS:
        raise StepError(f"unknown window fn: {fn}; choose from {sorted(_WINDOW_FNS)}")
    order_by = _require(p, "order_by")
    partition_by = p.get("partition_by") or []
    new_col = p.get("new_column") or f"{fn}_{p.get('column', '')}".rstrip("_")
    order_sql = ", ".join(q(c) for c in order_by)
    part_sql = f"PARTITION BY {', '.join(q(c) for c in partition_by)} " if partition_by else ""
    over = f"({part_sql}ORDER BY {order_sql})"
    if fn in ("lag", "lead"):
        col = _require(p, "column")
        offset = int(p.get("offset", 1))
        expr = f"{fn.upper()}({q(col)}, {offset}) OVER {over}"
    elif fn in ("rolling_mean", "rolling_sum"):
        col = _require(p, "column")
        window_size = int(p.get("window_size", 3))
        agg = "AVG" if fn == "rolling_mean" else "SUM"
        over = f"({part_sql}ORDER BY {order_sql} ROWS BETWEEN {window_size - 1} PRECEDING AND CURRENT ROW)"
        expr = f"{agg}({q(col)}) OVER {over}"
    elif fn == "row_number":
        expr = f"row_number() OVER {over}"
    else:
        expr = f"rank() OVER {over}"
    return f"SELECT *, {expr} AS {q(new_col)} FROM {q(ctx.prev_table)}"


def _sql_step(ctx: StepContext, p: dict) -> str:
    from ..sqlgate import gate_sql

    raw = _require(p, "sql")
    gate_sql(raw)
    return raw.replace("__prev__", q(ctx.prev_table)).rstrip().rstrip(";")


STEP_BUILDERS: dict[str, Callable[[StepContext, dict], str]] = {
    "filter": _filter,
    "select": _select,
    "drop": _drop,
    "rename": _rename,
    "cast": _cast,
    "fill_null": _fill_null,
    "drop_duplicates": _drop_duplicates,
    "derive": _derive,
    "split_column": _split_column,
    "text": _text,
    "replace": _replace,
    "bin": _bin,
    "date_parts": _date_parts,
    "group": _group,
    "pivot": _pivot,
    "unpivot": _unpivot,
    "join": _join,
    "union": _union,
    "sort": _sort,
    "sample": _sample,
    "window": _window,
    "sql": _sql_step,
}

STEP_KINDS = tuple(STEP_BUILDERS)


def build_step_sql(op: str, ctx: StepContext, params: dict) -> str:
    builder = STEP_BUILDERS.get(op)
    if not builder:
        raise StepError(f"unknown step: {op}; choose from {STEP_KINDS}")
    return builder(ctx, dict(params or {}))

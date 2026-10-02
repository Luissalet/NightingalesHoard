"""The semantic layer: named metrics and dimensions over a dataset, defined
once in YAML and reused by every code dashboard. One document per workbench,
stored at `<data_dir>/semantic.yaml`.

```yaml
version: 1
models:
  sales:
    dataset: ventas
    time_dimension: fecha
    dimensions:
      region: {column: region, label: Region}
      month: {expr: "date_trunc('month', fecha)", label: Month, type: time}
    metrics:
      revenue: {expr: "SUM(importe)", format: "€,.0f", label: Revenue}
      orders: {expr: "COUNT(*)", format: ",d"}
      revenue_prev: {metric: revenue, offset: "-1 month"}
```

Every column/expr here is compiled straight into SQL against the dataset's
own read-only view (see `workbench/engine.py::set_view`) — the same trust
level as a chart's filter fragment in `workbench/charts.py`. `validate()`
actually runs each expression (LIMIT 0) through the engine so a broken
column name or a typo comes back as a precise DuckDB error with a `hint`,
not a vague failure once the dashboard renders.
"""

from __future__ import annotations

import calendar
import re
from dataclasses import dataclass, field
from datetime import date, timedelta
from typing import Any, Optional

import duckdb
import yaml

from ..hoard_link.atomic import write_text_atomic
from ..sqlgate import SQLGateError, gate_sql
from ..workbench.engine import Engine, q

__all__ = [
    "SemanticError", "Issue", "default_doc", "load", "save", "parse",
    "validate", "compile", "suggest", "get_model", "dim_expr", "metric_expr",
    "filters_sql",
]

_OFFSET_UNITS = ("day", "week", "month", "quarter", "year")
_OFFSET_RE = re.compile(r"^\s*([+-]?\d+)\s+(day|week|month|quarter|year)s?\s*$", re.IGNORECASE)
_DATE_RE = re.compile(r"^\d{4}-\d{2}-\d{2}")
_ALLOWED_ORDER = ("eq", "in", "between", "gte", "lte", "contains")


class SemanticError(ValueError):
    pass


@dataclass
class Issue:
    path: str
    level: str  # "error" | "warning"
    message: str
    hint: str = ""

    def to_dict(self) -> dict:
        return {"path": self.path, "level": self.level, "message": self.message, "hint": self.hint}


def default_doc() -> dict:
    return {"version": 1, "models": {}}


def parse(text: str) -> dict:
    """Parse a semantic document from YAML text (JSON is valid YAML)."""
    if not text or not text.strip():
        return default_doc()
    try:
        doc = yaml.safe_load(text)
    except yaml.YAMLError as exc:
        raise SemanticError(f"could not parse YAML: {exc}") from exc
    if doc is None:
        return default_doc()
    if not isinstance(doc, dict):
        raise SemanticError("the semantic document must be a mapping at the top level")
    doc.setdefault("version", 1)
    doc.setdefault("models", {})
    return doc


def load(config) -> dict:
    path = config.data_dir / "semantic.yaml"
    if not path.exists():
        return default_doc()
    return parse(path.read_text(encoding="utf-8"))


def save(config, text: str) -> dict:
    """Parse `text`, and only write it to disk once it parses cleanly (a
    caller should still run `validate()` first for real content checks)."""
    doc = parse(text)
    path = config.data_dir / "semantic.yaml"
    path.parent.mkdir(parents=True, exist_ok=True)
    write_text_atomic(path, text if text.strip() else yaml.safe_dump(doc, sort_keys=False, allow_unicode=True))
    return doc


# ---- model/dimension/metric lookups ---------------------------------------

def get_model(doc: dict, model: str) -> dict:
    models = doc.get("models") or {}
    if model not in models:
        raise SemanticError(f"unknown semantic model: {model!r}. Defined models: {sorted(models)}")
    return models[model]


def resolve_source(mcfg: dict) -> str:
    if mcfg.get("sql"):
        return f"({mcfg['sql']}) __dac_src"
    dataset = mcfg.get("dataset")
    if not dataset:
        raise SemanticError("model needs a 'dataset' (or a 'sql' source)")
    return q(dataset)


def _parse_offset(text: str) -> tuple[int, str]:
    m = _OFFSET_RE.match(str(text or ""))
    if not m:
        raise SemanticError(f"bad offset {text!r}; expected e.g. '-1 month', '2 week', '+1 day'")
    return int(m.group(1)), m.group(2).lower()


def _interval_sql(n: int, unit: str) -> str:
    if unit == "day":
        return f"INTERVAL '{n} day'"
    if unit == "week":
        return f"INTERVAL '{n * 7} day'"
    if unit == "month":
        return f"INTERVAL '{n} month'"
    if unit == "quarter":
        return f"INTERVAL '{n * 3} month'"
    if unit == "year":
        return f"INTERVAL '{n} year'"
    raise SemanticError(f"unknown offset unit: {unit}")


def dim_expr(doc: dict, model: str, name: str, time_grain: Optional[str] = None) -> tuple[str, bool]:
    """Returns (sql_expr, is_time). Raises SemanticError for an unknown dimension."""
    mcfg = get_model(doc, model)
    if name == mcfg.get("time_dimension"):
        base = q(name)
        expr = f"date_trunc('{time_grain}', {base})" if time_grain else base
        return expr, True
    dims = mcfg.get("dimensions") or {}
    if name not in dims:
        raise SemanticError(f"unknown dimension {name!r} in model {model!r}. Defined: "
                             f"{sorted(dims)} (+ time_dimension {mcfg.get('time_dimension')!r})")
    cfg = dims[name]
    is_time = cfg.get("type") == "time"
    base = cfg["expr"] if cfg.get("expr") else q(cfg.get("column", name))
    expr = f"date_trunc('{time_grain}', {base})" if (is_time and time_grain) else base
    return expr, is_time


def _metric_def(doc: dict, model: str, name: str) -> dict:
    mcfg = get_model(doc, model)
    metrics = mcfg.get("metrics") or {}
    if name not in metrics:
        raise SemanticError(f"unknown metric {name!r} in model {model!r}. Defined: {sorted(metrics)}")
    return metrics[name]


def metric_expr(doc: dict, model: str, name: str) -> str:
    """The SQL expression for a *base* metric (not an offset/derived one)."""
    mdef = _metric_def(doc, model, name)
    if "metric" in mdef:
        raise SemanticError(f"{name!r} is a derived metric (offset of {mdef['metric']!r}); "
                             "it can only be requested as a top-level metric, not composed further")
    if not mdef.get("expr"):
        raise SemanticError(f"metric {name!r} needs an 'expr'")
    return mdef["expr"]


# ---- filters ----------------------------------------------------------------

def _lit(value: Any) -> str:
    if value is None:
        return "NULL"
    if isinstance(value, bool):
        return "TRUE" if value else "FALSE"
    if isinstance(value, (int, float)):
        return str(value)
    text = str(value)
    if _DATE_RE.match(text):
        return f"DATE '{text[:10]}'"
    return "'" + text.replace("'", "''") + "'"


def _add_months(d: date, n: int) -> date:
    m = d.month - 1 + n
    y = d.year + m // 12
    m = m % 12 + 1
    day = min(d.day, calendar.monthrange(y, m)[1])
    return date(y, m, day)


def _shift_date_str(value: str, n: int, unit: str) -> str:
    try:
        d = date.fromisoformat(str(value)[:10])
    except ValueError:
        return value
    if unit == "day":
        d2 = d + timedelta(days=n)
    elif unit == "week":
        d2 = d + timedelta(weeks=n)
    elif unit == "month":
        d2 = _add_months(d, n)
    elif unit == "quarter":
        d2 = _add_months(d, n * 3)
    elif unit == "year":
        d2 = _add_months(d, n * 12)
    else:
        return value
    return d2.isoformat()


def _shift_value(value: Any, n: int, unit: str) -> Any:
    if isinstance(value, (list, tuple)):
        return [_shift_value(v, n, unit) for v in value]
    if isinstance(value, str):
        return _shift_date_str(value, n, unit)
    return value


def _dim_for_filter(doc: dict, model: str, name: str) -> tuple[str, bool]:
    mcfg = get_model(doc, model)
    if name == mcfg.get("time_dimension") or name in (mcfg.get("dimensions") or {}):
        return dim_expr(doc, model, name)
    raise SemanticError(f"unknown filter dimension {name!r} in model {model!r}")


def _condition_sql(expr: str, op: str, value: Any) -> str:
    if op == "eq":
        return f"{expr} = {_lit(value)}"
    if op == "in":
        values = value if isinstance(value, (list, tuple)) else [value]
        return f"{expr} IN ({', '.join(_lit(v) for v in values)})"
    if op == "between":
        lo, hi = value
        return f"{expr} BETWEEN {_lit(lo)} AND {_lit(hi)}"
    if op == "gte":
        return f"{expr} >= {_lit(value)}"
    if op == "lte":
        return f"{expr} <= {_lit(value)}"
    if op == "contains":
        return f"{expr} ILIKE {_lit('%' + str(value) + '%')}"
    raise SemanticError(f"unknown filter op {op!r}; expected one of {_ALLOWED_ORDER}")


def filters_sql(doc: dict, model: str, filters: Optional[list[dict]], shift: Optional[tuple[int, str]] = None) -> str:
    filters = filters or []
    parts = []
    for f in filters:
        dim = f["dim"]
        op = f.get("op", "eq")
        value = f.get("value")
        if value is None or value == "" or (isinstance(value, str) and value.lower() == "all"):
            continue
        expr, is_time = _dim_for_filter(doc, model, dim)
        if shift and is_time:
            value = _shift_value(value, *shift)
        parts.append(_condition_sql(expr, op, value))
    return " WHERE " + " AND ".join(parts) if parts else ""


# ---- compile -----------------------------------------------------------------

def compile(doc: dict, model: str, metrics: list[str], dimensions: Optional[list[str]] = None,
            filters: Optional[list[dict]] = None, order: Optional[str] = None, limit: Optional[int] = None,
            time_grain: Optional[str] = None) -> str:
    """Compile one semantic query. `metrics` may include derived/offset
    metrics (see module docstring); everything else must be a base metric."""
    dimensions = list(dimensions or [])
    mcfg = get_model(doc, model)
    source = resolve_source(mcfg)
    where = filters_sql(doc, model, filters)

    base_metrics: list[str] = []
    offsets: list[tuple[str, str, int, str]] = []
    for name in metrics:
        mdef = _metric_def(doc, model, name)
        if "metric" in mdef:
            if mdef["metric"] == name:
                raise SemanticError(f"metric {name!r} cannot reference itself")
            n, unit = _parse_offset(mdef.get("offset", "0 day"))
            offsets.append((name, mdef["metric"], n, unit))
        else:
            base_metrics.append(name)

    dim_selects = [f"{dim_expr(doc, model, d, time_grain)[0]} AS {q(d)}" for d in dimensions]
    metric_selects = [f"{metric_expr(doc, model, m)} AS {q(m)}" for m in base_metrics]
    group_by = f" GROUP BY {', '.join(q(d) for d in dimensions)}" if dimensions else ""
    base_select = ", ".join(dim_selects + metric_selects) or "1"
    base_sql = f"SELECT {base_select} FROM {source}{where}{group_by}"

    if not offsets:
        sql = base_sql
    elif not dimensions:
        # a single scalar row: shift the time-range filter itself for each
        # offset metric and run it as a plain correlated-free scalar subquery.
        extra = []
        for name, base_name, n, unit in offsets:
            shifted_where = filters_sql(doc, model, filters, shift=(n, unit))
            extra.append(f"(SELECT {metric_expr(doc, model, base_name)} FROM {source}{shifted_where}) AS {q(name)}")
        all_selects = metric_selects + extra
        sql = f"SELECT {', '.join(all_selects) or '1'} FROM {source}{where}"
    else:
        time_dim = None
        for d in dimensions:
            _, is_time = dim_expr(doc, model, d, time_grain)
            if is_time:
                time_dim = d
                break
        if time_dim is None:
            raise SemanticError("an offset metric needs a time dimension among the query's dimensions")
        parts = [f"main AS ({base_sql})"]
        joins = []
        select_cols = [f"main.{q(d)}" for d in dimensions] + [f"main.{q(m)}" for m in base_metrics]
        for i, (name, base_name, n, unit) in enumerate(offsets):
            alias = f"off_{i}"
            off_dim_selects = []
            for d in dimensions:
                raw_expr, is_time = dim_expr(doc, model, d, time_grain)
                if d == time_dim:
                    shifted = f"({raw_expr}) + {_interval_sql(-n, unit)}"
                    off_dim_selects.append(f"{shifted} AS {q(d)}")
                else:
                    off_dim_selects.append(f"{raw_expr} AS {q(d)}")
            off_group = ", ".join(
                (f"({dim_expr(doc, model, d, time_grain)[0]}) + {_interval_sql(-n, unit)}" if d == time_dim
                 else dim_expr(doc, model, d, time_grain)[0])
                for d in dimensions
            )
            off_select = ", ".join(off_dim_selects + [f"{metric_expr(doc, model, base_name)} AS {q(name)}"])
            parts.append(f"{alias} AS (SELECT {off_select} FROM {source}{where} GROUP BY {off_group})")
            on = " AND ".join(f"main.{q(d)} = {alias}.{q(d)}" for d in dimensions)
            joins.append(f"LEFT JOIN {alias} ON {on}")
            select_cols.append(f"{alias}.{q(name)}")
        sql = f"WITH {', '.join(parts)} SELECT {', '.join(select_cols)} FROM main {' '.join(joins)}"

    if order:
        desc = order.startswith("-")
        col = order[1:] if desc else order
        sql += f" ORDER BY {q(col)} {'DESC' if desc else 'ASC'}"
    if limit:
        sql += f" LIMIT {int(limit)}"

    try:
        gate_sql(sql)
    except SQLGateError as exc:  # pragma: no cover - defensive, generated SQL is always read-only
        raise SemanticError(f"generated SQL failed the read-only gate: {exc}") from exc
    return sql


# ---- validate -----------------------------------------------------------------

def validate(doc: dict, engine: Engine) -> list[dict]:
    issues: list[Issue] = []
    if not isinstance(doc, dict):
        return [Issue("", "error", "the semantic document must be a mapping", "start from `dac_semantic_suggest`").to_dict()]
    models = doc.get("models") or {}
    if not models:
        issues.append(Issue("models", "warning", "no models defined yet",
                             "add at least one model, or call semantic_suggest with a dataset name"))
    for model_name, mcfg in models.items():
        p = f"models.{model_name}"
        if not isinstance(mcfg, dict):
            issues.append(Issue(p, "error", "a model must be a mapping", "see docs/DASHBOARDS_AS_CODE.md"))
            continue
        dataset = mcfg.get("dataset")
        columns: set[str] = set()
        if not dataset and not mcfg.get("sql"):
            issues.append(Issue(p, "error", "model needs a 'dataset' or a 'sql' source",
                                 f"add `dataset: <name>` to models.{model_name}"))
        elif dataset:
            try:
                columns = set(engine.table_columns(dataset))
            except duckdb.Error:
                issues.append(Issue(f"{p}.dataset", "error", f"unknown dataset: {dataset!r}",
                                     "register it first with data_ingest, or fix the name"))

        time_dimension = mcfg.get("time_dimension")
        if time_dimension and columns and time_dimension not in columns:
            issues.append(Issue(f"{p}.time_dimension", "error", f"column {time_dimension!r} not found in {dataset!r}",
                                 f"use one of: {sorted(columns)[:20]}"))

        dims = mcfg.get("dimensions") or {}
        metrics = mcfg.get("metrics") or {}
        overlap = set(dims) & set(metrics)
        for name in overlap:
            issues.append(Issue(f"{p}.{name}", "error", f"{name!r} is defined as both a dimension and a metric",
                                 "rename one of them; every metric/dimension name must be unique within a model"))

        for name, cfg in dims.items():
            dp = f"{p}.dimensions.{name}"
            if not isinstance(cfg, dict):
                issues.append(Issue(dp, "error", "a dimension must be a mapping", '{"column": "..."} or {"expr": "..."}'))
                continue
            column = cfg.get("column")
            if column and columns and column not in columns:
                issues.append(Issue(dp, "error", f"column {column!r} not found in {dataset!r}",
                                     f"use one of: {sorted(columns)[:20]}"))
            expr = cfg.get("expr")
            if expr and dataset:
                try:
                    engine.query(f"SELECT {expr} AS x FROM {q(dataset)} LIMIT 0", limit=1)
                except Exception as exc:  # noqa: BLE001
                    issues.append(Issue(dp, "error", f"bad expression: {exc}",
                                         "check column names and DuckDB syntax in 'expr'"))
            if not column and not expr:
                issues.append(Issue(dp, "error", "dimension needs a 'column' or an 'expr'", ""))

        for name, cfg in metrics.items():
            mp = f"{p}.metrics.{name}"
            if not isinstance(cfg, dict):
                issues.append(Issue(mp, "error", "a metric must be a mapping", '{"expr": "SUM(...)"} or {"metric": "...", "offset": "-1 month"}'))
                continue
            if "metric" in cfg:
                ref = cfg["metric"]
                if ref == name:
                    issues.append(Issue(mp, "error", "a metric cannot reference itself", "point 'metric' at a different, already-defined metric"))
                elif ref not in metrics:
                    issues.append(Issue(mp, "error", f"unknown base metric {ref!r}", f"defined metrics: {sorted(metrics)}"))
                try:
                    _parse_offset(cfg.get("offset", ""))
                except SemanticError as exc:
                    issues.append(Issue(f"{mp}.offset", "error", str(exc), "use e.g. '-1 month', '2 week', '+1 day'"))
                continue
            expr = cfg.get("expr")
            if not expr:
                issues.append(Issue(mp, "error", "metric needs an 'expr' (or 'metric'+'offset' for a derived one)", ""))
            elif dataset:
                try:
                    engine.query(f"SELECT {expr} AS x FROM {q(dataset)} LIMIT 0", limit=1)
                except Exception as exc:  # noqa: BLE001
                    issues.append(Issue(mp, "error", f"bad expression: {exc}",
                                         "check column names and DuckDB syntax in 'expr' (e.g. SUM(col))"))
    return [i.to_dict() for i in issues]


# ---- suggest ------------------------------------------------------------------

def _label(name: str) -> str:
    return name.replace("_", " ").strip().capitalize()


def suggest(dataset_name: str, columns: list[dict], profile: dict, row_count: int = 0) -> dict:
    """Propose a starting semantic model from a dataset's columns/profile:
    numeric -> SUM metric, low-cardinality text -> dimension, date -> a time
    dimension plus a month grain dimension. Always a starting point to edit,
    never a final answer."""
    from ..workbench.engine import is_numeric_type, is_temporal_type

    dims: dict[str, dict] = {}
    metrics: dict[str, dict] = {"rows": {"expr": "COUNT(*)", "format": ",d", "label": "Rows"}}
    time_dimension = None
    max_card = max(50, min(200, row_count // 20 or 50))

    for col in columns:
        name = col["name"]
        ctype = col["type"]
        prof = profile.get(name, {})
        if is_temporal_type(ctype) and time_dimension is None:
            time_dimension = name
            dims["month"] = {"expr": f"date_trunc('month', {q(name)})", "label": "Month", "type": "time"}
        elif is_numeric_type(ctype):
            metrics[f"{name}_sum"] = {"expr": f"SUM({q(name)})", "format": ",.2f", "label": f"Total {_label(name)}"}
            metrics[f"{name}_avg"] = {"expr": f"AVG({q(name)})", "format": ",.2f", "label": f"Average {_label(name)}"}
        else:
            distinct = prof.get("distinct_approx", max_card + 1)
            if distinct and distinct <= max_card:
                dims[name] = {"column": name, "label": _label(name)}

    model: dict[str, Any] = {"dataset": dataset_name, "dimensions": dims, "metrics": metrics}
    if time_dimension:
        model["time_dimension"] = time_dimension
    return {"version": 1, "models": {dataset_name: model}}

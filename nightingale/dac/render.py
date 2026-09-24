"""Render a dashboard spec (see `spec.py`) against its semantic model
(`semantic.py`) into plain data + Vega-Lite specs the client can draw. Every
widget is compiled and executed independently — a broken widget reports its
own `error` and the rest of the dashboard still renders (see module-level
`render()`)."""

from __future__ import annotations

from typing import Any, Optional

from . import exprs, semantic, spec as spec_mod
from ..workbench.charts import ChartSpec, to_vega_lite

__all__ = ["render"]


def _filters_for(filters_defs: list[dict], resolved: dict[str, Any], overrides: Optional[dict] = None) -> list[dict]:
    out = []
    for f in filters_defs:
        dim = f.get("dimension")
        if not dim:
            continue
        value = resolved.get(f["name"])
        if value is None or (isinstance(value, str) and value.lower() == "all"):
            continue
        op = spec_mod.FILTER_OPS_BY_TYPE.get(f.get("type"), "eq")
        out.append({"dim": dim, "op": op, "value": value})
    if overrides:
        out = [r for r in out if r["dim"] not in overrides]
        for dim, value in overrides.items():
            out.append({"dim": dim, "op": "eq", "value": value})
    return out


def _run_widget(services, semantic_doc: dict, model: str, mcfg: dict, widget: dict,
                 context: dict, filters_defs: list[dict], resolved: dict) -> dict:
    wtype = widget.get("type")
    title = widget.get("title")
    try:
        title = exprs.render_template(title, context) if title else title
    except exprs.ExprError:
        pass  # a bad template in a title is cosmetic; keep the raw text

    if wtype == "text":
        try:
            md = exprs.render_template(widget.get("markdown") or widget.get("text") or "", context)
            return {"type": "text", "title": title, "markdown": md}
        except exprs.ExprError as exc:
            return {"type": "text", "title": title, "error": str(exc)}

    try:
        wfilter = {}
        for dim, val in (widget.get("filter") or {}).items():
            wfilter[dim] = exprs.render_template(val, context) if isinstance(val, str) else val
        filters = _filters_for(filters_defs, resolved, wfilter)

        if wtype == "metric":
            metric = widget["metric"]
            compare = widget.get("compare")
            metrics = [metric] + ([compare] if compare else [])
            sql = semantic.compile(semantic_doc, model, metrics, [], filters)
            res = services.engine.query(sql, limit=1)
            row = res["rows"][0] if res["rows"] else {}
            value = row.get(metric)
            compare_value = row.get(compare) if compare else None
            delta_pct = None
            if value is not None and compare_value not in (None, 0):
                delta_pct = round((float(value) - float(compare_value)) / abs(float(compare_value)) * 100, 2)
            mdef = (mcfg.get("metrics") or {}).get(metric, {})
            return {"type": "metric", "title": title or mdef.get("label", metric), "sql": sql,
                    "value": value, "compare_value": compare_value, "delta_pct": delta_pct,
                    "format": mdef.get("format")}

        if wtype == "chart":
            kind = widget["kind"]
            x, y, color = widget["x"], widget["y"], widget.get("color")
            dims = [x] + ([color] if color else [])
            sql = semantic.compile(semantic_doc, model, [y], dims, filters, order=(x if not color else None))
            res = services.engine.query(sql, limit=5000)
            col_types = {c["name"]: c["type"] for c in res["columns"]}
            if kind in ("pie", "donut"):
                rows = [{"label": r.get(x), "value": r.get(y)} for r in res["rows"]]
                chart_spec = ChartSpec(kind=kind, dataset=model, x=x, y=y, title=title or "")
            else:
                rows = []
                for r in res["rows"]:
                    item = {"x": r.get(x), "y": r.get(y)}
                    if color:
                        item["series"] = r.get(color)
                    rows.append(item)
                chart_spec = ChartSpec(kind=kind, dataset=model, x=x, y=y, color=color, title=title or "")
            data = {"rows": rows, "columns": [{"name": "x", "type": col_types.get(x, "VARCHAR")}]}
            return {"type": "chart", "title": title, "sql": sql,
                    "vega_lite": to_vega_lite(chart_spec, data), "row_count": len(rows)}

        if wtype == "table":
            dims = widget.get("dimensions") or []
            metrics = widget.get("metrics") or []
            limit = int(widget.get("limit", 50))
            sql = semantic.compile(semantic_doc, model, metrics, dims, filters, order=widget.get("sort"), limit=limit)
            res = services.engine.query(sql, limit=limit)
            return {"type": "table", "title": title, "sql": sql,
                    "columns": [c["name"] for c in res["columns"]], "rows": res["rows"]}

        return {"type": wtype, "title": title, "error": f"unknown widget type: {wtype!r}"}
    except Exception as exc:  # noqa: BLE001 - one bad widget must never fail the whole dashboard
        return {"type": wtype, "title": title, "error": str(exc)}


def render(services, doc: dict, filter_values: Optional[dict] = None) -> dict:
    filter_values = filter_values or {}
    semantic_doc = semantic.load(services.config)
    model = doc.get("model")
    mcfg = semantic.get_model(semantic_doc, model)

    filters_defs = doc.get("filters") or []
    resolved = {f["name"]: filter_values.get(f["name"], f.get("default")) for f in filters_defs}

    out_filters = []
    for f in filters_defs:
        entry = {"name": f["name"], "type": f.get("type"), "value": resolved.get(f["name"])}
        if f.get("type") == "select" and f.get("dimension"):
            try:
                dim_sql, _ = semantic.dim_expr(semantic_doc, model, f["dimension"])
                source = semantic.resolve_source(mcfg)
                res = services.engine.query(
                    f"SELECT DISTINCT {dim_sql} AS v FROM {source} WHERE {dim_sql} IS NOT NULL ORDER BY 1 LIMIT 50", limit=50)
                entry["options"] = [r["v"] for r in res["rows"]]
            except Exception:  # noqa: BLE001
                entry["options"] = []
        out_filters.append(entry)

    warnings: list[str] = []
    out_tabs = []
    for tab in doc.get("tabs") or []:
        out_rows = []
        for row in tab.get("rows") or []:
            if "if" in row:
                try:
                    ok = exprs.safe_eval(row["if"], {"filters": resolved, "each": None})
                except exprs.ExprError as exc:
                    warnings.append(f"row condition skipped: {exc}")
                    continue
                if not ok:
                    continue

            if "for" in row:
                loop = row["for"]
                src = loop.get("in", {})
                each_dim = loop.get("each")
                try:
                    top_n = int(src.get("top", 5))
                    top_sql = semantic.compile(semantic_doc, model, [src["by"]], [src["dimension"]],
                                                _filters_for(filters_defs, resolved), order=f"-{src['by']}", limit=top_n)
                    top_res = services.engine.query(top_sql, limit=top_n)
                    values = [r[src["dimension"]] for r in top_res["rows"]]
                except Exception as exc:  # noqa: BLE001
                    warnings.append(f"loop over {each_dim!r} failed: {exc}")
                    values = []
                for value in values:
                    ctx = {"filters": resolved, "each": value}
                    widgets = [_run_widget(services, semantic_doc, model, mcfg, w, ctx, filters_defs, resolved)
                               for w in row.get("widgets") or []]
                    out_rows.append({"cols": row.get("cols", len(widgets) or 1), "widgets": widgets})
            else:
                ctx = {"filters": resolved, "each": None}
                widgets = [_run_widget(services, semantic_doc, model, mcfg, w, ctx, filters_defs, resolved)
                           for w in row.get("widgets") or []]
                out_rows.append({"cols": row.get("cols", len(widgets) or 1), "widgets": widgets})
        out_tabs.append({"name": tab.get("name"), "rows": out_rows})

    return {"name": doc.get("name"), "tabs": out_tabs, "filters": out_filters, "warnings": warnings}

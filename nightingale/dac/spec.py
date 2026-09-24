"""Dashboard spec: a YAML document over one semantic model (see
`semantic.py`) — filters, tabs of rows of widgets, with `for` loops and `if`
conditions for rows. `validate()` checks it against the semantic model so an
agent gets a `hint` for every mistake before ever rendering it.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Optional

import yaml

from . import exprs
from . import semantic

__all__ = ["SpecError", "Issue", "parse", "validate", "WIDGET_TYPES", "FILTER_TYPES", "CHART_KINDS"]

WIDGET_TYPES = ("metric", "chart", "table", "text")
FILTER_TYPES = ("date-range", "select", "text", "number")
CHART_KINDS = ("bar", "grouped_bar", "stacked_bar", "line", "area", "scatter", "pie", "donut")
FILTER_OPS_BY_TYPE = {"date-range": "between", "select": "eq", "text": "contains", "number": "eq"}


class SpecError(ValueError):
    pass


@dataclass
class Issue:
    path: str
    level: str
    message: str
    hint: str = ""

    def to_dict(self) -> dict:
        return {"path": self.path, "level": self.level, "message": self.message, "hint": self.hint}


def parse(text: str) -> dict:
    if not text or not text.strip():
        raise SpecError("empty dashboard document")
    try:
        doc = yaml.safe_load(text)
    except yaml.YAMLError as exc:
        raise SpecError(f"could not parse YAML: {exc}") from exc
    if not isinstance(doc, dict):
        raise SpecError("the dashboard document must be a mapping at the top level")
    doc.setdefault("version", 1)
    doc.setdefault("tabs", [])
    return doc


def _known_vars(doc: dict) -> set[str]:
    names = {f"filters.{f.get('name')}" for f in (doc.get("filters") or []) if isinstance(f, dict) and f.get("name")}
    names.add("each")
    return names


def _check_template(text: Any, path: str, known: set[str], issues: list[Issue]) -> None:
    if not isinstance(text, str):
        return
    for name in exprs.template_names(text):
        if name not in known and not name.startswith("each."):
            issues.append(Issue(path, "error", f"unknown template variable: {{{{ {name} }}}}",
                                 f"declare a filter named {name.split('.')[-1]!r}, or use 'each' inside a 'for' row"))


def _widget_issues(widget: dict, path: str, mcfg: dict, known_vars: set[str]) -> list[Issue]:
    issues: list[Issue] = []
    if not isinstance(widget, dict):
        return [Issue(path, "error", "a widget must be a mapping", "")]
    wtype = widget.get("type")
    if wtype not in WIDGET_TYPES:
        issues.append(Issue(path, "error", f"unknown widget type: {wtype!r}", f"use one of {WIDGET_TYPES}"))
        return issues
    metrics = mcfg.get("metrics") or {}
    dims = dict(mcfg.get("dimensions") or {})
    if mcfg.get("time_dimension"):
        dims[mcfg["time_dimension"]] = {}

    def check_metric(name, p):
        if name and name not in metrics:
            issues.append(Issue(p, "error", f"unknown metric: {name!r}", f"defined metrics: {sorted(metrics)}"))

    def check_dim(name, p):
        if name and name not in dims:
            issues.append(Issue(p, "error", f"unknown dimension: {name!r}", f"defined dimensions: {sorted(dims)}"))

    if wtype == "metric":
        check_metric(widget.get("metric"), f"{path}.metric")
        check_metric(widget.get("compare"), f"{path}.compare")
    elif wtype == "chart":
        if widget.get("kind") not in CHART_KINDS:
            issues.append(Issue(f"{path}.kind", "error", f"unknown chart kind: {widget.get('kind')!r}",
                                 f"use one of {CHART_KINDS}"))
        check_dim(widget.get("x"), f"{path}.x")
        check_metric(widget.get("y"), f"{path}.y")
        if widget.get("color"):
            check_dim(widget.get("color"), f"{path}.color")
    elif wtype == "table":
        for d in widget.get("dimensions") or []:
            check_dim(d, f"{path}.dimensions")
        for m in widget.get("metrics") or []:
            check_metric(m, f"{path}.metrics")
        sort = widget.get("sort")
        if sort:
            name = sort[1:] if sort.startswith("-") else sort
            if name not in metrics and name not in dims:
                issues.append(Issue(f"{path}.sort", "error", f"unknown sort column: {sort!r}", ""))
    elif wtype == "text":
        if not widget.get("markdown") and not widget.get("text"):
            issues.append(Issue(path, "error", "a text widget needs 'markdown' (or 'text')", ""))

    for key in ("title", "markdown", "text"):
        _check_template(widget.get(key), f"{path}.{key}", known_vars, issues)
    wfilter = widget.get("filter") or {}
    for dim, value in wfilter.items():
        check_dim(dim, f"{path}.filter.{dim}")
        _check_template(value, f"{path}.filter.{dim}", known_vars, issues)
    return issues


def validate(doc: dict, semantic_doc: Optional[dict] = None) -> list[dict]:
    issues: list[Issue] = []
    if not isinstance(doc, dict):
        return [Issue("", "error", "the dashboard document must be a mapping", "").to_dict()]
    if not doc.get("name"):
        issues.append(Issue("name", "error", "dashboard needs a 'name'", ""))
    model = doc.get("model")
    mcfg: dict = {}
    if not model:
        issues.append(Issue("model", "error", "dashboard needs a 'model' (a semantic model name)", ""))
    elif semantic_doc is not None:
        try:
            mcfg = semantic.get_model(semantic_doc, model)
        except semantic.SemanticError as exc:
            issues.append(Issue("model", "error", str(exc), "call semantic_get to see the defined models"))

    for i, f in enumerate(doc.get("filters") or []):
        p = f"filters[{i}]"
        if not isinstance(f, dict) or not f.get("name"):
            issues.append(Issue(p, "error", "a filter needs a 'name'", ""))
            continue
        if f.get("type") not in FILTER_TYPES:
            issues.append(Issue(f"{p}.type", "error", f"unknown filter type: {f.get('type')!r}", f"use one of {FILTER_TYPES}"))
        if mcfg and f.get("dimension"):
            dims = dict(mcfg.get("dimensions") or {})
            if mcfg.get("time_dimension"):
                dims[mcfg["time_dimension"]] = {}
            if f["dimension"] not in dims:
                issues.append(Issue(f"{p}.dimension", "error", f"unknown dimension: {f['dimension']!r}",
                                     f"defined dimensions: {sorted(dims)}"))

    known_vars = _known_vars(doc)

    for ti, tab in enumerate(doc.get("tabs") or []):
        tp = f"tabs[{ti}]"
        if not isinstance(tab, dict) or not tab.get("name"):
            issues.append(Issue(tp, "error", "a tab needs a 'name'", ""))
            continue
        for ri, row in enumerate(tab.get("rows") or []):
            rp = f"{tp}.rows[{ri}]"
            if not isinstance(row, dict):
                issues.append(Issue(rp, "error", "a row must be a mapping", ""))
                continue
            cols = row.get("cols")
            if cols is not None and not (1 <= int(cols) <= 6):
                issues.append(Issue(f"{rp}.cols", "error", f"cols must be 1-6, got {cols}", ""))
            row_known = set(known_vars)
            if "for" in row:
                loop = row["for"]
                if not isinstance(loop, dict) or "each" not in loop or "in" not in loop:
                    issues.append(Issue(f"{rp}.for", "error", "a loop needs 'each' and 'in'",
                                         '{"each": "region", "in": {"dimension": "region", "top": 3, "by": "revenue"}}'))
                else:
                    row_known.add("each")
                    source = loop["in"]
                    if mcfg:
                        if source.get("dimension") not in dict(mcfg.get("dimensions") or {}):
                            issues.append(Issue(f"{rp}.for.in.dimension", "error",
                                                 f"unknown dimension: {source.get('dimension')!r}", ""))
                        if source.get("by") not in (mcfg.get("metrics") or {}):
                            issues.append(Issue(f"{rp}.for.in.by", "error",
                                                 f"unknown metric: {source.get('by')!r}", ""))
            if "if" in row:
                try:
                    exprs.safe_eval(row["if"], {"filters": {f.get("name"): None for f in (doc.get("filters") or [])}, "each": None})
                except exprs.ExprError as exc:
                    issues.append(Issue(f"{rp}.if", "error", str(exc),
                                         'use == != in and/or/not over filters.<name> or each, e.g. "filters.region == \'all\'"'))
            for wi, widget in enumerate(row.get("widgets") or []):
                wp = f"{rp}.widgets[{wi}]"
                if mcfg:
                    issues.extend(_widget_issues(widget, wp, mcfg, row_known))
                else:
                    _check_template(widget.get("title") if isinstance(widget, dict) else None, wp, row_known, issues)

    return [i.to_dict() for i in issues]

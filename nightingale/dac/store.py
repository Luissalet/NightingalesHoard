"""File-backed storage for code dashboards: one human-diffable YAML file per
dashboard under `<data_dir>/dashboards/<slug>.yaml`, with the last 20 saved
versions of each kept under `dashboards/history/<slug>/` for `history()`/
`diff()`. Also converts a rendered code dashboard into the existing
item-based dashboard (`export_to_items`) and, best-effort, the other way
around (`import_from_items`) — see their docstrings for what is and isn't
preserved.
"""

from __future__ import annotations

import difflib
import re
import time
from pathlib import Path
from typing import Any, Optional

import yaml

from . import semantic as semantic_mod
from . import spec as spec_mod
from ..workbench.engine import slugify_name

__all__ = ["StoreError", "list_dashboards", "get", "put", "delete", "rename",
           "history", "diff", "export_to_items", "import_from_items"]

MAX_HISTORY = 20
_AGG_RE = re.compile(r'^\s*(SUM|AVG|COUNT|MIN|MAX|MEDIAN)\s*\(\s*"?([A-Za-z_][\w]*)"?\s*\)\s*$', re.IGNORECASE)


class StoreError(LookupError):
    pass


def _dir(config) -> Path:
    d = config.data_dir / "dashboards"
    d.mkdir(parents=True, exist_ok=True)
    return d


def _history_dir(config, slug: str) -> Path:
    d = _dir(config) / "history" / slug
    d.mkdir(parents=True, exist_ok=True)
    return d


def _path(config, slug: str) -> Path:
    return _dir(config) / f"{slug}.yaml"


def _unique_slug(config, base: str, exclude: Optional[str] = None) -> str:
    slug = base or "dashboard"
    n = 2
    existing = {p.stem for p in _dir(config).glob("*.yaml")}
    existing.discard(exclude)
    while slug in existing:
        slug = f"{base}-{n}"
        n += 1
    return slug


def list_dashboards(config) -> list[dict]:
    out = []
    for p in sorted(_dir(config).glob("*.yaml")):
        try:
            doc = spec_mod.parse(p.read_text(encoding="utf-8"))
        except spec_mod.SpecError:
            doc = {}
        out.append({"slug": p.stem, "name": doc.get("name", p.stem), "model": doc.get("model"),
                     "updated_at": p.stat().st_mtime})
    out.sort(key=lambda d: d["updated_at"], reverse=True)
    return out


def get(config, slug: str) -> dict:
    path = _path(config, slug)
    if not path.exists():
        raise StoreError(f"unknown dashboard: {slug!r}")
    text = path.read_text(encoding="utf-8")
    return {"slug": slug, "text": text, "doc": spec_mod.parse(text)}


def _snapshot(config, slug: str, text: str) -> None:
    hdir = _history_dir(config, slug)
    stamp = f"{int(time.time() * 1000)}"
    (hdir / f"{stamp}.yaml").write_text(text, encoding="utf-8")
    versions = sorted(hdir.glob("*.yaml"), key=lambda p: p.stat().st_mtime)
    for old in versions[:-MAX_HISTORY]:
        old.unlink(missing_ok=True)


def put(config, text: str, slug: Optional[str] = None) -> dict:
    doc = spec_mod.parse(text)
    if slug is None:
        slug = _unique_slug(config, slugify_name(doc.get("name") or "dashboard"))
    path = _path(config, slug)
    if path.exists():
        _snapshot(config, slug, path.read_text(encoding="utf-8"))
    path.write_text(text, encoding="utf-8")
    return {"slug": slug, "name": doc.get("name", slug), "doc": doc}


def delete(config, slug: str) -> None:
    path = _path(config, slug)
    if not path.exists():
        raise StoreError(f"unknown dashboard: {slug!r}")
    path.unlink()


def rename(config, slug: str, new_name: str) -> dict:
    current = get(config, slug)
    doc = dict(current["doc"])
    doc["name"] = new_name
    new_slug = _unique_slug(config, slugify_name(new_name), exclude=slug)
    text = yaml.safe_dump(doc, sort_keys=False, allow_unicode=True)
    _path(config, new_slug).write_text(text, encoding="utf-8")
    old_history = _dir(config) / "history" / slug
    if old_history.exists():
        old_history.rename(_dir(config) / "history" / new_slug)
    _path(config, slug).unlink()
    return {"slug": new_slug, "name": new_name, "doc": doc}


def history(config, slug: str) -> list[dict]:
    hdir = _dir(config) / "history" / slug
    if not hdir.exists():
        return []
    out = [{"id": p.stem, "saved_at": p.stat().st_mtime} for p in hdir.glob("*.yaml")]
    out.sort(key=lambda e: e["saved_at"], reverse=True)
    return out


def _read_version(config, slug: str, version_id: str) -> str:
    if version_id == "current":
        return get(config, slug)["text"]
    path = _dir(config) / "history" / slug / f"{version_id}.yaml"
    if not path.exists():
        raise StoreError(f"unknown version: {version_id!r}")
    return path.read_text(encoding="utf-8")


def diff(config, slug: str, a: str, b: str) -> dict:
    text_a = _read_version(config, slug, a)
    text_b = _read_version(config, slug, b)
    lines = list(difflib.unified_diff(text_a.splitlines(keepends=True), text_b.splitlines(keepends=True),
                                       fromfile=f"{slug}@{a}", tofile=f"{slug}@{b}"))
    return {"slug": slug, "a": a, "b": b, "diff": "".join(lines)}


# ---- interop with the item-based dashboard -----------------------------------

def _dim_column(mcfg: dict, name: Optional[str]) -> Optional[str]:
    if not name:
        return None
    if name == mcfg.get("time_dimension"):
        return name
    return (mcfg.get("dimensions") or {}).get(name, {}).get("column")


def export_to_items(services, doc: dict, filter_values: Optional[dict] = None) -> dict:
    """Render `doc` (best effort) into the legacy item-based dashboard, so it
    can be opened from the plain Dashboards page too. Metric widgets map
    exactly (a KPI item's expr is arbitrary SQL, same as a semantic metric's
    'expr'). A chart widget maps only when its metric is a plain
    AGG(column) over a real dataset column and its x/color are plain-column
    dimensions — anything more expressive (a derived/offset metric, an expr
    dimension like a month bucket) is skipped with a warning rather than
    silently faked."""
    semantic_doc = semantic_mod.load(services.config)
    model = doc.get("model")
    mcfg = semantic_mod.get_model(semantic_doc, model)
    dataset = mcfg.get("dataset")
    items: list[dict] = []
    warnings: list[str] = []

    for tab in doc.get("tabs") or []:
        for row in tab.get("rows") or []:
            for widget in row.get("widgets") or []:
                wtype = widget.get("type")
                if wtype == "metric" and dataset:
                    mdef = (mcfg.get("metrics") or {}).get(widget.get("metric"), {})
                    if mdef.get("expr"):
                        items.append({"type": "kpi", "dataset": dataset, "expr": mdef["expr"],
                                       "label": widget.get("title") or mdef.get("label", widget.get("metric"))})
                    else:
                        warnings.append(f"skipped a derived metric widget ({widget.get('metric')}) with no base expr")
                elif wtype == "chart" and dataset:
                    mdef = (mcfg.get("metrics") or {}).get(widget.get("y"), {})
                    m = _AGG_RE.match(mdef.get("expr", ""))
                    x_col = _dim_column(mcfg, widget.get("x"))
                    color_col = _dim_column(mcfg, widget.get("color")) if widget.get("color") else None
                    if m and x_col:
                        agg, ycol = m.group(1).lower(), m.group(2)
                        try:
                            chart = services.chart_create(dataset, widget["kind"], x=x_col, y=ycol, agg=agg,
                                                            color=color_col, title=widget.get("title") or "",
                                                            source="agent")
                            items.append({"type": "chart", "chart_id": chart["chart_id"]})
                        except Exception as exc:  # noqa: BLE001
                            warnings.append(f"could not export chart widget: {exc}")
                    else:
                        warnings.append(f"skipped a chart widget that isn't a plain aggregate over a raw column ({widget.get('y')})")

    dash = services.dashboard_create(doc.get("name") or "Untitled dashboard", items=items, source="agent")
    return {**dash, "exported_items": len(items), "warnings": warnings}


def import_from_items(services, dashboard_id: int) -> dict:
    """Best-effort YAML from an existing item dashboard: since item KPIs/
    charts aren't tied to a semantic model, each becomes a static text
    widget carrying its current value/name — a starting point to hand-wire
    into real metric/chart widgets against a semantic model, not a live
    dashboard on its own."""
    rendered = services.dashboard_get(dashboard_id)
    widgets = []
    for item in rendered.get("items") or []:
        if item.get("type") == "kpi":
            widgets.append({"type": "text", "title": item.get("label"),
                             "markdown": f"**{item.get('label') or item.get('expr')}**: {item.get('value')}"})
        elif item.get("type") == "chart":
            chart = item.get("chart") or {}
            widgets.append({"type": "text", "title": chart.get("name"),
                             "markdown": f"Imported chart **{chart.get('name', '')}** — recreate as a live chart "
                                          "widget once its dataset has a semantic model."})
    doc = {"version": 1, "name": rendered.get("name"), "model": None,
           "tabs": [{"name": "Imported", "rows": [{"cols": 1, "widgets": [w]} for w in widgets]}]}
    text = yaml.safe_dump(doc, sort_keys=False, allow_unicode=True)
    return {"doc": doc, "text": text,
            "warnings": ["best-effort import: items became static text; set 'model' to a semantic model and "
                          "rebuild metric/chart widgets for a live dashboard"]}

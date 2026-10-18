"""Build a Vega-Lite spec from a query result and render it to PNG.

Rendering is fully offline via `vl-convert-python` (a Rust Vega engine
bundled as wheels, no browser or network involved).
"""
from __future__ import annotations

from pathlib import Path
from typing import Any, Optional

import vl_convert as vlc

from .data import Catalog, DataError

__all__ = ["build_chart", "ChartError"]

MAX_CHART_ROWS = 5000
MAX_PNG_BYTES = 200_000


def _short(exc: Exception) -> str:
    text = str(exc).strip().splitlines()[0] if str(exc).strip() else type(exc).__name__
    return text[:300]
_KIND_TO_MARK = {
    "bar": "bar",
    "line": "line",
    "area": "area",
    "scatter": "point",
    "histogram": "bar",
    "pie": "arc",
    "heatmap": "rect",
}


class ChartError(ValueError):
    pass


def _field_type(columns: list[dict], field: Optional[str]) -> str:
    if not field:
        return "nominal"
    for c in columns:
        if c["name"] == field:
            t = c["type"].upper()
            if any(k in t for k in ("INT", "DOUBLE", "FLOAT", "DECIMAL", "REAL", "NUMERIC")):
                return "quantitative"
            if "DATE" in t or "TIME" in t:
                return "temporal"
            return "nominal"
    return "nominal"


def build_chart(
    catalog: Catalog,
    sql: str,
    kind: str,
    x: str,
    y: Optional[str] = None,
    color: Optional[str] = None,
    title: Optional[str] = None,
    out_path: Optional[Path] = None,
) -> dict[str, Any]:
    if kind not in _KIND_TO_MARK:
        raise ChartError(f"unknown chart kind: {kind}; choose one of {sorted(_KIND_TO_MARK)}")
    result = catalog.query_all(sql, max_rows=MAX_CHART_ROWS)
    if result["row_count"] == 0:
        raise ChartError("query returned no rows to chart")
    columns = result["columns"]
    field_names = {c["name"] for c in columns}
    if x not in field_names:
        raise ChartError(f"x field {x!r} is not in the query result columns {sorted(field_names)}")
    if kind == "scatter" and not y:
        raise ChartError("scatter needs both x and y (two numeric columns of the query result)")
    if color and color not in field_names:
        raise ChartError(f"color field {color!r} is not in the query result columns {sorted(field_names)}")
    if y and y not in field_names:
        raise ChartError(f"y field {y!r} is not in the query result columns {sorted(field_names)}")

    encoding: dict[str, Any] = {
        "x": {"field": x, "type": _field_type(columns, x), "title": x},
    }
    if kind == "pie":
        if not y:
            raise ChartError("pie needs y (the measure) as well as x (the category)")
        encoding = {
            "theta": {"field": y, "type": "quantitative"},
            "color": {"field": x, "type": "nominal"},
        }
    elif kind == "histogram":
        encoding = {
            "x": {"field": x, "type": "quantitative", "bin": True, "title": x},
            "y": {"aggregate": "count", "title": "count"},
        }
    elif kind == "heatmap":
        if not y:
            raise ChartError("heatmap needs both x and y")
        encoding = {
            "x": {"field": x, "type": _field_type(columns, x)},
            "y": {"field": y, "type": _field_type(columns, y)},
            "color": {"aggregate": "count", "type": "quantitative"},
        }
    else:
        if y:
            encoding["y"] = {"field": y, "type": _field_type(columns, y), "title": y}
        else:  # bar/line/area of x alone: count rows per x value
            encoding["y"] = {"aggregate": "count", "type": "quantitative", "title": "count"}
        if color:
            encoding["color"] = {"field": color, "type": _field_type(columns, color), "title": color}

    # Vega-Lite's default for a nominal field is to sort its axis/legend
    # alphabetically, which silently reorders a bar chart's categories away
    # from whatever order the SQL query put them in (e.g. ORDER BY total
    # DESC) - "sort: null" keeps the order the data already arrived in.
    for enc in encoding.values():
        if enc.get("type") == "nominal" and "field" in enc:
            enc["sort"] = None

    spec: dict[str, Any] = {
        "$schema": "https://vega.github.io/schema/vega-lite/v5.json",
        "data": {"values": result["rows"]},
        "mark": {"type": _KIND_TO_MARK[kind], "tooltip": True},
        "encoding": encoding,
        "width": 480,
        "height": 320,
    }
    if title:
        spec["title"] = title

    try:
        png_bytes = vlc.vegalite_to_png(vl_spec=spec, scale=2)
        if len(png_bytes) > MAX_PNG_BYTES:  # keep the image a model receives small
            png_bytes = vlc.vegalite_to_png(vl_spec=spec, scale=1)
    except Exception as exc:  # noqa: BLE001
        raise ChartError(f"could not render chart: {_short(exc)}") from exc

    saved_path = None
    if out_path is not None:
        out_path.parent.mkdir(parents=True, exist_ok=True)
        out_path.write_bytes(png_bytes)
        saved_path = str(out_path)

    return {
        "spec": spec,
        "png_bytes": png_bytes,
        "png_path": saved_path,
        "row_count": result["row_count"],
        "total_rows": result.get("total_rows"),
        "truncated": result["truncated"],
    }

"""Chart data: aggregated in SQL (never a whole table pulled into Python),
rendered two ways — a Vega-Lite spec for the interactive client (vega-embed,
see DESIGN.md for why) and a PNG via matplotlib (Agg backend, server-side,
for the agent and for dashboards/exports). Bar, grouped/stacked bar, line,
area, scatter (+ trend line), histogram, box, heatmap, pie/donut.
"""

from __future__ import annotations

import io
from dataclasses import dataclass, field
from typing import Any, Optional

import matplotlib

matplotlib.use("Agg")  # headless everywhere, including Windows: never opens a GUI window
import matplotlib.pyplot as plt
import numpy as np
from matplotlib.colors import LinearSegmentedColormap
from matplotlib.ticker import FuncFormatter

from ..hoard_link.atomic import write_bytes_atomic
from .engine import Engine, is_numeric_type, is_temporal_type, json_safe, q

__all__ = ["ChartError", "CHART_KINDS", "build_chart_query", "render_png", "to_vega_lite"]

CHART_KINDS = ("bar", "grouped_bar", "stacked_bar", "line", "area", "scatter", "histogram", "box", "heatmap", "pie", "donut")
MAX_CHART_ROWS = 5000
ACCENT = "#7a1f4a"
GOLD = "#e0a048"
# Rose and gold alternate so adjacent categories in a legend or a stacked bar
# stay visually distinct instead of blurring into one another the way an
# all-rose ramp would for anything beyond two series.
PALETTE = ["#7a1f4a", "#e0a048", "#c94f7c", "#8a6a1f", "#4a1330", "#e0679a", "#9c3b64", "#c98a2c"]
# Shared look for every chart: brand colors plus a muted ink/axis palette so a
# chart reads as part of the same app whether it came from the Charts page,
# a dashboard tile, or an exported PNG.
VEGA_CONFIG = {
    "background": "transparent",
    "font": "system-ui, -apple-system, 'Segoe UI', sans-serif",
    "range": {"category": PALETTE},
    "numberFormat": ",.2f",
    # A single-series bar/line/area/scatter mark has no "color" encoding
    # channel, so it never touches range.category — without this it falls
    # back to Vega's own default blue instead of the brand rose.
    "mark": {"color": ACCENT},
    "axis": {"labelColor": "#6b5560", "titleColor": "#241419", "gridColor": "#00000014",
              "domainColor": "#00000030", "tickColor": "#00000030", "labelFontSize": 10.5},
    "legend": {"labelColor": "#6b5560", "titleColor": "#241419"},
    "view": {"stroke": "transparent"},
    "title": {"color": "#241419", "fontSize": 13, "fontWeight": 600},
}
# The matplotlib heatmap path has no "scheme" concept — build the same
# rose-to-gold ramp by hand so a PNG export matches the interactive chart.
_HEATMAP_CMAP = LinearSegmentedColormap.from_list("nightingale_heat", [ACCENT, GOLD])


class ChartError(ValueError):
    pass


@dataclass
class ChartSpec:
    kind: str
    dataset: str
    x: Optional[str] = None
    y: Optional[str] = None
    agg: str = "sum"
    color: Optional[str] = None
    filter: Optional[str] = None
    bins: int = 20
    title: str = ""
    limit: int = MAX_CHART_ROWS

    @classmethod
    def from_dict(cls, d: dict) -> "ChartSpec":
        return cls(**{k: v for k, v in d.items() if k in cls.__dataclass_fields__})

    def to_dict(self) -> dict:
        return {k: getattr(self, k) for k in self.__dataclass_fields__}


_AGG_SQL = {"sum": "SUM", "avg": "AVG", "count": "COUNT", "min": "MIN", "max": "MAX", "median": "MEDIAN"}


def build_chart_query(spec: ChartSpec, table: str) -> str:
    where = f" WHERE {spec.filter}" if spec.filter else ""
    agg_fn = _AGG_SQL.get(spec.agg, "SUM")

    if spec.kind == "histogram":
        if not spec.x:
            raise ChartError("histogram needs 'x' (a numeric column)")
        return (
            f"WITH m AS (SELECT MIN({q(spec.x)}) AS lo, MAX({q(spec.x)}) AS hi FROM {q(table)}{where}) "
            f"SELECT m.lo + (bin * (m.hi - m.lo) / {spec.bins}.0) AS bin_start, COUNT(*) AS count "
            f"FROM {q(table)}, m, (SELECT LEAST({spec.bins - 1}, FLOOR(({q(spec.x)} - m.lo) / "
            f"NULLIF(m.hi - m.lo, 0) * {spec.bins}))::INT AS bin) t WHERE {q(spec.x)} IS NOT NULL{where.replace('WHERE', 'AND') if where else ''} "
            f"GROUP BY bin, m.lo, m.hi ORDER BY bin"
        )
    if spec.kind == "box":
        if not spec.y:
            raise ChartError("box needs 'y' (a numeric column); 'x' optionally groups it")
        group = f"{q(spec.x)}, " if spec.x else ""
        group_by = f" GROUP BY {q(spec.x)}" if spec.x else ""
        return (
            f"SELECT {group}MIN({q(spec.y)}) AS min, QUANTILE_CONT({q(spec.y)}, 0.25) AS q1, "
            f"MEDIAN({q(spec.y)}) AS median, QUANTILE_CONT({q(spec.y)}, 0.75) AS q3, MAX({q(spec.y)}) AS max "
            f"FROM {q(table)}{where}{group_by}"
        )
    if spec.kind == "heatmap":
        if not (spec.x and spec.y):
            raise ChartError("heatmap needs 'x' and 'y'")
        value_col = spec.color
        agg = f"{agg_fn}({q(value_col)})" if value_col else "COUNT(*)"
        return f"SELECT {q(spec.x)} AS x, {q(spec.y)} AS y, {agg} AS value FROM {q(table)}{where} GROUP BY {q(spec.x)}, {q(spec.y)}"
    if spec.kind == "scatter":
        if not (spec.x and spec.y):
            raise ChartError("scatter needs 'x' and 'y'")
        cols = f"{q(spec.x)} AS x, {q(spec.y)} AS y" + (f", {q(spec.color)} AS color" if spec.color else "")
        return f"SELECT {cols} FROM {q(table)}{where} USING SAMPLE {min(spec.limit, MAX_CHART_ROWS)} ROWS (reservoir, 42)"
    if spec.kind in ("pie", "donut"):
        if not spec.x:
            raise ChartError(f"{spec.kind} needs 'x' (category)")
        agg = f"{agg_fn}({q(spec.y)})" if spec.y else "COUNT(*)"
        return f"SELECT {q(spec.x)} AS label, {agg} AS value FROM {q(table)}{where} GROUP BY {q(spec.x)} ORDER BY value DESC LIMIT 20"
    # bar / grouped_bar / stacked_bar / line / area
    if not spec.x:
        raise ChartError(f"{spec.kind} needs 'x'")
    agg = f"{agg_fn}({q(spec.y)})" if spec.y else "COUNT(*)"
    if spec.color:
        return (
            f"SELECT {q(spec.x)} AS x, {q(spec.color)} AS series, {agg} AS y FROM {q(table)}{where} "
            f"GROUP BY {q(spec.x)}, {q(spec.color)} ORDER BY x"
        )
    return f"SELECT {q(spec.x)} AS x, {agg} AS y FROM {q(table)}{where} GROUP BY {q(spec.x)} ORDER BY x"


def run_chart(engine: Engine, dataset_table: str, spec: ChartSpec) -> dict:
    sql = build_chart_query(spec, dataset_table)
    result = engine.query(sql, limit=MAX_CHART_ROWS, cell_cap=None)
    if result["row_count"] == 0:
        raise ChartError("query returned no rows to chart")
    return result


# d3-format tokens (the client applies the actual locale's digit-group and
# decimal characters via vega-embed's formatLocale, based on the UI language —
# these strings just say "group by thousands" and how many decimals to keep).
_COUNT_FMT = ",d"
_VALUE_FMT = ",.2f"


def _agg_format(agg: Optional[str], has_y: bool) -> str:
    """A bare COUNT(*) (no y column, or agg == 'count') is always an integer;
    anything aggregated from a real value column keeps two decimals."""
    if not has_y or agg == "count":
        return _COUNT_FMT
    return _VALUE_FMT


def to_vega_lite(spec: ChartSpec, data: dict) -> dict:
    """A Vega-Lite v5 spec the client renders with vega-embed (see DESIGN.md).
    Colors and typography come from the shared VEGA_CONFIG theme so every
    chart — Charts page, dashboard tile, or PNG export — looks like the same
    app; number fields carry a d3-format token so thousands separators and
    the decimal mark follow the UI's language once vega-embed applies its
    locale (see VegaChart.jsx)."""
    values = data["rows"]
    base = {"$schema": "https://vega.github.io/schema/vega-lite/v5.json", "data": {"values": values},
            "width": "container", "height": 320, "config": dict(VEGA_CONFIG)}
    if spec.title:
        base["title"] = spec.title
    if spec.kind == "histogram":
        base["mark"] = {"type": "bar", "color": ACCENT, "tooltip": True}
        base["encoding"] = {"x": {"field": "bin_start", "type": "quantitative", "title": spec.x, "format": _VALUE_FMT},
                             "y": {"field": "count", "type": "quantitative", "title": "count", "format": _COUNT_FMT}}
    elif spec.kind == "box":
        base["mark"] = {"type": "boxplot", "extent": "min-max", "color": ACCENT}
        enc = {"y": {"field": "median", "type": "quantitative", "format": _VALUE_FMT}}
        if spec.x:
            enc["x"] = {"field": spec.x, "type": "nominal", "sort": None}
        base["encoding"] = enc
        base["transform"] = []
    elif spec.kind == "heatmap":
        base["mark"] = "rect"
        value_fmt = _agg_format(spec.agg, has_y=bool(spec.color))
        base["encoding"] = {"x": {"field": "x", "type": "nominal", "sort": None},
                             "y": {"field": "y", "type": "nominal", "sort": None},
                             "color": {"field": "value", "type": "quantitative", "format": value_fmt,
                                       "scale": {"range": [VEGA_CONFIG["range"]["category"][0], GOLD]}}}
    elif spec.kind == "scatter":
        base["mark"] = {"type": "point", "filled": True, "tooltip": True}
        enc = {"x": {"field": "x", "type": "quantitative", "format": _VALUE_FMT},
               "y": {"field": "y", "type": "quantitative", "format": _VALUE_FMT}}
        if spec.color:
            enc["color"] = {"field": "color", "type": "nominal"}
        base["encoding"] = enc
    elif spec.kind in ("pie", "donut"):
        base["mark"] = {"type": "arc", "innerRadius": 60 if spec.kind == "donut" else 0, "tooltip": True}
        base["encoding"] = {"theta": {"field": "value", "type": "quantitative", "format": _agg_format(spec.agg, bool(spec.y))},
                             "color": {"field": "label", "type": "nominal", "sort": None}}
    else:
        mark = {"bar": "bar", "grouped_bar": "bar", "stacked_bar": "bar", "line": "line", "area": "area"}[spec.kind]
        x_col = next((c for c in data.get("columns", []) if c["name"] == "x"), None)
        if x_col is not None and is_temporal_type(x_col["type"]):
            # a real date/timestamp axis: let Vega-Lite pick readable ticks
            # instead of drawing one nominal category per distinct date.
            x_enc: dict[str, Any] = {"field": "x", "type": "temporal", "title": spec.x}
        else:
            x_enc = {"field": "x", "type": "nominal", "sort": None, "title": spec.x}
        enc: dict[str, Any] = {"x": x_enc, "y": {"field": "y", "type": "quantitative", "title": spec.y or "count",
                                                   "format": _agg_format(spec.agg, bool(spec.y))}}
        if "series" in (values[0] if values else {}):
            enc["color"] = {"field": "series", "type": "nominal"}
            if spec.kind == "grouped_bar":
                enc["xOffset"] = {"field": "series"}
        base["mark"] = {"type": mark, "tooltip": True}
        base["encoding"] = enc
    return base


def _format_locale_number(value: float, lang: str) -> str:
    """Thousands-grouped, at most 2 decimals, trimmed to a whole number when
    the value is (near enough) one — the matplotlib equivalent of
    Intl.NumberFormat(..., {maximumFractionDigits: 2}). es swaps the
    grouping/decimal marks (3.374.888,14) to match the UI's language."""
    if value is None or value != value:  # None or NaN
        return ""
    rounded = round(float(value), 2)
    s = f"{rounded:,.0f}" if abs(rounded - round(rounded)) < 1e-9 else f"{rounded:,.2f}"
    if lang == "es":
        s = s.translate(str.maketrans({",": "\u0000", ".": ","})).replace("\u0000", ".")
    return s


def _locale_formatter(lang: str) -> FuncFormatter:
    return FuncFormatter(lambda v, _pos: _format_locale_number(v, lang))


def render_png(spec: ChartSpec, data: dict, lang: str = "en", out_path=None) -> bytes:
    rows = data["rows"]
    fig, ax = plt.subplots(figsize=(6, 4), dpi=130)
    fig.patch.set_facecolor("white")
    try:
        _draw(ax, spec, rows, lang)
        ax.set_title(spec.title or spec.dataset, fontsize=11)
        fig.tight_layout()
        buf = io.BytesIO()
        fig.savefig(buf, format="png")
        png_bytes = buf.getvalue()
    finally:
        plt.close(fig)
    if out_path is not None:
        out_path.parent.mkdir(parents=True, exist_ok=True)
        write_bytes_atomic(out_path, png_bytes)
    return png_bytes


def _thin_labels(labels: list[str], max_labels: int = 15) -> list[str]:
    """Blank most of a long category axis's labels so they don't overlap into
    an unreadable smear — matplotlib has no auto tick-thinning for a purely
    categorical axis the way Vega-Lite does for a temporal one."""
    n = len(labels)
    if n <= max_labels:
        return labels
    step = max(1, round(n / max_labels))
    return [lab if i % step == 0 else "" for i, lab in enumerate(labels)]


def _draw(ax, spec: ChartSpec, rows: list[dict], lang: str = "en") -> None:
    fmt = _locale_formatter(lang)
    if spec.kind == "histogram":
        xs = [r["bin_start"] for r in rows]
        ys = [r["count"] for r in rows]
        ax.bar(xs, ys, color=ACCENT, width=(xs[1] - xs[0]) * 0.9 if len(xs) > 1 else 1)
        ax.set_xlabel(spec.x)
        ax.set_ylabel("count")
        ax.xaxis.set_major_formatter(fmt)
        ax.yaxis.set_major_formatter(fmt)
    elif spec.kind == "box":
        stats = []
        for r in rows:
            stats.append({"med": r["median"], "q1": r["q1"], "q3": r["q3"], "whislo": r["min"], "whishi": r["max"]})
        ax.bxp(stats, showfliers=False, patch_artist=True,
               boxprops={"facecolor": ACCENT, "alpha": 0.6}, medianprops={"color": "black"})
        if spec.x:
            labels = [str(r.get(spec.x, i)) for i, r in enumerate(rows)]
            ax.set_xticklabels(labels, rotation=30, ha="right")
        ax.yaxis.set_major_formatter(fmt)
    elif spec.kind == "heatmap":
        xs = sorted({r["x"] for r in rows}, key=str)
        ys = sorted({r["y"] for r in rows}, key=str)
        grid = np.zeros((len(ys), len(xs)))
        xi = {v: i for i, v in enumerate(xs)}
        yi = {v: i for i, v in enumerate(ys)}
        for r in rows:
            grid[yi[r["y"]], xi[r["x"]]] = r["value"] or 0
        im = ax.imshow(grid, cmap=_HEATMAP_CMAP, aspect="auto")
        ax.set_xticks(range(len(xs)))
        ax.set_xticklabels([str(v) for v in xs], rotation=45, ha="right", fontsize=7)
        ax.set_yticks(range(len(ys)))
        ax.set_yticklabels([str(v) for v in ys], fontsize=7)
        cbar = plt.colorbar(im, ax=ax, fraction=0.046)
        cbar.ax.yaxis.set_major_formatter(fmt)
    elif spec.kind == "scatter":
        xs = np.array([r["x"] for r in rows], dtype=float)
        ys = np.array([r["y"] for r in rows], dtype=float)
        if "color" in (rows[0] if rows else {}):
            groups = sorted({r["color"] for r in rows}, key=str)
            for i, g in enumerate(groups):
                mask = [r["color"] == g for r in rows]
                ax.scatter(xs[mask], ys[mask], s=14, alpha=0.7, color=PALETTE[i % len(PALETTE)], label=str(g))
            ax.legend(fontsize=7)
        else:
            ax.scatter(xs, ys, s=14, alpha=0.7, color=ACCENT)
        if len(xs) >= 2 and np.std(xs) > 0:
            coeffs = np.polyfit(xs, ys, 1)
            xs_line = np.linspace(xs.min(), xs.max(), 50)
            ax.plot(xs_line, np.polyval(coeffs, xs_line), color="#4a1330", linewidth=1.5, linestyle="--")
        ax.set_xlabel(spec.x)
        ax.set_ylabel(spec.y)
        ax.xaxis.set_major_formatter(fmt)
        ax.yaxis.set_major_formatter(fmt)
    elif spec.kind in ("pie", "donut"):
        labels = [str(r["label"]) for r in rows]
        values = [r["value"] or 0 for r in rows]
        wedge_props = {"width": 0.4} if spec.kind == "donut" else None
        ax.pie(values, labels=labels, colors=PALETTE * (len(values) // len(PALETTE) + 1),
               autopct="%1.0f%%", wedgeprops=wedge_props, textprops={"fontsize": 7})
        ax.set_aspect("equal")
    else:
        xs = [str(r["x"]) for r in rows]
        if "series" in (rows[0] if rows else {}):
            series_names = sorted({r["series"] for r in rows}, key=str)
            xcats = sorted({r["x"] for r in rows}, key=str)
            width = 0.8 / len(series_names)
            bottom = np.zeros(len(xcats))
            for i, s in enumerate(series_names):
                ys = [next((r["y"] for r in rows if r["x"] == xc and r["series"] == s), 0) for xc in xcats]
                pos = np.arange(len(xcats))
                if spec.kind == "stacked_bar":
                    ax.bar(pos, ys, bottom=bottom, label=str(s), color=PALETTE[i % len(PALETTE)])
                    bottom += np.array(ys, dtype=float)
                elif spec.kind == "line":
                    ax.plot(pos, ys, label=str(s), color=PALETTE[i % len(PALETTE)], marker="o", markersize=3)
                elif spec.kind == "area":
                    ax.fill_between(pos, ys, alpha=0.5, label=str(s), color=PALETTE[i % len(PALETTE)])
                else:
                    ax.bar(pos + i * width, ys, width=width, label=str(s), color=PALETTE[i % len(PALETTE)])
            ax.set_xticks(np.arange(len(xcats)))
            ax.set_xticklabels(_thin_labels([str(x) for x in xcats]), rotation=30, ha="right", fontsize=7)
            ax.legend(fontsize=7)
        else:
            ys = [r["y"] or 0 for r in rows]
            if spec.kind == "line":
                ax.plot(range(len(xs)), ys, color=ACCENT, marker="o", markersize=3)
            elif spec.kind == "area":
                ax.fill_between(range(len(xs)), ys, color=ACCENT, alpha=0.5)
            else:
                ax.bar(range(len(xs)), ys, color=ACCENT)
            ax.set_xticks(range(len(xs)))
            ax.set_xticklabels(_thin_labels(xs), rotation=30, ha="right", fontsize=7)
        ax.set_ylabel(spec.y or "count")
        ax.yaxis.set_major_formatter(fmt)

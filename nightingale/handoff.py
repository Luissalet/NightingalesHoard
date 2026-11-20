"""A chart's figures or a dataset's table, handed to another station with where every figure comes from.

Cicero (a dataset connector) and Writer (a cited table) call ``data_handoff`` through the hub and receive the same
columns and rows Nightingale computed, never a copy typed by an agent. The provenance names the dataset, the version the
figures were read from, the query, the source the dataset was ingested from and a fingerprint of the rows, so the
receiving station can tell later whether the figures it holds are still the current ones.
"""

from __future__ import annotations

import csv
import hashlib
import io
import json
from typing import Any, Optional

from .services import NotFoundError
from .sqlgate import gate_sql
from .workbench.charts import ChartSpec, build_chart_query, run_chart
from .workbench.engine import DataError, q

MAX_ROWS = 2000


def fingerprint(columns: list[str], rows: list[list[Any]]) -> str:
    text = json.dumps([columns, rows], ensure_ascii=False, default=str, separators=(",", ":"))
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


def build(services, chart_id: Optional[int] = None, dataset: Optional[str] = None, columns: Optional[list[str]] = None,
          filter: Optional[str] = None, limit: int = 500, title: str = "") -> dict[str, Any]:
    if (chart_id is None) == (not dataset):
        raise DataError("pass chart_id (a saved chart) or dataset (a table of its current version), not both")
    limit = max(1, min(int(limit), MAX_ROWS))
    chart = None
    if chart_id is not None:
        row = services.meta.get_chart(chart_id)
        if row is None:
            raise NotFoundError(f"unknown chart: {chart_id}")
        spec = ChartSpec.from_dict(json.loads(row["spec_json"]))
        dataset_row = services.meta.get_dataset_by_id(row["dataset_id"])
        version = services._version_row(dataset_row)
        query = build_chart_query(spec, dataset_row["name"])
        data = run_chart(services.engine, version["table_name"], spec)
        chart = {"id": chart_id, "type": spec.kind, "x": spec.x, "y": spec.y, "agg": spec.agg, "color": spec.color}
        title = title or row["name"]
    else:
        dataset_row = services._dataset_row(dataset)
        version = services._version_row(dataset_row)
        known = [c["name"] for c in json.loads(version["columns_json"] or "[]")]
        unknown = [c for c in columns or [] if c not in known]
        if unknown:
            raise DataError(f"unknown column(s) {unknown}; {dataset_row['name']!r} has {known}")
        select = ", ".join(q(c) for c in columns) if columns else "*"
        where = f" WHERE {filter}" if filter else ""
        query = f"SELECT {select} FROM {q(dataset_row['name'])}{where}"
        gate_sql(query)
        # The version's own table, so the figures are the ones of the version named below even if the dataset moves on.
        data = services.engine.query(f"SELECT {select} FROM {q(version['table_name'])}{where}", limit=limit, cell_cap=None)
        title = title or dataset_row["name"]
    names = [c["name"] for c in data["columns"]]
    if chart:
        # The chart query names its columns x, y, label, value...: they go back to the dataset's own names.
        value = (spec.color if spec.kind == "heatmap" else spec.y) or "count"
        aliases = {"x": spec.x, "label": spec.x, "y": spec.y if spec.kind in ("heatmap", "scatter") else (spec.y or "count"),
                   "value": value, "color": spec.color}
        renamed = [aliases.get(n) or n for n in names]
        if len(set(renamed)) == len(renamed):
            names = renamed
            data = {**data, "rows": [dict(zip(renamed, record.values())) for record in data["rows"]]}
    rows = [[record.get(name) for name in names] for record in data["rows"]][:limit]
    buffer = io.StringIO()
    csv.writer(buffer, lineterminator="\n").writerows([names, *rows])
    source = services.meta.get_source(dataset_row["source_id"]) if dataset_row["source_id"] else None
    ref = f"hoard://nightingale/chart/{chart_id}" if chart else f"hoard://nightingale/dataset/{dataset_row['name']}"
    return {
        "kind": "data-chart" if chart else "data-table", "title": title, "columns": names, "rows": rows,
        "row_count": len(rows), "truncated": bool(data.get("truncated")) or len(data["rows"]) > limit,
        "csv": buffer.getvalue(), "chart": chart,
        "provenance": {
            "app": "nightingale", "ref": ref, "dataset": dataset_row["name"], "version": version["version"],
            "version_created_at": version["created_at"], "query": query, "fingerprint": fingerprint(names, rows),
            "source": {"kind": source["kind"], "path": source["path"]} if source else None,
        },
        "_log_summary": f"handed {len(rows)} row(s) of {dataset_row['name']!r} v{version['version']}",
    }

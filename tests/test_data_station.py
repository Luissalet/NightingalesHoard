"""Nightingale as a production station: folders with different columns, the hub as a source, timestamps with a time
zone, and the handoff of a chart or a table with its provenance."""

import asyncio
import csv
import io
import json

from nightingale import handoff, hoard_source
from nightingale.agent_tools import call_tool


def test_folder_of_json_files_with_different_columns_loads_by_column_name(services, tmp_path):
    folder = tmp_path / "timelines"
    folder.mkdir()
    (folder / "t1.json").write_text(json.dumps([{"task": 1, "kind": "submit", "at": "2026-11-20 01:00:00"}]), encoding="utf-8")
    (folder / "t2.json").write_text(json.dumps([{"task": 2, "kind": "review", "verdict": "changes", "revision": 2}]),
                                    encoding="utf-8")
    result = services.ingest_folder(str(folder), "timelines", "*.json")
    assert result["row_count"] == 2
    names = {c["name"] for c in result["columns"]}
    assert {"task", "kind", "at", "verdict", "revision", "_source_file"} <= names
    rows = services.query("SELECT task, verdict FROM timelines ORDER BY task", 10)["rows"]
    assert rows == [{"task": 1, "verdict": None}, {"task": 2, "verdict": "changes"}]


def test_folder_of_csv_files_with_different_columns_loads_by_column_name(services, tmp_path):
    folder = tmp_path / "csvs"
    folder.mkdir()
    (folder / "a.csv").write_text("station,returns\nwriter,2\n", encoding="utf-8")
    (folder / "b.csv").write_text("station,returns,reason\ncicero,1,column contract\n", encoding="utf-8")
    result = services.ingest_folder(str(folder), "returns", "*.csv")
    assert result["row_count"] == 2
    assert {"station", "returns", "reason"} <= {c["name"] for c in result["columns"]}


def test_folder_with_mixed_kinds_says_which_pattern_to_use(services, tmp_path):
    import pytest

    folder = tmp_path / "mixed"
    folder.mkdir()
    (folder / "a.csv").write_text("x\n1\n", encoding="utf-8")
    (folder / "b.json").write_text("[{\"x\": 2}]", encoding="utf-8")
    with pytest.raises(Exception, match="different or unsupported kinds"):
        services.ingest_folder(str(folder), "mixed", "*")


def test_the_hub_is_a_source_under_its_short_names():
    for name in ("hoardhub", "hub", "Hoard-Hub", "hoard_hub"):
        spec = hoard_source.resolve_spec(None, name, "hub_agora_tasks", {"limit": 50}, None, {})
        assert spec["app"] == "hoardhub"


def test_hub_tool_answer_becomes_a_dataset(services, monkeypatch):
    calls = []

    def fake(app, tool, args, timeout):
        calls.append((app, tool, args))
        return {"ok": True, "app": "hoardhub", "tool": tool,
                "result": {"ok": True, "tasks": [{"id": 3509, "status": "review", "returns": 1},
                                                 {"id": 3510, "status": "done", "returns": 0}]}}

    monkeypatch.setattr(hoard_source, "_hub_call", fake)
    result = services.ingest_hoard(app="hub", tool="hub_agora_tasks", args={"limit": 50}, name="tasks")
    assert calls == [("hoardhub", "hub_agora_tasks", {"limit": 50})]
    assert result["row_count"] == 2


def test_to_timestamp_answers(services):
    rows = services.query("SELECT to_timestamp(1795140000) AS at", 1)["rows"]
    assert str(rows[0]["at"]).startswith("2026-11-")


def _returns(services):
    services.ingest_text("station,returns\nwriter,2\ncicero,1\nwriter,3\nhypatia,1\n", "returns", "csv")


def test_handoff_of_a_chart_carries_figures_and_provenance(services):
    _returns(services)
    chart = services.chart_create("returns", "bar", "station", "returns", "sum", None, None, "Returns by station")

    async def run():
        return await call_tool(services, "data_handoff", {"chart_id": chart["chart_id"]})

    out = asyncio.run(run())
    assert out["kind"] == "data-chart" and out["title"] == "Returns by station"
    assert out["chart"]["type"] == "bar" and out["chart"]["x"] == "station" and out["columns"] == ["station", "returns"]
    figures = {row[0]: row[1] for row in out["rows"]}
    assert figures == {"writer": 5, "cicero": 1, "hypatia": 1}
    assert list(csv.reader(io.StringIO(out["csv"])))[0] == out["columns"]
    prov = out["provenance"]
    assert prov["ref"] == f"hoard://nightingale/chart/{chart['chart_id']}" and prov["dataset"] == "returns"
    assert prov["version"] == 0 and prov["fingerprint"] == handoff.fingerprint(out["columns"], out["rows"])
    assert prov["source"]["kind"] == "paste_csv" and "returns" in prov["query"]
    assert any(entry["op"] == "handoff" for entry in services.log_search(None, "agent", None, 10)["entries"])


def test_handoff_of_a_table_pins_the_version_and_changes_its_fingerprint_with_the_data(services):
    _returns(services)
    first = handoff.build(services, dataset="returns", columns=["station", "returns"], filter="returns > 1")
    assert first["kind"] == "data-table" and first["rows"] == [["writer", 2], ["writer", 3]]
    assert first["provenance"]["query"] == 'SELECT "station", "returns" FROM "returns" WHERE returns > 1'
    services.transform_apply("returns", "filter", {"expr": "station <> 'hypatia'"}, source="agent")
    second = handoff.build(services, dataset="returns")
    assert first["provenance"]["version"] == 0 and second["provenance"]["version"] == 1
    assert second["provenance"]["fingerprint"] != handoff.build(services, dataset="returns", columns=["station"])["provenance"]["fingerprint"]


def test_handoff_refuses_ambiguous_or_unknown_input(services):
    import pytest

    _returns(services)
    with pytest.raises(Exception, match="not both"):
        handoff.build(services)
    with pytest.raises(Exception, match="unknown column"):
        handoff.build(services, dataset="returns", columns=["nope"])


def test_manifest_declares_the_data_station():
    from pathlib import Path

    manifest = json.loads((Path(__file__).resolve().parent.parent / "faustus-plugin.json").read_text(encoding="utf-8"))
    handoffs = manifest["x-family"]["production"]["handoffs"]
    station = next(h for h in handoffs if h["accepts"] == "production-brief")
    assert station["produces"] == ["data-table", "data-chart"]
    assert station["skill"] in manifest["skills"]["slugs"]
    assert set(station["check"]) == {"en", "es"} and "data_handoff" in station["check"]["en"]
    assert all(r["id"] and r["es"] and r["en"] for r in station["requires"])

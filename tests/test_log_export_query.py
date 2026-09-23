"""Analysis log ids, export formats, and the read-only query gate."""

import pytest

from nightingale.sqlgate import SQLGateError, gate_sql


@pytest.fixture
def ds(services, tmp_path):
    path = tmp_path / "e.csv"
    path.write_text("a,b\n1,2\n3,4\n", encoding="utf-8")
    services.ingest_file(str(path), "e")
    return "e"


def test_every_operation_gets_a_log_id(services, ds):
    services.transform_apply(ds, "derive", {"name": "c", "expr": "a + b"})
    entries = services.log_search(dataset=ds)["entries"]
    assert len(entries) >= 2
    assert all(e["op_id"].startswith("N-") for e in entries)
    fetched = services.log_get(entries[0]["op_id"])
    assert fetched["op_id"] == entries[0]["op_id"]


def test_failed_operation_is_logged_too(services, ds):
    with pytest.raises(Exception):
        services.transform_apply(ds, "filter", {"expr": "no_such_column > 1"})
    entries = services.log_search(dataset=ds)["entries"]
    assert any(not e["ok"] for e in entries)


def test_source_filter_ui_vs_agent(services, ds):
    services.transform_apply(ds, "derive", {"name": "c", "expr": "1"}, source="agent")
    agent_entries = services.log_search(source="agent")["entries"]
    assert all(e["source"] == "agent" for e in agent_entries)
    assert len(agent_entries) >= 1


@pytest.mark.parametrize("fmt", ["csv", "xlsx", "parquet", "json"])
def test_export_formats(services, ds, fmt):
    result = services.export(ds, fmt)
    from pathlib import Path

    assert Path(result["path"]).exists()
    assert Path(result["path"]).stat().st_size > 0


def test_export_path_cannot_escape_exports_dir(services, ds):
    with pytest.raises(Exception):
        services.export(ds, "csv", "../../evil.csv")


def test_gate_sql_allows_select_rejects_writes():
    gate_sql("SELECT * FROM t")
    gate_sql("WITH x AS (SELECT 1) SELECT * FROM x")
    for bad in ("DELETE FROM t", "DROP TABLE t", "INSERT INTO t VALUES (1)", "SELECT 1; SELECT 2"):
        with pytest.raises(SQLGateError):
            gate_sql(bad)


def test_data_query_runs_over_dataset_view(services, ds):
    result = services.query(f"SELECT SUM(a) AS total FROM {ds}")
    assert result["rows"][0]["total"] == 4

"""Ingesting under a name that is taken must refuse without touching the
existing dataset (it used to drop its version-0 table, then fail with 500)."""
import pytest

from nightingale.workbench.engine import DataError


def test_second_ingest_with_the_same_name_is_refused_and_keeps_the_data(services, tmp_path):
    first = tmp_path / "a.csv"
    first.write_text("x;y\n1;2\n3;4\n", encoding="utf-8")
    second = tmp_path / "b.csv"
    second.write_text("x;y\n9;9\n", encoding="utf-8")
    services.ingest_file(str(first), name="ventas")
    with pytest.raises(DataError, match="already exists"):
        services.ingest_file(str(second), name="ventas")
    rows = services.engine.query('SELECT COUNT(*) AS n FROM "ventas"', limit=1)["rows"][0]["n"]
    assert rows == 2


def test_unique_rule_reports_groups_and_extra_copies(services, tmp_path):
    """12 failing rows are 6 pairs: 6 extra copies, not 12 (an assistant
    reported 12 duplicates and 1,494 rows left)."""
    f = tmp_path / "d.csv"
    rows = ["k;v"] + [f"{i};{i}" for i in range(10)] + ["1;1", "2;2", "2;2"]
    f.write_text("\n".join(rows) + "\n", encoding="utf-8")
    services.ingest_file(str(f), name="dups")
    services.quality_define("dups", "u", "unique", {"columns": ["k", "v"]})
    res = services.quality_run("dups")["results"][0]
    assert res["failed"] == 5            # rows in duplicated groups: 2 + 3
    assert res["duplicate_groups"] == 2
    assert res["extra_copies"] == 3
    assert res["rows_if_deduplicated"] == 10

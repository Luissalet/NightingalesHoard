import csv
from pathlib import Path

import openpyxl
import pytest

from laplaces_hoard.engines.data import Catalog, DataError, SQLGateError


@pytest.fixture()
def catalog(data_dir: Path) -> Catalog:
    return Catalog(data_dir)


def test_register_csv_and_query(catalog, sample_csv):
    meta = catalog.register(str(sample_csv), name="sample")
    assert meta["row_count"] == 6
    r = catalog.query("SELECT region, SUM(amount) AS total FROM sample GROUP BY region ORDER BY total DESC", limit=10)
    assert r["row_count"] == 4
    # amounts: North=220, East=200, South=170, West=60 -> North sorts first desc
    assert r["rows"][0]["region"] == "North"


def test_failed_reregistration_preserves_previous_dataset(catalog, sample_csv, tmp_path):
    catalog.register(str(sample_csv), name="sample")
    broken = tmp_path / "broken.parquet"
    broken.write_text("not a parquet file", encoding="utf-8")
    with pytest.raises(DataError):
        catalog.register(str(broken), name="sample")
    assert catalog.describe("sample")["source_path"] == str(sample_csv.resolve())
    assert catalog.query("SELECT SUM(amount) AS total FROM sample")["rows"][0]["total"] == 650


def test_profile_numbers_are_correct_on_a_known_table(catalog, sample_csv):
    catalog.register(str(sample_csv), name="sample")
    d = catalog.describe("sample")
    profile = d["profile"]["amount"]
    # amounts: 100,120,80,90,200,60 -> min 60 max 200 mean 108.333...
    assert profile["min"] == 60
    assert profile["max"] == 200
    assert profile["mean"] == pytest.approx(108.3333, abs=0.01)
    assert profile["nulls_pct"] == 0.0
    region_profile = d["profile"]["region"]
    assert region_profile["distinct_approx"] == 4
    top_values = {t["value"]: t["count"] for t in region_profile["top_values"]}
    assert top_values["North"] == 2
    assert top_values["South"] == 2


def test_excel_sheet_becomes_a_dataset(catalog, tmp_path):
    xlsx = tmp_path / "book.xlsx"
    wb = openpyxl.Workbook()
    ws1 = wb.active
    ws1.title = "Sheet1"
    ws1.append(["id", "name", "score"])
    for i in range(5):
        ws1.append([i, f"item{i}", i * 1.5])
    ws2 = wb.create_sheet("Sheet2")
    ws2.append(["x", "y"])
    ws2.append([1, 2])
    wb.save(xlsx)

    result = catalog.register(str(xlsx), name="book")
    assert set(result["sheets"]) == {"book__Sheet1", "book__Sheet2"}
    d1 = catalog.describe("book__Sheet1")
    assert d1["row_count"] == 5
    assert {c["name"] for c in d1["columns"]} == {"id", "name", "score"}


def test_sqlite_table_becomes_a_dataset(catalog, tmp_path):
    import sqlite3
    db_path = tmp_path / "mini.sqlite"
    conn = sqlite3.connect(db_path)
    conn.execute("CREATE TABLE users (id INTEGER, name TEXT)")
    conn.executemany("INSERT INTO users VALUES (?, ?)", [(1, "Ana"), (2, "Luis")])
    conn.commit()
    conn.close()

    result = catalog.register(str(db_path), name="mini")
    assert result["tables"] == ["mini__users"]
    d = catalog.describe("mini__users")
    assert d["row_count"] == 2


def test_folder_glob_registers_multiple_csvs(catalog, tmp_path):
    folder = tmp_path / "shards"
    folder.mkdir()
    for i in range(3):
        with (folder / f"part{i}.csv").open("w", newline="") as fh:
            w = csv.writer(fh)
            w.writerow(["n"])
            w.writerow([i])
    result = catalog.register(str(folder), name="shards", options={"glob": "*.csv"})
    d = catalog.describe(result["name"])
    assert d["row_count"] == 3


# -- SQL gate: the required "refuses COPY/ATTACH/INSTALL/multi-statement" test --

@pytest.mark.parametrize(
    "bad_sql",
    [
        "ATTACH ':memory:' AS x",
        "COPY sample TO 'out.csv'",
        "INSTALL httpfs",
        "LOAD httpfs",
        "SELECT 1; SELECT 2",
        "PRAGMA table_info(sample)",
        "CREATE TABLE evil AS SELECT 1",
        "INSERT INTO sample VALUES ('x', 1)",
        "UPDATE sample SET amount = 0",
        "DELETE FROM sample",
        "CALL pragma_table_info('sample')",
        "DROP TABLE sample",
        "EXPORT DATABASE 'out'",
    ],
)
def test_sql_gate_refuses_dangerous_statements(catalog, sample_csv, bad_sql):
    catalog.register(str(sample_csv), name="sample")
    with pytest.raises(SQLGateError):
        catalog.query(bad_sql)


def test_sql_gate_allows_describe_summarize_and_pivot(catalog, sample_csv):
    catalog.register(str(sample_csv), name="sample")
    assert catalog.query("DESCRIBE sample")["row_count"] > 0
    assert catalog.query("SUMMARIZE sample")["row_count"] > 0
    assert catalog.query("PIVOT sample ON region USING sum(amount)")["row_count"] > 0


def test_query_data_is_untouched_after_gated_query(catalog, sample_csv):
    catalog.register(str(sample_csv), name="sample")
    catalog.query("SELECT * FROM sample")
    assert catalog.describe("sample")["row_count"] == 6


def test_query_limit_and_truncation(catalog, sample_csv):
    catalog.register(str(sample_csv), name="sample")
    r = catalog.query("SELECT * FROM sample", limit=2)
    assert r["row_count"] == 2
    assert r["truncated"] is True


def test_unknown_dataset_raises_data_error(catalog):
    with pytest.raises(DataError):
        catalog.describe("does_not_exist")

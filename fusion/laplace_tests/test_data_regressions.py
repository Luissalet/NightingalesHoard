"""Regressions found in review: connection model, names, errors, row caps."""
import csv
from pathlib import Path

import openpyxl
import pytest

from laplaces_hoard.engines import stats
from laplaces_hoard.engines.data import Catalog, DataError, slugify_name


def _write_csv(path: Path, n: int) -> Path:
    with path.open("w", newline="", encoding="utf-8") as fh:
        w = csv.writer(fh)
        w.writerow(["grp", "value"])
        for i in range(n):
            w.writerow(["a" if i % 2 else "b", i])
    return path


def test_register_still_works_after_a_query(tmp_path):
    # enable_external_access cannot be re-enabled once off: registration must
    # not depend on toggling it on the query connection.
    cat = Catalog(tmp_path / "data")
    cat.register(str(_write_csv(tmp_path / "one.csv", 3)))
    cat.query("SELECT * FROM one")
    meta = cat.register(str(_write_csv(tmp_path / "two.csv", 4)))
    assert meta["row_count"] == 4
    assert cat.query("SELECT COUNT(*) AS n FROM two")["rows"][0]["n"] == 4


def test_query_cannot_read_arbitrary_files(tmp_path):
    cat = Catalog(tmp_path / "data")
    secret = tmp_path / "secret.csv"
    secret.write_text("password\nhunter2\n", encoding="utf-8")
    with pytest.raises(DataError, match="register the file"):
        cat.query(f"SELECT * FROM read_csv('{secret.as_posix()}')")


def test_query_cannot_write_to_catalog(tmp_path):
    cat = Catalog(tmp_path / "data")
    cat.register(str(_write_csv(tmp_path / "t.csv", 3)))
    # PIVOT is allowed through the gate and must still work on the read-only connection
    assert cat.query("PIVOT t ON grp USING sum(value)")["row_count"] == 1
    assert cat.describe("t")["row_count"] == 3


def test_sql_errors_are_data_errors_with_duckdb_hint(tmp_path):
    cat = Catalog(tmp_path / "data")
    cat.register(str(_write_csv(tmp_path / "t.csv", 3)))
    with pytest.raises(DataError, match="nope"):
        cat.query("SELECT nope FROM t")


def test_file_names_with_spaces_and_accents_get_safe_dataset_names(tmp_path):
    cat = Catalog(tmp_path / "data")
    meta = cat.register(str(_write_csv(tmp_path / "Ventas año 2024.csv", 2)))
    assert meta["name"] == "Ventas_ano_2024"
    # the human-typed name resolves to the same dataset
    assert cat.describe("Ventas año 2024")["name"] == "Ventas_ano_2024"
    assert cat.describe("ventas_ano_2024")["row_count"] == 2


def test_slugify_name():
    assert slugify_name("Ventas 2024") == "Ventas_2024"
    assert slugify_name("2024 sales") == "t_2024_sales"
    assert slugify_name("book__Sheet1") == "book__Sheet1"
    assert slugify_name("###") == "dataset"


def test_excel_sheet_with_spaces_and_accents(tmp_path):
    xlsx = tmp_path / "Informe.xlsx"
    wb = openpyxl.Workbook()
    ws = wb.active
    ws.title = "Año 2024"
    ws.append(["mes", "importe"])
    ws.append(["enero", 10])
    wb.save(xlsx)
    cat = Catalog(tmp_path / "data")
    result = cat.register(str(xlsx))
    assert result["sheets"] == ["Informe__Ano_2024"]


def test_unknown_dataset_error_lists_registered_names(tmp_path):
    cat = Catalog(tmp_path / "data")
    cat.register(str(_write_csv(tmp_path / "sales.csv", 2)))
    with pytest.raises(DataError, match="sales"):
        cat.describe("sale")


def test_total_rows_reported_when_truncated(tmp_path):
    cat = Catalog(tmp_path / "data")
    cat.register(str(_write_csv(tmp_path / "t.csv", 2500)))
    r = cat.query("SELECT * FROM t", limit=10)
    assert r["row_count"] == 10
    assert r["truncated"] is True
    assert r["total_rows"] == 2500


def test_stats_on_a_dataset_column_uses_every_row(tmp_path):
    # the public query() caps at 1000 rows; stats must not silently inherit that cap
    cat = Catalog(tmp_path / "data")
    cat.register(str(_write_csv(tmp_path / "t.csv", 3000)))
    r = stats.run("describe", dataset="t", column="value", catalog=cat)
    assert r["n"] == 3000
    assert r["max"] == 2999
    g = stats.run("ttest_ind", dataset="t", column="value", group_by="grp", catalog=cat)
    assert g["n1"] + g["n2"] == 3000


def test_catalog_under_a_path_with_an_apostrophe(tmp_path):
    root = tmp_path / "Laplace's Hoard"
    root.mkdir()
    cat = Catalog(root / "data")
    meta = cat.register(str(_write_csv(root / "it's.csv", 3)))
    assert meta["row_count"] == 3
    xlsx = root / "book's.xlsx"
    wb = openpyxl.Workbook()
    wb.active.append(["a"])
    wb.active.append([1])
    wb.save(xlsx)
    assert cat.register(str(xlsx))["sheets"]


def test_long_text_cells_are_truncated_in_query_results(tmp_path):
    path = tmp_path / "notes.csv"
    with path.open("w", newline="", encoding="utf-8") as fh:
        w = csv.writer(fh)
        w.writerow(["note"])
        w.writerow(["x" * 5000])
    cat = Catalog(tmp_path / "data")
    cat.register(str(path))
    cell = cat.query("SELECT note FROM notes")["rows"][0]["note"]
    assert len(cell) < 600 and cell.endswith("…")


def test_demo_seed_under_apostrophe_path_then_app_can_register(tmp_path):
    # the Windows install folder is "Laplace's Hoard"; demo seeding must survive it,
    # and the app's own Catalog must be able to register afterwards.
    from laplaces_hoard.demo import seed_demo_data

    root = tmp_path / "Laplace's Hoard" / "data-demo"
    seed_demo_data(root, root / "files")
    cat = Catalog(root)
    names = {d["name"] for d in cat.list_datasets()}
    assert {"sales", "sensor_readings", "hr__Employees", "hr__Departments"} <= names
    assert cat.query("SELECT COUNT(*) AS n FROM sales")["rows"][0]["n"] == 5000
    extra = _write_csv(tmp_path / "extra.csv", 2)
    assert cat.register(str(extra))["row_count"] == 2


def test_json_and_ndjson_register_without_extension_downloads(tmp_path):
    (tmp_path / "rows.json").write_text('[{"a": 1, "b": "x"}, {"a": 2, "b": "y"}]', encoding="utf-8")
    (tmp_path / "rows.ndjson").write_text('{"a": 1}\n{"a": 2}\n{"a": 3}\n', encoding="utf-8")
    cat = Catalog(tmp_path / "data")
    assert cat.register(str(tmp_path / "rows.json"))["row_count"] == 2
    assert cat.register(str(tmp_path / "rows.ndjson"), name="nd")["row_count"] == 3


def test_queries_never_try_to_install_extensions(tmp_path):
    cat = Catalog(tmp_path / "data")
    with pytest.raises(DataError, match="not in the catalog"):
        cat.query("SELECT * FROM sqlite_scan('x.db', 'y')")
    r = cat.query("SELECT current_setting('autoinstall_known_extensions') AS v")
    assert r["rows"][0]["v"] is False


def test_dataset_count_does_not_wait_behind_a_busy_catalog(tmp_path):
    import threading
    import time as _time

    cat = Catalog(tmp_path / "data")
    cat.register(str(_write_csv(tmp_path / "t.csv", 3)))
    assert cat.dataset_count() == 1
    release = threading.Event()

    def hold():
        with cat._lock:
            release.wait(5)

    th = threading.Thread(target=hold)
    th.start()
    _time.sleep(0.05)
    start = _time.monotonic()
    assert cat.dataset_count() == 1  # cached value, answered quickly
    assert _time.monotonic() - start < 1
    release.set()
    th.join()


def test_linked_datasets_are_queryable_through_the_file_access_connection(tmp_path, monkeypatch):
    from laplaces_hoard.engines import data as data_mod

    monkeypatch.setattr(data_mod, "LINK_THRESHOLD_BYTES", 0)  # treat every file as "big"
    cat = Catalog(tmp_path / "data")
    meta = cat.register(str(_write_csv(tmp_path / "big.csv", 50)))
    assert meta["linked"] is True and meta["row_count"] == 50
    assert cat.query("SELECT SUM(value) AS s FROM big")["rows"][0]["s"] == sum(range(50))
    # only the linked source is readable: any other file stays blocked, even in
    # the same query as the linked dataset
    secret = tmp_path / "secret.csv"
    secret.write_text("password\nhunter2\n", encoding="utf-8")
    with pytest.raises(DataError, match="register the file"):
        cat.query(f"SELECT * FROM big, read_csv('{secret.as_posix()}')")
    assert cat.describe("big")["sample_rows"]


def test_linked_folder_dataset_is_queryable(tmp_path, monkeypatch):
    from laplaces_hoard.engines import data as data_mod

    monkeypatch.setattr(data_mod, "LINK_THRESHOLD_BYTES", 0)
    folder = tmp_path / "shards"
    folder.mkdir()
    for i in range(3):
        _write_csv(folder / f"part{i}.csv", 4)
    cat = Catalog(tmp_path / "data")
    meta = cat.register(str(folder), options={"glob": "*.csv"})
    assert meta["linked"] is True
    assert cat.query("SELECT COUNT(*) AS n FROM shards")["rows"][0]["n"] == 12

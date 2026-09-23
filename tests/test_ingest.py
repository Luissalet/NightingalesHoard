"""Ingestion: CSV (Spanish formats), Excel (title rows), JSON (flatten), Parquet, SQLite, folder glob, paste."""

import json
import sqlite3

import pytest


def write(tmp_path, name, content, encoding="utf-8"):
    path = tmp_path / name
    path.write_bytes(content.encode(encoding))
    return path


def test_ingest_csv_spanish_format(services, tmp_path):
    csv_text = (
        "producto;importe;fecha\n"
        "Teclado;1.150,00;13/02/2024\n"
        "Raton;51,05;01/03/2024\n"
    )
    path = write(tmp_path, "ventas.csv", csv_text, encoding="cp1252")
    result = services.ingest_file(str(path), "ventas", {"delimiter": ";", "encoding": "latin-1"})
    assert result["row_count"] == 2
    names = [c["name"] for c in result["columns"]]
    assert names == ["producto", "importe", "fecha"]
    preview = services.preview("ventas", limit=5)
    row0 = preview["rows"][0]
    assert row0["importe"] == 1150.0
    assert row0["fecha"] == "2024-02-13"  # day-first parsed correctly, not year/day swapped


def test_ingest_csv_default_utf8_and_dedup_source(services, tmp_path):
    path = write(tmp_path, "simple.csv", "a,b\n1,2\n3,4\n")
    result = services.ingest_file(str(path), "simple")
    assert result["row_count"] == 2
    assert result["current_version"] == 0


def test_ingest_tsv(services, tmp_path):
    path = write(tmp_path, "data.tsv", "a\tb\n1\t2\n")
    result = services.ingest_file(str(path), "tsvtest")
    assert result["row_count"] == 1


def test_ingest_excel_with_title_row(services, tmp_path):
    import openpyxl

    wb = openpyxl.Workbook()
    ws = wb.active
    ws.append(["Informe de ventas Q1"])
    ws.append([])
    ws.append(["producto", "cantidad"])
    ws.append(["A", 3])
    ws.append(["B", 5])
    path = tmp_path / "informe.xlsx"
    wb.save(path)
    result = services.ingest_file(str(path), "informe")
    assert result["row_count"] == 2
    assert [c["name"] for c in result["columns"]] == ["producto", "cantidad"]


def test_ingest_json_flatten(services, tmp_path):
    data = [{"id": 1, "info": {"city": "Madrid", "zip": "28001"}}, {"id": 2, "info": {"city": "Cadiz", "zip": "11001"}}]
    path = tmp_path / "people.json"
    path.write_text(json.dumps(data), encoding="utf-8")
    result = services.ingest_file(str(path), "people", {"flatten": True})
    names = [c["name"] for c in result["columns"]]
    assert "city" in names and "zip" in names


def test_ingest_ndjson(services, tmp_path):
    path = tmp_path / "log.ndjson"
    path.write_text('{"a": 1}\n{"a": 2}\n', encoding="utf-8")
    result = services.ingest_file(str(path), "logdata")
    assert result["row_count"] == 2


def test_ingest_parquet(services, tmp_path):
    import pandas as pd

    df = pd.DataFrame({"x": [1, 2, 3], "y": ["a", "b", "c"]})
    path = tmp_path / "data.parquet"
    df.to_parquet(path)
    result = services.ingest_file(str(path), "pq")
    assert result["row_count"] == 3


def test_ingest_sqlite(services, tmp_path):
    path = tmp_path / "app.sqlite"
    conn = sqlite3.connect(path)
    conn.execute("CREATE TABLE items (id INTEGER, name TEXT)")
    conn.executemany("INSERT INTO items VALUES (?, ?)", [(1, "a"), (2, "b")])
    conn.commit()
    conn.close()
    result = services.ingest_file(str(path), "items")
    assert result["row_count"] == 2


def test_ingest_folder_glob_adds_source_file_column(services, tmp_path):
    folder = tmp_path / "many"
    folder.mkdir()
    (folder / "a.csv").write_text("x\n1\n2\n")
    (folder / "b.csv").write_text("x\n3\n")
    result = services.ingest_folder(str(folder), "many", "*.csv")
    assert result["row_count"] == 3
    names = [c["name"] for c in result["columns"]]
    assert "_source_file" in names


def test_ingest_pasted_text(services):
    result = services.ingest_text("a,b\n1,2\n3,4\n", "pasted", "csv")
    assert result["row_count"] == 2


def test_ingest_unsupported_extension_raises(services, tmp_path):
    path = write(tmp_path, "weird.xyz", "hello")
    with pytest.raises(Exception):
        services.ingest_file(str(path), "weird")


def test_ingest_missing_path_raises(services):
    with pytest.raises(Exception):
        services.ingest_file("/no/such/file.csv", "nope")

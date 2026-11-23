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


def _mixed_endings_rows(n=13):
    return [f"R{i},puesto {i % 3},{i % 10},{'si' if i % 4 == 0 else 'no'},nota.md" for i in range(1, n + 1)]


def test_ingest_csv_with_bom_and_mixed_line_endings(services, tmp_path):
    """The DuckDB sniffer refuses a file whose lines end with LF except the last (CRLF); we read it as written."""
    rows = _mixed_endings_rows()
    text = "﻿intento,puesto,nota,falta,fuente\n" + "\n".join(rows[:-1]) + "\n" + rows[-1] + "\r\n"
    path = write(tmp_path, "mixto.csv", text)
    assert path.read_bytes()[:3] == b"\xef\xbb\xbf" and path.read_bytes().count(b"\r") == 1
    result = services.ingest_file(str(path), "mixto")
    assert result["row_count"] == len(rows)
    assert [c["name"] for c in result["columns"]] == ["intento", "puesto", "nota", "falta", "fuente"]  # BOM not in the name
    assert result["mixed_line_endings"] == {"lf": len(rows), "crlf": 1, "cr": 0}
    got = services.preview("mixto", limit=50)["rows"]
    assert [tuple(r.values()) for r in got] == [tuple(int(v) if v.isdigit() else v for v in row.split(",")) for row in rows]
    assert got[-1]["fuente"] == "nota.md"  # the CR of the last line did not leak into the last cell


def test_ingest_csv_mixed_line_endings_with_delimiter_and_header_set(services, tmp_path):
    rows = _mixed_endings_rows(8)
    text = "a,b,c,d,e\r\n" + "\n".join(rows[:4]) + "\r\n" + "\n".join(rows[4:]) + "\n"
    path = write(tmp_path, "mixto2.csv", text)
    result = services.ingest_file(str(path), "mixto2", {"delimiter": ",", "header": True})
    assert result["row_count"] == 8
    assert result["mixed_line_endings"] == {"lf": 7, "crlf": 2, "cr": 0}


def test_ingest_csv_mixed_line_endings_in_latin1(services, tmp_path):
    rows = [f"R{i};nota número {i};{i},5" for i in range(1, 14)]
    text = "id;comentario;importe\n" + "\n".join(rows[:-1]) + "\n" + rows[-1] + "\r\n"
    path = write(tmp_path, "mixto3.csv", text, encoding="cp1252")
    result = services.ingest_file(str(path), "mixto3")
    assert result["row_count"] == 13 and result["encoding_detected"] == "latin-1"
    assert result["mixed_line_endings"]["crlf"] == 1
    assert services.preview("mixto3", limit=1)["rows"][0]["comentario"] == "nota número 1"


def test_line_endings_counter_handles_a_crlf_split_between_two_blocks(tmp_path):
    from nightingale.workbench.engine import _line_endings

    block = 1 << 20
    path = tmp_path / "limite.csv"
    path.write_bytes(b"a\n" * ((block - 1) // 2) + b"x" + b"\r\n" + b"b\r" + b"\n" * 2 + b"c\r")
    assert path.read_bytes()[block - 1:block + 1] == b"\r\n"  # the CR is the last byte of the first block
    assert _line_endings(path) == {"lf": (block - 1) // 2 + 1, "crlf": 2, "cr": 1}
    assert _line_endings(path, "utf-16") is None


def test_ingest_csv_with_one_line_ending_has_no_mixed_note(services, tmp_path):
    path = write(tmp_path, "limpio.csv", "﻿a,b\r\n1,2\r\n3,4\r\n")
    result = services.ingest_file(str(path), "limpio")
    assert result["row_count"] == 2 and "mixed_line_endings" not in result


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


def test_ingest_folder_reads_csv_files_with_mixed_line_endings(services, tmp_path):
    """The folder reader gets the same retry as a single file, and names which files mix their endings."""
    folder = tmp_path / "mezcla"
    folder.mkdir()
    rows = _mixed_endings_rows()
    mixed = "﻿intento,puesto,nota,falta,fuente\n" + "\n".join(rows[:-1]) + "\n" + rows[-1] + "\r\n"
    (folder / "mixto.csv").write_bytes(mixed.encode("utf-8"))
    (folder / "limpio.csv").write_bytes(b"intento,puesto,nota,falta,fuente\r\nR99,puesto 0,9,no,otra.md\r\n")
    result = services.ingest_folder(str(folder), "mezcla", "*.csv")
    assert result["row_count"] == len(rows) + 1 and result["files"] == 2
    assert result["mixed_line_endings"] == {"mixto.csv": {"lf": len(rows), "crlf": 1, "cr": 0}}
    assert [c["name"] for c in result["columns"]] == ["intento", "puesto", "nota", "falta", "fuente", "_source_file"]
    got = services.preview("mezcla", limit=50)["rows"]
    assert sorted(r["intento"] for r in got) == sorted([r.split(",")[0] for r in rows] + ["R99"])
    assert {r["_source_file"].replace("\\", "/").rsplit("/", 1)[-1] for r in got} == {"mixto.csv", "limpio.csv"}
    assert {r["fuente"] for r in got if r["intento"] != "R99"} == {"nota.md"}  # no CR leaked into the last cell


def test_ingest_folder_of_clean_csv_files_has_no_mixed_note(services, tmp_path):
    folder = tmp_path / "limpios"
    folder.mkdir()
    (folder / "a.csv").write_bytes(b"x\r\n1\r\n2\r\n")
    (folder / "b.csv").write_bytes(b"x\n3\n")
    result = services.ingest_folder(str(folder), "limpios", "*.csv")
    assert result["row_count"] == 3 and "mixed_line_endings" not in result


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

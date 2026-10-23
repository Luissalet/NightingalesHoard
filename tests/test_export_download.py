import csv
import io
import json
import zipfile

import duckdb
import openpyxl


def test_download_all_formats_uses_explicit_saved_version_without_changing_data(client, tmp_path):
    rows = [[index, 'Café "ficticio", duplicado', None if index % 3 == 0 else index * 2] for index in range(260)]
    rows.append(rows[1])
    source = tmp_path / "source.csv"
    with source.open("w", encoding="utf-8", newline="") as handle:
        writer = csv.writer(handle)
        writer.writerow(["id", "label", "value"])
        writer.writerows(rows)
    original = source.read_bytes()
    svc = client.services
    svc.ingest_file(str(source), name="export_fixture")
    svc.transform_apply("export_fixture", "filter", {"expr": "id < 3"})
    datasets = svc.list_datasets()
    lineage = svc.lineage("export_fixture")
    for fmt in ("csv", "json", "parquet", "xlsx"):
        response = client.post("/api/export/download", json={"dataset": "export_fixture", "format": fmt, "version": 0})
        assert response.status_code == 200, response.text
        assert f"export_fixture_v0.{fmt}" in response.headers["content-disposition"]
        if fmt == "csv":
            parsed = list(csv.reader(io.StringIO(response.content.decode("utf-8"))))
            assert parsed[0] == ["id", "label", "value"]
            assert parsed[1:] == [["" if value is None else str(value) for value in row] for row in rows]
        elif fmt == "json":
            assert json.loads(response.content) == [dict(zip(["id", "label", "value"], row)) for row in rows]
        elif fmt == "xlsx":
            book = openpyxl.load_workbook(io.BytesIO(response.content), read_only=True)
            assert list(book.active.values) == [("id", "label", "value"), *[tuple(row) for row in rows]]
            assert len(book.worksheets) == 1
            book.close()
        else:
            file = tmp_path / "download.parquet"
            file.write_bytes(response.content)
            with duckdb.connect() as connection:
                assert connection.execute("SELECT * FROM read_parquet(?)", [str(file)]).fetchall() == [tuple(row) for row in rows]
    assert svc.list_datasets() == datasets
    assert svc.lineage("export_fixture") == lineage
    assert source.read_bytes() == original
    assert svc.preview("export_fixture")["row_count_total"] == 4


def test_workbook_download_uses_saved_version_preserves_parts_and_reports_native_error(client, tmp_path):
    from test_workbook_export import _rich_workbook, _ingest_target

    source = _rich_workbook(tmp_path / "book.xlsx", target_validation=True)
    original = source.read_bytes()
    svc = client.services
    dataset = _ingest_target(svc, source)
    svc.transform_apply(dataset, "replace", {"column": "categoria", "pattern": "café", "replacement": "té"})
    svc.transform_apply(dataset, "filter", {"expr": "importe > 10"})
    datasets, lineage = svc.list_datasets(), svc.lineage(dataset)
    response = client.post("/api/export/workbook/download", json={"dataset": dataset, "version": 1})
    assert response.status_code == 200, response.text
    assert f"{dataset}_v1_workbook.xlsx" in response.headers["content-disposition"]
    with zipfile.ZipFile(io.BytesIO(original)) as before, zipfile.ZipFile(io.BytesIO(response.content)) as after:
        assert before.namelist() == after.namelist()
        changed = {name for name in before.namelist() if before.read(name) != after.read(name)}
        assert changed == {"xl/worksheets/sheet1.xml", "xl/workbook.xml"}
    book = openpyxl.load_workbook(io.BytesIO(response.content))
    assert book["Ventas"]["C2"].value == "té"
    assert book["Ventas"]["C3"].value == "té"
    assert book["Ventas"]["F2"].value == "00123"
    assert book["Resumen"]["B1"].value == "=SUM(Ventas!B2:B4)"
    assert book["Ventas"].data_validations.count == 1
    book.close()
    incompatible = client.post("/api/export/workbook/download", json={"dataset": dataset, "version": 2})
    assert incompatible.status_code == 400, incompatible.text
    assert "row" in incompatible.json()["error"].lower()
    assert source.read_bytes() == original
    assert svc.list_datasets() == datasets and svc.lineage(dataset) == lineage

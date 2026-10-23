import csv
import io
import json

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

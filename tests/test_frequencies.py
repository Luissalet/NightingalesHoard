import asyncio
import json

from nightingale.agent_tools import call_tool, tool_catalog


def test_frequencies_pages_null_empty_long_values_and_quoted_column(services):
    column = 'categoría,"ñ"'
    long = "Texto ñ " * 4000
    rows = [[long], [long], ["NULL"], [""], [None], [None], *[[f"valor{i:02}"] for i in range(53)]]
    text = json.dumps([{column: row[0]} for row in rows], ensure_ascii=False)
    services.ingest_text(text, "frecuencias", fmt="json")
    first = services.column_frequencies("frecuencias", column, limit=2)
    assert first["row_count"] == 59 and first["value_count"] == 57
    assert first["rows"] == [
        {"value": long, "is_null": False, "count": 2, "percent": 200/59},
        {"value": None, "is_null": True, "count": 2, "percent": 200/59},
    ]
    assert first["next_offset"] == 2
    second = services.column_frequencies("frecuencias", column, offset=2, limit=1000)
    assert len(second["rows"]) == 55 and not second["has_more"] and second["next_offset"] is None
    assert second["rows"][0]["value"] == "" and second["rows"][1]["value"] == "NULL"
    assert sum(row["count"] for row in first["rows"]+second["rows"]) == 59


def test_saved_version_decimal_precision_empty_and_no_metadata_write(services):
    services.ingest_text("id\n1\n2\n3\n", "versiones")
    services.transform_apply("versiones", "sql", {"sql": "SELECT CAST('123456789012345678.123456789' AS DECIMAL(30,9)) AS exact FROM __prev__"})
    services.transform_apply("versiones", "sql", {"sql": "SELECT * FROM __prev__ WHERE false"})
    before = services.list_datasets()
    versions = services.meta.list_versions(services._dataset_row("versiones")["id"])
    logs = services.meta.db.query("SELECT * FROM log")
    old = services.column_frequencies("versiones", "exact", version=1)
    assert old["rows"][0] == {"value":"123456789012345678.123456789", "is_null":False, "count":3, "percent":100.0}
    empty = services.column_frequencies("versiones", "exact")
    assert empty["version"] == 2 and empty["row_count"] == empty["value_count"] == 0 and empty["rows"] == []
    assert before == services.list_datasets()
    assert versions == services.meta.list_versions(services._dataset_row("versiones")["id"])
    assert logs == services.meta.db.query("SELECT * FROM log")


def test_http_agent_full_page_and_static_catalog(client):
    long = "A" * 30000
    client.services.ingest_text(json.dumps([{"valor": long},{"valor":"ñ"}]), "api", fmt="json")
    args = {"dataset":"api", "mode":"frequencies", "column":"valor", "version":0, "limit":1}
    response = client.get("/api/datasets/api/frequencies", params={k:v for k,v in args.items() if k not in ("dataset","mode")})
    assert response.status_code == 200
    native = asyncio.run(call_tool(client.services,"data_profile", args))
    assert native == response.json() and native["rows"][0]["value"] == long and native["next_offset"] == 1
    assert client.get("/api/datasets/api/frequencies",params={"column":"missing"}).status_code == 400
    assert client.get("/api/datasets/api/frequencies",params={"column":"valor","version":99}).status_code == 404
    catalog = json.loads((__import__('pathlib').Path(__file__).parents[1]/'fusion/catalog.json').read_text(encoding='utf8'))
    frozen = next(t for t in catalog if t['name']=='data_profile')
    live = next(t for t in tool_catalog() if t['name']=='data_profile')
    assert frozen['inputSchema'] == live['inputSchema']
    assert len(catalog) == 44

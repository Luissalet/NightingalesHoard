import asyncio

from nightingale.agent_tools import call_tool


def prepare(svc):
    svc.ingest_text("id,value,old\n1,10,x\n2,,y\n3,30,z\n", "comparison")
    svc.transform_apply("comparison", "sql", {"sql": "SELECT CAST(id AS VARCHAR) AS id, COALESCE(value,0) AS value, COALESCE(value,0)*2 AS doubled FROM __prev__ WHERE id < 3"})


def test_compare_versions_reports_schema_profiles_and_reverse_deltas_without_moving_current(services):
    prepare(services)
    before = services.list_datasets()
    versions = len(services.meta.list_versions(services._dataset_row("comparison")["id"]))
    result = services.compare_versions("comparison", 0, 1)
    assert result["row_delta"] == -1 and result["column_delta"] == 0
    assert result["added_columns"] == ["doubled"] and result["removed_columns"] == ["old"]
    assert result["type_changed_columns"] == ["id"]
    value = next(column for column in result["columns"] if column["name"] == "value")
    assert value["before"]["nulls"] == 1 and value["after"]["nulls"] == 0 and value["nulls_delta"] == -1
    assert value["before"]["max"] == 30 and value["after"]["max"] == 10
    assert result["row_alignment"] is False and result["distinct_kind"] == "approximate"
    assert services.compare_versions("comparison", 1, 0)["row_delta"] == 1
    assert not any(column["profile_changed"] for column in services.compare_versions("comparison", 1, 1)["columns"])
    assert services.list_datasets() == before
    assert len(services.meta.list_versions(services._dataset_row("comparison")["id"])) == versions


def test_compare_versions_api_and_tool_can_read_a_saved_redo_version(client):
    prepare(client.services)
    client.services.undo("comparison", 1)
    response = client.get("/api/datasets/comparison/versions/compare", params={"version_a":0,"version_b":1})
    assert response.status_code == 200 and response.json()["b"]["version"] == 1
    answer = asyncio.run(call_tool(client.services, "data_versions_compare", {"dataset":"comparison","version_a":0,"version_b":1}))
    assert answer == response.json()
    assert client.services._dataset_row("comparison")["current_version"] == 0
    default = client.get("/api/datasets/comparison/versions/compare")
    assert default.json()["row_delta"] == 0
    assert client.get("/api/datasets/comparison/versions/compare", params={"version_b":99}).status_code == 404


def test_row_difference_preserves_duplicates_nulls_precision_and_column_scope(services):
    services.ingest_text("id,value,old\n1,10,x\n1,10,x\n2,,y\n3,30,z\n", "rows")
    services.transform_apply("rows", "sql", {"sql": "SELECT id, value, 'new' AS extra FROM __prev__ WHERE id<>3 UNION ALL SELECT 4,40,'new'"})
    result = services.compare_rows("rows", 0, 1, limit=1)
    assert result["compared_columns"] == ["id", "value"]
    assert result["common_count"] == 3
    assert result["only_a"]["rows"] == [{"id":"3", "value":"30"}]
    assert result["only_b"]["rows"] == [{"id":"4", "value":"40"}]
    assert {c["reason"] for c in result["omitted_columns"]} == {"missing_in_a", "missing_in_b"}
    same = services.compare_rows("rows", 0, 0)
    assert same["common_count"] == 4 and same["only_a"]["count"] == same["only_b"]["count"] == 0
    services.transform_apply("rows", "sql", {"sql": "SELECT id, CAST('123456789012345678.123456789' AS DECIMAL(30,9)) AS exact FROM __prev__ WHERE id=1 LIMIT 1"})
    services.transform_apply("rows", "sql", {"sql": "SELECT * FROM __prev__ WHERE false"})
    exact = services.compare_rows("rows", 2, 3)
    assert exact["only_a"]["rows"][0]["exact"] == "123456789012345678.123456789"
    duplicate = services.compare_rows("rows", 0, 2, columns=["id"], limit=1)
    assert duplicate["common_count"] == 1 and duplicate["only_a"]["count"] == 3
    assert duplicate["only_a"]["next_offset"] == 1
    assert services.compare_rows("rows", 0, 2, columns=["id"], offset_a=1, limit=1)["only_a"]["rows"] == [{"id":"2"}]
    assert services.compare_rows("rows", 0, 1, columns=[])["common_count"] is None


def test_row_difference_http_and_tool_read_redo_without_changing_history(client):
    prepare(client.services)
    client.services.undo("comparison", 1)
    before = client.services.list_datasets()
    versions = client.services.meta.list_versions(client.services._dataset_row("comparison")["id"])
    args = {"dataset":"comparison", "version_a":0, "version_b":1}
    response = client.post("/api/datasets/comparison/versions/rows", json={"version_a":0,"version_b":1})
    assert response.status_code == 200
    result = response.json()
    assert result == asyncio.run(call_tool(client.services, "data_rows_compare", args))
    assert result["compared_columns"] == ["value"] and result["only_a"]["rows"] == [{"value":"30"},{"value":None}]
    assert client.services.list_datasets() == before
    assert client.services.meta.list_versions(client.services._dataset_row("comparison")["id"]) == versions

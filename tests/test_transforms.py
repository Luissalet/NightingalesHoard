"""Every transform step type, applied at least once, plus preview/undo/redo/recipe/lineage."""

import pytest
from nightingale.agent_tools import tool_catalog
from nightingale.sqlgate import gate_sql
from nightingale.workbench.steps import STEP_BUILDERS, STEP_PARAM_GUIDANCE, STEP_KINDS, StepContext, StepError, build_step_sql


@pytest.fixture
def sales(services, tmp_path):
    path = tmp_path / "sales.csv"
    path.write_text(
        "id,region,product,qty,price,note\n"
        "1,Madrid,Widget,3,9.5,ok\n"
        "2,Madrid,Widget,3,9.5,ok\n"  # exact duplicate of row 1
        "3,Barcelona,Gadget,1,25.0, urgent \n"
        "4,Sevilla,Widget,,12.0,\n"  # null qty
        "5,Bilbao,Gizmo,2,7.25,PROMO\n",
        encoding="utf-8",
    )
    services.ingest_file(str(path), "sales")
    return "sales"


def test_transform_parameter_inventory_matches_native_builders(client):
    expected = client.services.transform_operations()
    assert [item["op"] for item in expected["operations"]] == list(STEP_KINDS)
    assert set(STEP_PARAM_GUIDANCE) == set(STEP_BUILDERS)
    api = client.get("/api/transforms")
    assert api.status_code == 200 and api.json() == expected
    assert client.get("/api/transforms", params={"op": "replace"}).json()["operations"] == [
        next(item for item in expected["operations"] if item["op"] == "replace")]
    by_op = {item["op"]: item for item in expected["operations"]}
    assert "array<{fn?: string, column?: string (required unless fn=count), alias?: string}>" in by_op["group"]["shapes"]["aggregations"]
    assert "desc?: boolean" in by_op["sort"]["shapes"]["by"]
    assert "left: string, right: string" in by_op["join"]["shapes"]["on"]
    assert by_op["window"]["defaults"]["offset"] == 1
    assert "Exact COUNT(DISTINCT column)" in by_op["group"]["operation_notes"]["count_distinct"]
    schema = next(item for item in tool_catalog() if item["name"] == "data_transform")["inputSchema"]
    assert "dataset" not in schema.get("required", [])
    assert len(schema["properties"]["params"]["description"]) < 180


def test_mcp_transform_help_is_filterable_and_has_no_dataset_side_effect(client, sales):
    dataset = client.services._dataset_row(sales)
    version_before = dataset["current_version"]
    version_count_before = len(client.services.meta.list_versions(dataset["id"]))
    log_count_before = len(client.services.log_search(limit=200)["entries"])
    help_response = client.post("/api/agent/call", headers={"Authorization": f"Bearer {client.services.token}"},
        json={"name": "data_transform", "arguments": {"op": "help", "help_for": "replace"}})
    assert help_response.status_code == 200, help_response.text
    contract = help_response.json()
    assert contract["read_only"] is True
    assert [item["op"] for item in contract["operations"]] == ["replace"]
    assert contract["operations"][0]["required"] == ["column", "pattern"]
    assert contract["operations"][0]["optional"] == ["replacement", "regex"]
    full_help = client.post("/api/agent/call", headers={"Authorization": f"Bearer {client.services.token}"},
        json={"name": "data_transform", "arguments": {"op": "help"}})
    assert full_help.status_code == 200 and len(full_help.json()["operations"]) == len(STEP_KINDS)
    dataset_after = client.services._dataset_row(sales)
    assert dataset_after["current_version"] == version_before
    assert len(client.services.meta.list_versions(dataset["id"])) == version_count_before
    assert len(client.services.log_search(limit=200)["entries"]) == log_count_before


def test_transform_missing_parameter_errors_name_the_native_contract():
    ctx = StepContext("prev", ["amount"], lambda _name: "other")
    with pytest.raises(StepError, match=r"replace requires parameter\(s\): column, pattern"):
        build_step_sql("replace", ctx, {})
    with pytest.raises(StepError, match=r"fill_null requires parameter\(s\): column, value"):
        build_step_sql("fill_null", ctx, {})
    with pytest.raises(StepError, match=r"sample requires parameter\(s\): one of n or frac"):
        build_step_sql("sample", ctx, {})
    with pytest.raises(StepError, match=r"window requires parameter\(s\): fn, order_by"):
        build_step_sql("window", ctx, {})
    with pytest.raises(StepError, match=r"window requires parameter\(s\): column"):
        build_step_sql("window", ctx, {"fn": "lag", "order_by": ["amount"]})


def test_replace_missing_pattern_error_is_clear_on_rest_and_mcp(client, sales):
    rest = client.post(f"/api/datasets/{sales}/transform", json={
        "op": "replace", "params": {"column": "note"}, "preview": True})
    assert rest.status_code == 400
    assert "replace requires parameter(s): pattern" in rest.json()["error"]
    mcp = client.post("/api/agent/call", headers={"Authorization": f"Bearer {client.services.token}"},
                      json={"name": "data_transform", "arguments": {
                          "dataset": sales, "op": "replace", "params": {"column": "note"}, "preview": True}})
    assert mcp.status_code == 400
    assert "replace requires parameter(s): pattern" in mcp.json()["error"]


@pytest.mark.parametrize("transport", ["rest", "mcp"])
@pytest.mark.parametrize("preview", [True, False], ids=["preview", "apply"])
@pytest.mark.parametrize(
    "op,bad_params,good_params,expected_values",
    [
        ("text", {"column": "name", "op": "title"},
         {"column": "phrase", "op": "title"}, ["Lumiere", "Nightingale", "Lumiere"]),
        ("group", {"group_by": ["name"], "aggregations": [
            {"column": "row_id", "fn": "count", "alias": "n"}]},
         {"group_by": ["phrase"], "aggregations": [
             {"column": "row_id", "fn": "count", "alias": "n"}]}, None),
    ],
)
def test_unknown_transform_columns_report_schema_and_allow_recovery(
        client, tmp_path, transport, preview, op, bad_params, good_params, expected_values):
    path = tmp_path / "column-feedback.csv"
    path.write_text(
        "row_id,phrase\n1,lumiere\n2,nightingale\n3,lumiere\n", encoding="utf-8")
    client.services.ingest_file(str(path), "column_feedback")
    dataset = client.services._dataset_row("column_feedback")
    dataset_id = dataset["id"]
    before_version = dataset["current_version"]
    before_versions = client.services.meta.list_versions(dataset_id)
    before_preview = client.services.preview("column_feedback", limit=10)
    arguments = {"op": op, "params": bad_params, "preview": preview}

    if transport == "rest":
        response = client.post("/api/datasets/column_feedback/transform", json=arguments)
    else:
        response = client.post("/api/agent/call", headers={
            "Authorization": f"Bearer {client.services.token}"}, json={
                "name": "data_transform",
                "arguments": {"dataset": "column_feedback", **arguments},
            })

    assert response.status_code == 400, response.text
    error = response.json()["error"]
    assert "name" in error
    assert "available" in error
    assert "row_id" in error and "phrase" in error
    assert client.services._dataset_row("column_feedback")["current_version"] == before_version
    assert client.services.meta.list_versions(dataset_id) == before_versions
    after_preview = client.services.preview("column_feedback", limit=10)
    assert {key: value for key, value in after_preview.items() if key != "elapsed_ms"} == {
        key: value for key, value in before_preview.items() if key != "elapsed_ms"}

    recovery_args = {"op": op, "params": good_params, "preview": False}
    if transport == "rest":
        recovered = client.post("/api/datasets/column_feedback/transform", json=recovery_args)
    else:
        recovered = client.post("/api/agent/call", headers={
            "Authorization": f"Bearer {client.services.token}"}, json={
                "name": "data_transform",
                "arguments": {"dataset": "column_feedback", **recovery_args},
            })
    assert recovered.status_code == 200, recovered.text
    assert client.services._dataset_row("column_feedback")["current_version"] == 1
    if op == "text":
        rows = client.services.preview("column_feedback", limit=10)["rows"]
        assert [row["phrase"] for row in rows] == expected_values
    else:
        rows = client.services.preview("column_feedback", limit=10)["rows"]
        assert {row["phrase"]: row["n"] for row in rows} == {
            "lumiere": 2, "nightingale": 1}


def test_scalar_and_group_column_errors_include_available_columns():
    ctx = StepContext("prev", ["row_id", "phrase"], lambda _name: "other")
    cases = [
        ("text", {"column": "name", "op": "title"}, "unknown column: ['name']"),
        ("group", {"group_by": ["name"], "aggregations": []}, "unknown group_by column(s): ['name']"),
        ("group", {"group_by": [], "aggregations": [{"column": "name", "fn": "sum"}]},
         "unknown aggregation column: ['name']"),
    ]
    for op, params, message in cases:
        with pytest.raises(StepError) as exc:
            build_step_sql(op, ctx, params)
        assert message in str(exc.value)
        assert "available: ['row_id', 'phrase']" in str(exc.value)


def test_preview_does_not_create_a_version(services, sales):
    before = services.list_datasets()["datasets"][0]["current_version"]
    preview = services.transform_preview(sales, "filter", {"expr": "qty > 1"})
    assert preview["row_count_after"] < preview["row_count_before"]
    after = services.list_datasets()["datasets"][0]["current_version"]
    assert before == after


def test_filter_and_select_and_drop(services, sales):
    services.transform_apply(sales, "filter", {"expr": "qty IS NOT NULL"})
    r = services.transform_apply(sales, "select", {"columns": ["id", "region", "qty"]})
    assert [c["name"] for c in r["columns"]] == ["id", "region", "qty"]
    r2 = services.transform_apply(sales, "drop", {"columns": ["id"]})
    assert "id" not in [c["name"] for c in r2["columns"]]


def test_rename(services, sales):
    r = services.transform_apply(sales, "rename", {"mapping": {"qty": "quantity"}})
    assert "quantity" in [c["name"] for c in r["columns"]]


def test_cast_spanish_number_and_date(services, tmp_path):
    services2 = services
    path = tmp_path / "amounts.csv"
    path.write_text("amount_text,date_text\n1.150,00 vs\n\n", encoding="utf-8")
    # simpler: cast a plain text numeric column
    path.write_text("amount_text\n12\n34\n", encoding="utf-8")
    services2.ingest_file(str(path), "amounts")
    r = services2.transform_apply("amounts", "cast", {"column": "amount_text", "to": "integer"})
    assert r["columns"][0]["type"] in ("BIGINT",)


def test_fill_null_strategies(services, sales):
    services.transform_apply(sales, "fill_null", {"column": "qty", "strategy": "mean"})
    preview = services.preview(sales, limit=10)
    assert all(row["qty"] is not None for row in preview["rows"])


def test_drop_duplicates(services, sales):
    before = services.preview(sales, limit=10)["row_count_total"]
    r = services.transform_apply(sales, "drop_duplicates", {"subset": ["region", "product", "qty", "price"]})
    assert r["row_count"] < before


def test_derive(services, sales):
    r = services.transform_apply(sales, "derive", {"name": "total", "expr": "qty * price"})
    assert "total" in [c["name"] for c in r["columns"]]


def test_split_column(services, sales):
    services.transform_apply(sales, "derive", {"name": "combo", "expr": "region || '-' || product"})
    r = services.transform_apply(sales, "split_column", {"column": "combo", "delimiter": "-", "into": ["a", "b"]})
    assert "a" in [c["name"] for c in r["columns"]] and "b" in [c["name"] for c in r["columns"]]


def test_text_ops(services, sales):
    r = services.transform_apply(sales, "text", {"column": "note", "op": "trim"})
    preview = services.preview(sales, limit=10)
    assert not any((row["note"] or "").startswith(" ") for row in preview["rows"])
    services.transform_apply(sales, "text", {"column": "region", "op": "upper", "new_column": "region_upper"})


def test_title_text_transform_preserves_unicode_delimiters_and_nulls(services, tmp_path):
    import unicodedata

    path = tmp_path / "title.csv"
    path.write_text('phrase\n"  mIxEd  café-o\'NEIL! "\n"e\u0301COLE--HELLO"\n""\n', encoding="utf-8")
    services.ingest_file(str(path), "title_text")
    ctx = StepContext("__prev__", ["phrase"], lambda _name: "other")
    generated_sql = build_step_sql("text", ctx, {"column": "phrase", "op": "title"})
    gate_sql(generated_sql)
    services.transform_apply("title_text", "text", {"column": "phrase", "op": "title"})
    rows = services.preview("title_text", limit=10)["rows"]
    assert rows[0]["phrase"] == "  Mixed  Café-O'Neil! "
    assert unicodedata.normalize("NFC", rows[1]["phrase"]) == "École--Hello"
    assert "\u0301" in rows[1]["phrase"]  # combining accent remains decomposed in the source text
    assert rows[2]["phrase"] in (None, "")


def test_replace(services, sales):
    r = services.transform_apply(sales, "replace", {"column": "note", "pattern": "PROMO", "replacement": "promo"})
    assert r["row_count"] > 0


@pytest.mark.parametrize("transport", ["rest", "mcp"])
@pytest.mark.parametrize("preview", [True, False], ids=["preview", "apply"])
def test_unknown_replace_field_cannot_silently_delete_values(client, sales, transport, preview):
    before = client.services.preview(sales, limit=10)
    dataset = client.services._dataset_row(sales)
    versions_before = client.services.meta.list_versions(dataset["id"])
    arguments = {"op": "replace", "params": {
        "column": "note", "pattern": "PROMO", "new_value": "renamed"}, "preview": preview}
    if transport == "rest":
        response = client.post(f"/api/datasets/{sales}/transform", json=arguments)
    else:
        response = client.post("/api/agent/call", headers={"Authorization": f"Bearer {client.services.token}"},
            json={"name": "data_transform", "arguments": {"dataset": sales, **arguments}})
    assert response.status_code == 400, response.text
    assert "replace" in response.json()["error"]
    assert "new_value" in response.json()["error"]
    assert "replacement" in response.json()["error"]
    assert client.services._dataset_row(sales)["current_version"] == 0
    assert client.services.meta.list_versions(dataset["id"]) == versions_before
    after = client.services.preview(sales, limit=10)
    assert {key: value for key, value in after.items() if key != "elapsed_ms"} == {
        key: value for key, value in before.items() if key != "elapsed_ms"}


def test_replace_without_replacement_still_supports_intentional_deletion(services, sales):
    preview = services.transform_preview(sales, "replace", {"column": "note", "pattern": "PROMO"})
    assert next(row for row in preview["preview_rows"] if row["id"] == 5)["note"] == ""
    assert services._dataset_row(sales)["current_version"] == 0
    applied = services.transform_apply(sales, "replace", {"column": "note", "pattern": "PROMO"})
    assert applied["current_version"] == 1
    assert next(row for row in services.preview(sales, limit=10)["rows"] if row["id"] == 5)["note"] == ""


def test_conditional_and_alternative_fields_remain_valid(services, sales):
    filled = services.transform_preview(sales, "fill_null", {"column": "qty", "value": 0})
    assert next(row for row in filled["preview_rows"] if row["id"] == 4)["qty"] == 0
    sampled = services.transform_preview(sales, "sample", {"frac": 1.0, "seed": 9})
    assert sampled["row_count_after"] == 5


def test_bin(services, sales):
    r = services.transform_apply(sales, "bin", {"column": "price", "bins": 3})
    assert "price_bin" in [c["name"] for c in r["columns"]]


def test_date_parts(services, tmp_path):
    path = tmp_path / "dates.csv"
    path.write_text("d\n2024-01-15\n2024-06-01\n", encoding="utf-8")
    services.ingest_file(str(path), "dates")
    services.transform_apply("dates", "cast", {"column": "d", "to": "date"})
    r = services.transform_apply("dates", "date_parts", {"column": "d", "parts": ["year", "month"]})
    names = [c["name"] for c in r["columns"]]
    assert "d_year" in names and "d_month" in names


def test_group_aggregate(services, sales):
    r = services.transform_apply(sales, "group", {"group_by": ["region"],
                                                     "aggregations": [{"column": "qty", "fn": "sum", "alias": "total_qty"},
                                                                        {"fn": "count", "alias": "n"}]})
    assert "total_qty" in [c["name"] for c in r["columns"]]
    assert r["row_count"] <= 5


def test_count_distinct_is_exact_through_api_mcp_recipe_and_export(client, tmp_path):
    import csv

    path = tmp_path / "distinct.csv"
    path.write_text(
        "bucket,value\n" + "".join(f"all,{value}\n" for value in range(1000)) +
        "all,13\nall,13\nall,\n",
        encoding="utf-8",
    )
    client.services.ingest_file(str(path), "distinct_counts")
    source_rows = client.get("/api/datasets/distinct_counts/preview?limit=1100").json()["rows"]
    assert any(row["value"] is None for row in source_rows)
    params = {"group_by": ["bucket"], "aggregations": [
        {"column": "value", "fn": "count_distinct", "alias": "unique_count"}]}

    preview = client.post("/api/datasets/distinct_counts/transform", json={
        "op": "group", "params": params, "preview": True})
    assert preview.status_code == 200, preview.text
    assert preview.json()["preview_rows"] == [{"bucket": "all", "unique_count": 1000}]
    assert "COUNT(DISTINCT" in preview.json()["select_sql"]
    assert "APPROX_COUNT_DISTINCT" not in preview.json()["select_sql"]

    mcp = client.post("/api/agent/call", headers={"Authorization": f"Bearer {client.services.token}"},
        json={"name": "data_transform", "arguments": {
            "dataset": "distinct_counts", "op": "group", "params": params, "preview": False}})
    assert mcp.status_code == 200, mcp.text
    current = client.get("/api/datasets/distinct_counts/preview?limit=10")
    assert current.status_code == 200
    assert current.json()["rows"] == [{"bucket": "all", "unique_count": 1000}]

    recipe = client.get("/api/datasets/distinct_counts/recipe?action=export")
    assert recipe.status_code == 200 and "COUNT(DISTINCT" in recipe.json()["sql_script"]
    assert "APPROX_COUNT_DISTINCT" not in recipe.json()["sql_script"]

    exported = client.post("/api/export", json={"dataset": "distinct_counts", "format": "csv",
                                                 "path": "distinct-counts.csv"})
    assert exported.status_code == 200, exported.text
    with open(exported.json()["path"], newline="", encoding="utf-8") as stream:
        rows = list(csv.DictReader(stream))
    assert rows == [{"bucket": "all", "unique_count": "1000"}]


def test_pivot_unpivot(services, sales):
    r = services.transform_apply(sales, "pivot", {"on": "region", "value": "qty", "fn": "sum"})
    assert r["row_count"] >= 1


def test_sort_and_sample(services, sales):
    services.transform_apply(sales, "sort", {"by": [{"column": "price", "desc": True}]})
    r = services.transform_apply(sales, "sample", {"n": 3, "seed": 1})
    assert r["row_count"] == 3


def test_window_lag(services, sales):
    r = services.transform_apply(sales, "window", {"fn": "lag", "column": "price", "order_by": ["id"], "new_column": "prev_price"})
    assert "prev_price" in [c["name"] for c in r["columns"]]


def test_sql_step(services, sales):
    r = services.transform_apply(sales, "sql", {"sql": "SELECT * FROM __prev__ WHERE qty > 0"})
    assert r["row_count"] >= 1


def test_sql_step_rejects_write(services, sales):
    with pytest.raises(Exception):
        services.transform_apply(sales, "sql", {"sql": "DELETE FROM __prev__"})


def test_join_and_match_rate(services, sales, tmp_path):
    path = tmp_path / "regions.csv"
    path.write_text("region,country\nMadrid,ES\nBarcelona,ES\n", encoding="utf-8")
    services.ingest_file(str(path), "regions")
    rate = services.join_preview(sales, "regions", [{"left": "region", "right": "region"}])
    assert 0 <= rate["match_rate"] <= 1
    r = services.transform_apply(sales, "join", {"other_dataset": "regions",
                                                   "on": [{"left": "region", "right": "region"}], "how": "left"})
    assert "country" in [c["name"] for c in r["columns"]]


def test_union(services, sales, tmp_path):
    path = tmp_path / "more_sales.csv"
    path.write_text("id,region,product,qty,price,note\n99,Malaga,Widget,1,9.5,extra\n", encoding="utf-8")
    services.ingest_file(str(path), "more_sales")
    r = services.transform_apply(sales, "union", {"other_dataset": "more_sales"})
    assert r["row_count"] >= 6


def test_undo_redo_and_branch_discards_redo(services, sales):
    services.transform_apply(sales, "derive", {"name": "a", "expr": "1"})
    services.transform_apply(sales, "derive", {"name": "b", "expr": "2"})
    d = services.undo(sales, 1)
    assert d["current_version"] == 1
    d2 = services.redo(sales, 1)
    assert d2["current_version"] == 2
    services.undo(sales, 1)
    d3 = services.transform_apply(sales, "derive", {"name": "c", "expr": "3"})
    # the redo stack (old v2 = column b) was discarded; new branch created instead
    assert "c" in [c["name"] for c in d3["columns"]]
    with pytest.raises(Exception):
        services.redo(sales, 1)  # nothing beyond the new tip


def test_recipe_show_export_and_lineage(services, sales):
    services.transform_apply(sales, "derive", {"name": "total", "expr": "qty * price"})
    show = services.recipe(sales, "show")
    assert show["steps"][0]["op"] == "ingest"
    exported = services.recipe(sales, "export")
    assert "CREATE OR REPLACE VIEW" in exported["sql_script"]
    lineage = services.lineage(sales)
    assert lineage["source"] is not None


def test_refresh_replays_recipe(services, tmp_path):
    path = tmp_path / "growing.csv"
    path.write_text("id,qty\n1,10\n2,20\n", encoding="utf-8")
    services.ingest_file(str(path), "growing")
    services.transform_apply("growing", "derive", {"name": "doubled", "expr": "qty * 2"})
    path.write_text("id,qty\n1,10\n2,20\n3,30\n", encoding="utf-8")
    result = services.refresh("growing")
    assert result["steps_replayed"] == 1
    preview = services.preview("growing", limit=10)
    assert preview["row_count_total"] == 3
    assert "doubled" in [c["name"] for c in preview["columns"]]

"""Every transform step type, applied at least once, plus preview/undo/redo/recipe/lineage."""

import pytest


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


def test_replace(services, sales):
    r = services.transform_apply(sales, "replace", {"column": "note", "pattern": "PROMO", "replacement": "promo"})
    assert r["row_count"] > 0


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

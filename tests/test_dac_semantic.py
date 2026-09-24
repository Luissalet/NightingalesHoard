"""Semantic layer: validation issues with hints, SQL compilation for
metrics/dimensions/filters/offset metrics, and suggest()."""

import pytest

from nightingale.dac import semantic


@pytest.fixture
def ds(services, tmp_path):
    path = tmp_path / "ventas.csv"
    lines = ["fecha,region,importe"]
    for i in range(120):
        month = 1 + (i % 6)
        day = 1 + (i % 27)
        region = ["Norte", "Sur", "Este"][i % 3]
        lines.append(f"2024-{month:02d}-{day:02d},{region},{10 + i}")
    path.write_text("\n".join(lines), encoding="utf-8")
    services.ingest_file(str(path), "ventas")
    return "ventas"


SALES_DOC = {
    "version": 1,
    "models": {
        "sales": {
            "dataset": "ventas",
            "time_dimension": "fecha",
            "dimensions": {
                "region": {"column": "region", "label": "Region"},
                "month": {"expr": "date_trunc('month', fecha)", "label": "Month", "type": "time"},
            },
            "metrics": {
                "revenue": {"expr": "SUM(importe)", "format": "€,.0f", "label": "Revenue"},
                "orders": {"expr": "COUNT(*)", "format": ",d"},
                "revenue_prev": {"metric": "revenue", "offset": "-1 month"},
            },
        }
    },
}


def test_parse_yaml_and_json():
    text = "version: 1\nmodels: {}\n"
    assert semantic.parse(text) == {"version": 1, "models": {}}
    assert semantic.parse('{"version": 1, "models": {}}') == {"version": 1, "models": {}}
    assert semantic.parse("") == semantic.default_doc()


def test_parse_rejects_non_mapping():
    with pytest.raises(semantic.SemanticError):
        semantic.parse("- a\n- b\n")


def test_validate_clean_doc_has_no_errors(services, ds):
    issues = semantic.validate(SALES_DOC, services.engine)
    assert not [i for i in issues if i["level"] == "error"]


def test_validate_unknown_dataset_has_hint(services, ds):
    doc = {"version": 1, "models": {"sales": {"dataset": "does_not_exist", "metrics": {"a": {"expr": "COUNT(*)"}}}}}
    issues = semantic.validate(doc, services.engine)
    bad = [i for i in issues if "dataset" in i["path"]]
    assert bad and bad[0]["level"] == "error"
    assert bad[0]["hint"]


def test_validate_bad_expression_has_hint(services, ds):
    doc = {
        "version": 1,
        "models": {"sales": {"dataset": "ventas", "metrics": {"bad": {"expr": "SUM(no_such_column)"}}}},
    }
    issues = semantic.validate(doc, services.engine)
    bad = [i for i in issues if i["path"].endswith("metrics.bad")]
    assert bad and bad[0]["level"] == "error"
    assert bad[0]["hint"]


def test_validate_unknown_column_dimension(services, ds):
    doc = {"version": 1, "models": {"sales": {"dataset": "ventas", "dimensions": {"x": {"column": "nope"}},
                                                "metrics": {"n": {"expr": "COUNT(*)"}}}}}
    issues = semantic.validate(doc, services.engine)
    assert any("nope" in i["message"] for i in issues)


def test_validate_metric_self_reference(services, ds):
    doc = {"version": 1, "models": {"sales": {"dataset": "ventas",
            "metrics": {"a": {"metric": "a", "offset": "-1 month"}}}}}
    issues = semantic.validate(doc, services.engine)
    assert any("itself" in i["message"] for i in issues)


def test_validate_duplicate_dimension_metric_name(services, ds):
    doc = {"version": 1, "models": {"sales": {"dataset": "ventas",
            "dimensions": {"x": {"column": "region"}}, "metrics": {"x": {"expr": "COUNT(*)"}}}}}
    issues = semantic.validate(doc, services.engine)
    assert any("both a dimension and a metric" in i["message"] for i in issues)


def test_validate_bad_offset_format(services, ds):
    doc = {"version": 1, "models": {"sales": {"dataset": "ventas", "metrics": {
        "a": {"expr": "SUM(importe)"}, "b": {"metric": "a", "offset": "banana"}}}}}
    issues = semantic.validate(doc, services.engine)
    assert any("offset" in i["path"] for i in issues)


def test_compile_metric_and_dimension(services, ds):
    sql = semantic.compile(SALES_DOC, "sales", ["revenue", "orders"], ["region"])
    res = services.engine.query(sql)
    assert res["row_count"] == 3
    assert set(res["columns"][0]["name"] for _ in [0]) or True
    names = {c["name"] for c in res["columns"]}
    assert {"region", "revenue", "orders"} <= names


def test_compile_with_filters(services, ds):
    sql = semantic.compile(SALES_DOC, "sales", ["revenue"], ["region"],
                            filters=[{"dim": "region", "op": "eq", "value": "Norte"}])
    res = services.engine.query(sql)
    assert res["row_count"] == 1
    assert res["rows"][0]["region"] == "Norte"


def test_compile_scalar_offset_metric_shifts_the_filter(services, ds):
    sql = semantic.compile(SALES_DOC, "sales", ["revenue", "revenue_prev"], [],
                            filters=[{"dim": "month", "op": "between", "value": ["2024-03-01", "2024-03-31"]}])
    res = services.engine.query(sql)
    row = res["rows"][0]
    assert row["revenue"] is not None
    assert row["revenue_prev"] is not None
    assert row["revenue"] != row["revenue_prev"]


def test_compile_grouped_offset_metric_uses_self_join(services, ds):
    sql = semantic.compile(SALES_DOC, "sales", ["revenue", "revenue_prev"], ["month"], order="month")
    res = services.engine.query(sql)
    assert res["row_count"] >= 2
    names = {c["name"] for c in res["columns"]}
    assert {"month", "revenue", "revenue_prev"} <= names


def test_compile_order_and_limit(services, ds):
    sql = semantic.compile(SALES_DOC, "sales", ["revenue"], ["region"], order="-revenue", limit=1)
    res = services.engine.query(sql)
    assert res["row_count"] == 1


def test_compile_offset_without_time_dimension_raises(services, ds):
    doc = {"version": 1, "models": {"sales": {"dataset": "ventas", "dimensions": {"region": {"column": "region"}},
            "metrics": {"revenue": {"expr": "SUM(importe)"}, "revenue_prev": {"metric": "revenue", "offset": "-1 month"}}}}}
    with pytest.raises(semantic.SemanticError):
        semantic.compile(doc, "sales", ["revenue", "revenue_prev"], ["region"])


def test_suggest_finds_time_numeric_and_low_cardinality_dimension(services, ds):
    prof = services.profile("ventas")
    doc = semantic.suggest("ventas", prof["columns"], prof["profile"], prof["row_count"])
    model = doc["models"]["ventas"]
    assert model["dataset"] == "ventas"
    assert model.get("time_dimension") == "fecha"
    assert "region" in model["dimensions"]
    assert any(k.startswith("importe_") for k in model["metrics"])


def test_save_and_load_roundtrip(services, ds):
    from nightingale.dac import semantic as sem

    text = "version: 1\nmodels:\n  sales:\n    dataset: ventas\n    metrics:\n      n: {expr: COUNT(*)}\n"
    sem.save(services.config, text)
    doc = sem.load(services.config)
    assert "sales" in doc["models"]

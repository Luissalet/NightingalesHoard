"""Dashboard spec: validation with hints, loop/condition/template expansion,
and rendering (per-widget SQL and errors, never a whole-dashboard failure)."""

import pytest

from nightingale.dac import exprs, render, semantic, spec, store


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
    semantic_text = """
version: 1
models:
  sales:
    dataset: ventas
    time_dimension: fecha
    dimensions:
      region: {column: region, label: Region}
      month: {expr: "date_trunc('month', fecha)", label: Month, type: time}
    metrics:
      revenue: {expr: "SUM(importe)", format: "€,.0f", label: Revenue}
      orders: {expr: "COUNT(*)", format: ",d"}
      revenue_prev: {metric: revenue, offset: "-1 month"}
"""
    services.dac_semantic_put(semantic_text)
    return "ventas"


DASHBOARD_YAML = """
version: 1
name: Board meeting
model: sales
filters:
  - name: date_range
    type: date-range
    dimension: month
    default: ["2024-01-01", "2024-06-30"]
  - name: region
    type: select
    dimension: region
    default: all
tabs:
  - name: Overview
    rows:
      - cols: 3
        widgets:
          - {type: metric, metric: revenue, compare: revenue_prev, title: Revenue}
          - {type: metric, metric: orders}
          - {type: chart, kind: line, x: month, y: revenue, color: region}
      - for: {each: region, in: {dimension: region, top: 2, by: revenue}}
        widgets:
          - {type: chart, kind: bar, x: month, y: revenue, filter: {region: "{{ each }}"}, title: "{{ each }}"}
      - if: "filters.region == 'all'"
        widgets:
          - {type: text, markdown: "Showing every region."}
      - widgets:
          - {type: table, dimensions: [region], metrics: [revenue, orders], sort: "-revenue", limit: 10}
"""


# ---- exprs -------------------------------------------------------------------

def test_safe_eval_basic_conditions():
    ctx = {"filters": {"region": "all"}, "each": "Norte"}
    assert exprs.safe_eval("filters.region == 'all'", ctx) is True
    assert exprs.safe_eval("filters.region != 'all'", ctx) is False
    assert exprs.safe_eval("each in ['Norte', 'Sur']", ctx) is True
    assert exprs.safe_eval("not (filters.region == 'x')", ctx) is True
    assert exprs.safe_eval("filters.region == 'x' and each == 'Norte'", ctx) is False


def test_safe_eval_rejects_unknown_variable():
    with pytest.raises(exprs.ExprError):
        exprs.safe_eval("filters.nope == 1", {"filters": {}, "each": None})


def test_safe_eval_never_executes_arbitrary_code():
    with pytest.raises(exprs.ExprError):
        exprs.safe_eval("__import__('os').system('echo hi')", {"filters": {}, "each": None})


def test_render_template():
    assert exprs.render_template("Hello {{ each }}!", {"each": "Norte"}) == "Hello Norte!"
    assert exprs.render_template("{{ filters.region }}", {"filters": {"region": "all"}}) == "all"
    assert exprs.render_template("plain text", {}) == "plain text"


# ---- spec.validate -------------------------------------------------------------

def test_spec_validate_clean(services, ds):
    doc = spec.parse(DASHBOARD_YAML)
    semantic_doc = semantic.load(services.config)
    issues = spec.validate(doc, semantic_doc)
    assert not [i for i in issues if i["level"] == "error"], issues


def test_spec_validate_unknown_metric_has_hint(services, ds):
    doc = spec.parse(DASHBOARD_YAML.replace("metric: revenue,", "metric: does_not_exist,"))
    issues = spec.validate(doc, semantic.load(services.config))
    bad = [i for i in issues if "does_not_exist" in i["message"]]
    assert bad and bad[0]["hint"]


def test_spec_validate_unknown_template_variable(services, ds):
    bad_yaml = DASHBOARD_YAML.replace('"{{ each }}"', '"{{ nope }}"')
    doc = spec.parse(bad_yaml)
    issues = spec.validate(doc, semantic.load(services.config))
    assert any("nope" in i["message"] for i in issues)


def test_spec_validate_bad_cols(services, ds):
    doc = spec.parse(DASHBOARD_YAML.replace("cols: 3", "cols: 9"))
    issues = spec.validate(doc, semantic.load(services.config))
    assert any("cols" in i["path"] for i in issues)


def test_spec_validate_unknown_model():
    doc = spec.parse(DASHBOARD_YAML.replace("model: sales", "model: nope"))
    issues = spec.validate(doc, {"version": 1, "models": {}})
    assert any(i["path"] == "model" for i in issues)


# ---- render ---------------------------------------------------------------------

def test_render_produces_metric_chart_table_and_text(services, ds):
    put = store.put(services.config, DASHBOARD_YAML)
    doc = put["doc"]
    result = render.render(services, doc, {})
    assert result["name"] == "Board meeting"
    tab = result["tabs"][0]
    kinds = [w["type"] for row in tab["rows"] for w in row["widgets"]]
    assert "metric" in kinds and "chart" in kinds and "table" in kinds and "text" in kinds

    metric_widget = tab["rows"][0]["widgets"][0]
    assert metric_widget["value"] is not None
    assert metric_widget["compare_value"] is not None
    assert metric_widget["sql"]

    table_widget = [w for row in tab["rows"] for w in row["widgets"] if w["type"] == "table"][0]
    assert len(table_widget["rows"]) == 3  # 3 regions


def test_render_for_loop_expands_top_n_regions(services, ds):
    put = store.put(services.config, DASHBOARD_YAML)
    result = render.render(services, put["doc"], {})
    tab = result["tabs"][0]
    loop_rows = tab["rows"][1:3]  # the two expanded "for" rows (top=2)
    titles = [row["widgets"][0]["title"] for row in loop_rows]
    assert len(titles) == 2
    assert all(t for t in titles)


def test_render_if_condition_hides_row_when_false(services, ds):
    put = store.put(services.config, DASHBOARD_YAML)
    result = render.render(services, put["doc"], {"region": "Norte"})
    tab = result["tabs"][0]
    all_widgets = [w for row in tab["rows"] for w in row["widgets"]]
    assert not any(w.get("markdown") == "Showing every region." for w in all_widgets)

    result_all = render.render(services, put["doc"], {})
    tab_all = result_all["tabs"][0]
    all_widgets_all = [w for row in tab_all["rows"] for w in row["widgets"]]
    assert any(w.get("markdown") == "Showing every region." for w in all_widgets_all)


def test_render_bad_widget_carries_its_own_error(services, ds):
    doc = spec.parse(DASHBOARD_YAML)
    doc["tabs"][0]["rows"][0]["widgets"][0]["metric"] = "no_such_metric"
    result = render.render(services, doc, {})
    widgets = [w for row in result["tabs"][0]["rows"] for w in row["widgets"]]
    bad = [w for w in widgets if w["type"] == "metric" and "error" in w]
    assert bad
    # the rest of the dashboard still rendered
    assert any(w["type"] == "table" and "rows" in w for w in widgets)


def test_render_select_filter_lists_options(services, ds):
    put = store.put(services.config, DASHBOARD_YAML)
    result = render.render(services, put["doc"], {})
    region_filter = [f for f in result["filters"] if f["name"] == "region"][0]
    assert set(region_filter["options"]) == {"Norte", "Sur", "Este"}

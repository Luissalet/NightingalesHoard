"""Charts (every kind produces data + a Vega-Lite spec + a non-trivial PNG) and dashboards."""

import base64

import pytest

from nightingale.workbench.charts import CHART_KINDS


@pytest.fixture
def ds(services, tmp_path):
    path = tmp_path / "c.csv"
    path.write_text(
        "category,value,extra\n" + "\n".join(f"{'ABC'[i % 3]},{i * 3 % 17},{i % 2}" for i in range(40)),
        encoding="utf-8",
    )
    services.ingest_file(str(path), "c")
    return "c"


CHART_SPECS = {
    "bar": {"x": "category", "y": "value"},
    "grouped_bar": {"x": "category", "y": "value", "color": "extra"},
    "stacked_bar": {"x": "category", "y": "value", "color": "extra"},
    "line": {"x": "category", "y": "value"},
    "area": {"x": "category", "y": "value"},
    "scatter": {"x": "value", "y": "extra"},
    "histogram": {"x": "value"},
    "box": {"x": "category", "y": "value"},
    "heatmap": {"x": "category", "y": "extra"},
    "pie": {"x": "category", "y": "value"},
    "donut": {"x": "category", "y": "value"},
}


@pytest.mark.parametrize("kind", CHART_KINDS)
def test_every_chart_kind_renders(services, ds, kind):
    spec = CHART_SPECS[kind]
    result = services.chart_create(ds, kind, image=True, **spec)
    assert result["row_count"] > 0
    assert "encoding" in result["vega_lite"] or "mark" in result["vega_lite"]
    png = base64.b64decode(result["image_base64"])
    assert png[:8] == b"\x89PNG\r\n\x1a\n"
    assert len(png) > 500


def test_chart_get_and_list_and_delete(services, ds):
    created = services.chart_create(ds, "bar", x="category", y="value")
    fetched = services.chart_get(created["chart_id"])
    assert fetched["name"] == created["name"]
    assert len(services.chart_list()["charts"]) == 1
    services.chart_delete(created["chart_id"])
    assert len(services.chart_list()["charts"]) == 0


def test_dashboard_with_chart_and_kpi(services, ds):
    chart = services.chart_create(ds, "bar", x="category", y="value")
    dash = services.dashboard_create("Overview", items=[{"type": "chart", "chart_id": chart["chart_id"]}])
    services.dashboard_add(dash["dashboard_id"], {"type": "kpi", "dataset": ds, "expr": "COUNT(*)", "label": "rows"})
    rendered = services.dashboard_get(dash["dashboard_id"])
    assert len(rendered["items"]) == 2
    kpi_item = [i for i in rendered["items"] if i["type"] == "kpi"][0]
    assert kpi_item["value"] == 40
    assert len(services.dashboard_list()["dashboards"]) == 1

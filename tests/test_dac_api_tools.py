"""Dashboards as code: HTTP API round trip and the data_dashboard tool
actions (store history/diff, export to items, and the full agent surface)."""

import asyncio

import pytest

from nightingale.agent_tools import TOOLS, call_tool


@pytest.fixture
def ds(client):
    csv = "fecha,region,importe\n" + "\n".join(
        f"2024-{1 + (i % 6):02d}-{1 + (i % 27):02d},{'ABC'[i % 3]},{10 + i}" for i in range(90)
    )
    r = client.post("/api/sources/ingest", json={"kind": "paste", "text": csv, "name": "ventas", "fmt": "csv"})
    assert r.status_code == 200, r.text
    return "ventas"


SEMANTIC_YAML = """
version: 1
models:
  sales:
    dataset: ventas
    time_dimension: fecha
    dimensions:
      region: {column: region}
      month: {expr: "date_trunc('month', fecha)", type: time}
    metrics:
      revenue: {expr: "SUM(importe)"}
      orders: {expr: "COUNT(*)"}
"""

DASHBOARD_YAML = """
version: 1
name: API dashboard
model: sales
tabs:
  - name: Overview
    rows:
      - widgets:
          - {type: metric, metric: revenue}
          - {type: chart, kind: bar, x: region, y: revenue}
"""


def test_tool_cap_is_18():
    assert len(TOOLS) <= 18


def test_api_semantic_and_dashboard_round_trip(client, ds):
    r = client.get("/api/dac/semantic/suggest", params={"dataset": ds})
    assert r.status_code == 200
    assert ds in r.json()["doc"]["models"]

    r = client.put("/api/dac/semantic", json={"text": SEMANTIC_YAML})
    assert r.status_code == 200
    assert r.json()["issues"] == []

    r = client.get("/api/dac/semantic")
    assert r.status_code == 200
    assert "sales" in r.json()["doc"]["models"]

    r = client.post("/api/dac/dashboards", json={"text": DASHBOARD_YAML})
    assert r.status_code == 200
    body = r.json()
    slug = body["slug"]
    assert body["issues"] == []

    r = client.get("/api/dac/dashboards")
    assert r.status_code == 200
    assert any(d["slug"] == slug for d in r.json()["dashboards"])

    r = client.get(f"/api/dac/dashboards/{slug}")
    assert r.status_code == 200
    assert r.json()["doc"]["name"] == "API dashboard"

    r = client.post(f"/api/dac/dashboards/{slug}/validate", json=None)
    assert r.status_code == 200
    assert r.json()["issues"] == []

    r = client.post(f"/api/dac/dashboards/{slug}/render", json={"filters": {}})
    assert r.status_code == 200
    rendered = r.json()
    assert rendered["tabs"][0]["rows"][0]["widgets"][0]["value"] is not None

    r = client.put(f"/api/dac/dashboards/{slug}", json={"text": DASHBOARD_YAML.replace("API dashboard", "API dashboard v2")})
    assert r.status_code == 200

    r = client.get(f"/api/dac/dashboards/{slug}/history")
    assert r.status_code == 200
    history = r.json()["history"]
    assert len(history) == 1

    r = client.get(f"/api/dac/dashboards/{slug}/diff", params={"a": history[0]["id"], "b": "current"})
    assert r.status_code == 200
    assert "API dashboard v2" in r.json()["diff"]

    r = client.post(f"/api/dac/dashboards/{slug}/export", json={"filters": {}})
    assert r.status_code == 200
    export = r.json()
    assert export["dashboard_id"]

    r = client.post("/api/dac/dashboards/import", json={"dashboard_id": export["dashboard_id"]})
    assert r.status_code == 200
    assert r.json()["warnings"]

    r = client.delete(f"/api/dac/dashboards/{slug}")
    assert r.status_code == 200
    r = client.get(f"/api/dac/dashboards/{slug}")
    assert r.status_code == 404


def test_api_validation_error_carries_hints(client, ds):
    r = client.put("/api/dac/semantic", json={"text": SEMANTIC_YAML.replace("importe", "not_a_column")})
    assert r.status_code == 200
    issues = r.json()["issues"]
    assert issues
    assert all("hint" in i for i in issues)


def test_agent_tool_actions(services):
    csv = "fecha,region,importe\n" + "\n".join(
        f"2024-{1 + (i % 6):02d}-{1 + (i % 27):02d},{'ABC'[i % 3]},{10 + i}" for i in range(90)
    )
    services.ingest_text(csv, "ventas", "csv")

    async def run():
        r = await call_tool(services, "data_dashboard", {"action": "semantic_suggest", "dataset": "ventas"})
        assert "ventas" in r["doc"]["models"]

        r = await call_tool(services, "data_dashboard", {"action": "semantic_put", "text": SEMANTIC_YAML})
        assert r["issues"] == []

        r = await call_tool(services, "data_dashboard", {"action": "code_put", "text": DASHBOARD_YAML})
        assert r["issues"] == []
        slug = r["slug"]

        r = await call_tool(services, "data_dashboard", {"action": "code_validate", "slug": slug})
        assert r["issues"] == []

        r = await call_tool(services, "data_dashboard", {"action": "code_render", "slug": slug, "filters": {}})
        assert r["tabs"][0]["rows"][0]["widgets"][0]["value"] is not None

        r = await call_tool(services, "data_dashboard", {"action": "code_list"})
        assert any(d["slug"] == slug for d in r["dashboards"])

        r = await call_tool(services, "data_dashboard", {"action": "code_get", "slug": slug})
        assert r["doc"]["name"] == "API dashboard"

        r = await call_tool(services, "data_dashboard", {"action": "code_export", "slug": slug})
        assert r["dashboard_id"]

        with pytest.raises(ValueError):
            await call_tool(services, "data_dashboard", {"action": "code_render"})

    asyncio.run(run())

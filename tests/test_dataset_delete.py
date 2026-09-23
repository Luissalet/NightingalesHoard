"""Deleting a dataset: through Services, the HTTP API, and the MCP tool
(data_ingest, action="delete" — data_list stays read-only and data_refresh
never grew a delete action, per the family's <=18-tool cap)."""

import asyncio

import pytest

from nightingale.agent_tools import call_tool


@pytest.fixture
def ds(services, tmp_path):
    path = tmp_path / "d.csv"
    path.write_text("a,b\n1,2\n3,4\n5,6\n", encoding="utf-8")
    services.ingest_file(str(path), "d")
    return "d"


def test_dataset_dependents_empty(services, ds):
    deps = services.dataset_dependents(ds)
    assert deps == {"dataset": ds, "charts": [], "dashboards": [], "models": [], "has_dependents": False}


def test_delete_dataset_removes_everything(services, ds):
    result = services.dataset_delete(ds)
    assert result["ok"] is True
    names = [d["name"] for d in services.list_datasets()["datasets"]]
    assert ds not in names
    with pytest.raises(LookupError):
        services._dataset_row(ds)


def test_delete_blocked_by_dependent_chart_then_forced(services, ds):
    services.chart_create(ds, "bar", x="a", y="b")
    with pytest.raises(ValueError):
        services.dataset_delete(ds)
    deps = services.dataset_dependents(ds)
    assert deps["has_dependents"] and len(deps["charts"]) == 1
    result = services.dataset_delete(ds, force=True)
    assert result["ok"] is True


def test_delete_blocked_by_dependent_model(services, tmp_path):
    path = tmp_path / "d_model.csv"
    path.write_text("a,b\n" + "\n".join(f"{i},{i * 2}" for i in range(20)), encoding="utf-8")
    services.ingest_file(str(path), "d_model")
    services.model_train("d_model", "b", ["a"], write_to="none")
    deps = services.dataset_dependents("d_model")
    assert deps["has_dependents"] and len(deps["models"]) == 1
    with pytest.raises(ValueError):
        services.dataset_delete("d_model")


def test_dashboard_survives_dataset_delete_with_warning(services, ds):
    chart = services.chart_create(ds, "bar", x="a", y="b")
    dash = services.dashboard_create("dash", items=[{"type": "chart", "chart_id": chart["chart_id"]}])
    deps = services.dataset_dependents(ds)
    assert deps["dashboards"] == [{"id": dash["dashboard_id"], "name": "dash", "items": 1}]
    services.dataset_delete(ds, force=True)
    # dashboard_get tolerates the now-missing chart instead of erroring
    rendered = services.dashboard_get(dash["dashboard_id"])
    assert rendered["items"][0]["chart"] is None


def test_api_dependents_and_delete_routes(client, tmp_path):
    path = tmp_path / "api_d2.csv"
    path.write_text("a,b\n1,2\n", encoding="utf-8")
    client.services.ingest_file(str(path), "api_d2")

    r = client.get("/api/datasets/api_d2/dependents")
    assert r.status_code == 200
    assert r.json()["has_dependents"] is False

    r = client.delete("/api/datasets/api_d2")
    assert r.status_code == 200
    assert r.json()["ok"] is True

    names = [d["name"] for d in client.get("/api/datasets").json()["datasets"]]
    assert "api_d2" not in names


def test_api_delete_with_dependents_requires_force(client, tmp_path):
    path = tmp_path / "api_d3.csv"
    path.write_text("a,b\n1,2\n", encoding="utf-8")
    client.services.ingest_file(str(path), "api_d3")
    client.services.chart_create("api_d3", "bar", x="a", y="b")

    r = client.delete("/api/datasets/api_d3")
    assert r.status_code == 400

    r = client.delete("/api/datasets/api_d3?force=true")
    assert r.status_code == 200


def test_mcp_data_ingest_delete_action(services, ds):
    async def run():
        return await call_tool(services, "data_ingest", {"action": "delete", "name": ds})

    result = asyncio.run(run())
    assert result["ok"] is True
    names = [d["name"] for d in services.list_datasets()["datasets"]]
    assert ds not in names


def test_mcp_data_ingest_delete_requires_name(services):
    async def run():
        return await call_tool(services, "data_ingest", {"action": "delete"})

    with pytest.raises(ValueError):
        asyncio.run(run())

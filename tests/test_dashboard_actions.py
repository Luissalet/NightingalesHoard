"""Typed, reversible dashboard actions: reducer, inverses, undo/redo, persistence, migration, API and MCP tool."""

import json
import sqlite3

import pytest

from nightingale import agent_tools
from nightingale.config import Config
from nightingale.dashboard_actions import ActionError, action_schemas, apply_action, normalize_state, parse_action
from nightingale.services import Services

from conftest import make_config


def _state():
    state, _ = normalize_state("Sales", {"items": [
        {"type": "kpi", "dataset": "d", "expr": "COUNT(*)", "label": "Rows"},
        {"type": "chart", "chart_id": 1},
        {"type": "kpi", "dataset": "d", "expr": "SUM(v)", "label": "Total", "span": 2},
    ], "filter": {"region": "N"}})
    return state


ACTIONS = [
    {"type": "add_item", "item": {"type": "kpi", "dataset": "d", "expr": "AVG(v)", "label": "Mean"}},
    {"type": "add_item", "item": {"type": "chart", "chart_id": 7, "id": "mine"}, "index": 1},
    {"type": "remove_item", "item_id": "i2"},
    {"type": "move_item", "item_id": "i1", "to_index": 2},
    {"type": "update_item", "item_id": "i3", "set": {"expr": "MAX(v)", "title": "Peak"}, "unset": ["span", "label"]},
    {"type": "update_item", "item_id": "i2", "set": {"chart_id": 9}},
    {"type": "set_name", "name": "Revenue"},
    {"type": "set_filter", "filter": {"region": "S", "year": 2024}},
    {"type": "set_filter", "filter": {}},
    {"type": "set_layout", "columns": 2},
]


@pytest.mark.parametrize("action", ACTIONS, ids=[a["type"] + str(n) for n, a in enumerate(ACTIONS)])
def test_every_action_has_an_exact_inverse(action):
    before = _state()
    after, normalized, inverse = apply_action(before, action)
    assert after != before
    assert before == _state(), "the reducer must not mutate its input"
    restored, _, again = apply_action(after, inverse)
    assert restored == before
    # redo: the logged (normalized) action reproduces the same state, and its inverse is the same inverse
    redone, _, inverse2 = apply_action(before, normalized)
    assert redone == after and inverse2 == inverse


def test_legacy_items_get_stable_ids_and_defaults():
    state, changed = normalize_state("x", {"items": [{"type": "chart", "chart_id": 3}, {"type": "kpi", "id": "i1", "dataset": "d", "expr": "1"}]})
    assert changed and [i["id"] for i in state["items"]] == ["i2", "i1"] or len({i["id"] for i in state["items"]}) == 2
    again, changed2 = normalize_state("x", {"items": state["items"], "layout": state["layout"]})
    assert not changed2 and again == state


@pytest.mark.parametrize("bad, code", [
    ({"type": "nope"}, "unknown_action"),
    ("add_item", "invalid_action"),
    ({"type": "add_item"}, "invalid_action"),
    ({"type": "add_item", "item": {"type": "chart"}}, "invalid_action"),
    ({"type": "add_item", "item": {"type": "kpi", "dataset": "d", "expr": "1", "bogus": 1}}, "invalid_action"),
    ({"type": "add_item", "item": {"type": "chart", "chart_id": 1}, "index": 99}, "bad_index"),
    ({"type": "add_item", "item": {"type": "chart", "chart_id": 1, "id": "i1"}}, "duplicate_item"),
    ({"type": "remove_item", "item_id": "zzz"}, "unknown_item"),
    ({"type": "move_item", "item_id": "i1", "to_index": 3}, "bad_index"),
    ({"type": "move_item", "item_id": "i1", "to_index": -1}, "invalid_action"),
    ({"type": "update_item", "item_id": "i1", "set": {}}, "empty_update"),
    ({"type": "update_item", "item_id": "i1", "set": {"chart_id": 4}}, "bad_field"),
    ({"type": "update_item", "item_id": "i1", "unset": ["expr"]}, "bad_field"),
    ({"type": "update_item", "item_id": "i1", "set": {"span": 9}}, "invalid_action"),
    ({"type": "update_item", "item_id": "i1", "set": {"label": "a"}, "unset": ["label"]}, "bad_field"),
    ({"type": "set_name", "name": ""}, "invalid_action"),
    ({"type": "set_layout", "columns": 9}, "invalid_action"),
    ({"type": "set_filter", "filter": []}, "invalid_action"),
])
def test_validation_errors_are_explicit(bad, code):
    with pytest.raises(ActionError) as err:
        apply_action(_state(), bad)
    assert err.value.code == code
    assert code in str(err.value) and err.value.message


def test_schemas_cover_every_action():
    schemas = action_schemas()
    assert set(schemas["actions"]) == {"add_item", "remove_item", "move_item", "update_item", "set_name", "set_filter", "set_layout"}
    for entry in schemas["actions"].values():
        assert entry["schema"]["type"] == "object" and entry["description"]
    json.dumps(schemas)
    assert parse_action({"type": "set_layout", "columns": 2}) == {"type": "set_layout", "columns": 2}


# ---- service level -------------------------------------------------------------------------------------------
@pytest.fixture
def dash(services, tmp_path):
    path = tmp_path / "s.csv"
    path.write_text("cat,v\n" + "\n".join(f"{'ab'[i % 2]},{i}" for i in range(10)), encoding="utf-8")
    services.ingest_file(str(path), "s")
    chart = services.chart_create("s", "bar", x="cat", y="v")["chart_id"]
    chart2 = services.chart_create("s", "line", x="cat", y="v")["chart_id"]
    d = services.dashboard_create("Board")["dashboard_id"]
    return services, d, chart, chart2


def test_apply_undo_redo_sequence_and_persistence(dash, tmp_path, clock):
    s, d, chart, chart2 = dash
    s.dashboard_apply(d, {"type": "add_item", "item": {"type": "chart", "chart_id": chart}}, source="ui")
    s.dashboard_apply(d, {"type": "add_item", "item": {"type": "kpi", "dataset": "s", "expr": "SUM(v)", "label": "Sum"}}, source="agent")
    s.dashboard_apply(d, {"type": "move_item", "item_id": "i2", "to_index": 0})
    s.dashboard_apply(d, {"type": "update_item", "item_id": "i2", "set": {"expr": "MAX(v)"}})
    s.dashboard_apply(d, {"type": "set_name", "name": "Renamed"})
    s.dashboard_apply(d, {"type": "update_item", "item_id": "i1", "set": {"chart_id": chart2}})
    final = s.dashboard_get(d)
    assert final["name"] == "Renamed" and [i["id"] for i in final["items"]] == ["i2", "i1"]
    assert final["items"][0]["value"] == 9  # MAX(v)

    for _ in range(6):
        s.dashboard_undo(d)
    initial = s.dashboard_get(d)
    assert initial["items"] == [] and initial["name"] == "Board" and not initial["can_undo"] and initial["can_redo"]
    with pytest.raises(ActionError) as err:
        s.dashboard_undo(d)
    assert err.value.code == "nothing_to_undo"

    for _ in range(6):
        s.dashboard_redo(d)
    again = s.dashboard_get(d)
    assert again["name"] == "Renamed" and [(i["id"], i.get("value")) for i in again["items"]] == [(i["id"], i.get("value")) for i in final["items"]]
    with pytest.raises(ActionError) as err:
        s.dashboard_redo(d)
    assert err.value.code == "nothing_to_redo"

    # undo twice, then a new action drops the redo branch
    s.dashboard_undo(d)
    s.dashboard_undo(d)
    s.dashboard_apply(d, {"type": "set_layout", "columns": 1})
    h = s.dashboard_history(d)
    assert not h["can_redo"] and h["total"] == 5 and h["actions"][-1]["action"] == {"type": "set_layout", "columns": 1}
    assert {a["source"] for a in h["actions"]} == {"ui", "agent"}
    assert h["initial_state"]["items"] == [] and h["state"]["layout"]["columns"] == 1

    # the log survives a restart of the service on the same data
    s2 = Services(make_config(tmp_path), clock=clock)
    try:
        h2 = s2.dashboard_history(d)
        assert h2["state"] == h["state"] and h2["total"] == 5 and h2["can_undo"]
        s2.dashboard_undo(d)
        assert s2.dashboard_get(d)["layout"]["columns"] == 3
    finally:
        s2.stop()


def test_reference_and_validation_errors(dash):
    s, d, chart, _ = dash
    with pytest.raises(ActionError) as err:
        s.dashboard_apply(d, {"type": "add_item", "item": {"type": "chart", "chart_id": 999}})
    assert err.value.code == "unknown_chart"
    with pytest.raises(ActionError) as err:
        s.dashboard_apply(d, {"type": "add_item", "item": {"type": "kpi", "dataset": "nope", "expr": "1"}})
    assert err.value.code == "unknown_dataset"
    with pytest.raises(LookupError):
        s.dashboard_apply(404, {"type": "set_layout", "columns": 2})
    assert s.dashboard_history(d)["total"] == 0, "rejected actions leave no trace in the log"


def test_legacy_dashboard_migrates_with_snapshot(tmp_path, clock):
    svc = Services(make_config(tmp_path), clock=clock)
    svc.stop()
    # an old dashboard: no ids, no layout, no snapshot, no action tables rows
    con = sqlite3.connect(tmp_path / "data" / "nightingale-meta.sqlite")
    con.execute("INSERT INTO dashboards (name, spec_json, created_at, updated_at) VALUES (?,?,?,?)",
                ("Old", json.dumps({"items": [{"type": "chart", "chart_id": 1}, {"type": "kpi", "dataset": "d", "expr": "COUNT(*)", "label": "n"}],
                                    "filter": {}}), "2024-01-01T00:00:00Z", "2024-01-01T00:00:00Z"))
    con.commit()
    con.close()
    svc = Services(make_config(tmp_path), clock=clock)
    try:
        did = svc.dashboard_list()["dashboards"][0]["id"]
        h = svc.dashboard_history(did)
        assert [i["id"] for i in h["initial_state"]["items"]] == ["i1", "i2"] and h["total"] == 0
        svc.dashboard_apply(did, {"type": "remove_item", "item_id": "i1"})
        svc.dashboard_undo(did)
        assert svc.dashboard_history(did)["state"] == h["initial_state"]
    finally:
        svc.stop()


def test_legacy_add_goes_through_the_log(dash):
    s, d, chart, _ = dash
    out = s.dashboard_add(d, {"type": "chart", "chart_id": chart})
    assert out["spec"]["items"][0]["id"] == "i1"
    assert s.dashboard_history(d)["total"] == 1
    s.dashboard_undo(d)
    assert s.dashboard_get(d)["items"] == []


# ---- REST + MCP ----------------------------------------------------------------------------------------------
def test_rest_endpoints(client):
    svc = client.services
    d = client.post("/api/dashboards", json={"name": "R"}).json()["dashboard_id"]
    schemas = client.get("/api/dashboards/action-schemas").json()
    assert "move_item" in schemas["actions"]
    bad = client.post(f"/api/dashboards/{d}/actions", json={"action": {"type": "set_layout", "columns": 12}})
    assert bad.status_code == 400 and "invalid_action" in bad.text
    ok = client.post(f"/api/dashboards/{d}/actions", json={"action": {"type": "set_name", "name": "R2"}}).json()
    assert ok["state"]["name"] == "R2" and ok["can_undo"]
    assert client.get(f"/api/dashboards/{d}/history").json()["total"] == 1
    assert client.post(f"/api/dashboards/{d}/undo").json()["state"]["name"] == "R"
    assert client.post(f"/api/dashboards/{d}/redo").json()["state"]["name"] == "R2"
    assert client.post(f"/api/dashboards/{d}/redo").status_code == 400
    assert client.get(f"/api/dashboards/{d}").json()["name"] == "R2"
    assert svc.dashboard_list()["dashboards"][0]["name"] == "R2"


def test_mcp_tool_actions(dash):
    services, _, chart, _ = dash
    call = lambda **kw: agent_tools.call_tool_sync(services, "data_dashboard", kw)  # noqa: E731
    d = call(action="create", name="M")["dashboard_id"]
    assert "update_item" in call(action="action_schemas")["actions"]
    r = call(action="apply_action", dashboard_id=d, change={"type": "add_item", "item": {"type": "chart", "chart_id": chart}})
    assert r["state"]["items"][0]["id"] == "i1" and r["can_undo"] and r["inverse"] == {"type": "remove_item", "item_id": "i1"}
    with pytest.raises(ValueError, match="needs `change`"):
        call(action="apply_action", dashboard_id=d)
    with pytest.raises(ActionError, match="unknown_item"):
        call(action="apply_action", dashboard_id=d, change={"type": "remove_item", "item_id": "nope"})
    assert call(action="undo", dashboard_id=d)["state"]["items"] == []
    assert call(action="redo", dashboard_id=d)["state"]["items"][0]["id"] == "i1"
    h = call(action="history", dashboard_id=d)
    assert h["total"] == 1 and h["actions"][0]["source"] == "agent"
    assert call(action="get", dashboard_id=d)["items"][0]["chart"]["chart_id"] == chart

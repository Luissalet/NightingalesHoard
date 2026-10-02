"""Source kind "hoard": records another family app returns from a tool, called through the hub (always faked here)."""
import asyncio
import json

import pytest

from nightingale import hoard_source
from nightingale.agent_tools import call_tool
from nightingale.workbench.engine import DataError


def entries(n, start=0):
    return [{"id": start + i, "date": f"2026-09-{(i % 28) + 1:02d}", "amount_cents": -1000 - i, "amount": f"-{10 + i / 100:.2f}",
             "counterparty": f"Shop {i % 5}", "account": {"id": 1, "name": "Main"}, "tags": ["a", "b"]} for i in range(n)]


class Hub:
    """A stand-in for family.call: records every call and answers from a table."""

    def __init__(self, answers):
        self.answers, self.calls = answers, []

    def __call__(self, app, tool, args, timeout):
        self.calls.append((app, tool, dict(args)))
        answer = self.answers[(app, tool)]
        return answer(args) if callable(answer) else answer


@pytest.fixture
def hub(monkeypatch):
    def install(answers):
        fake = Hub(answers)
        monkeypatch.setattr(hoard_source, "_hub_call", fake)
        return fake
    return install


def ok(result):
    return {"ok": True, "result": result}


# ---------- finding the records ----------
def test_extract_records_shapes():
    rows = [{"a": 1}, {"a": 2}]
    assert hoard_source.extract_records(rows) == (rows, "")
    assert hoard_source.extract_records({"count": 2, "items": rows}) == (rows, "items")
    assert hoard_source.extract_records({"meta": {"n": 1}, "shipments": rows, "other": [{"x": 1}]})[1] == "shipments"
    assert hoard_source.extract_records({"a": [{"x": 1}], "b": [{"x": 1}, {"x": 2}, {"x": 3}]})[1] == "b"  # no known key: the longest list
    assert hoard_source.extract_records({"data": {"rows": rows}})[1] == "data.rows"
    assert hoard_source.extract_records({"data": {"rows": rows}}, "data.rows") == (rows, "data.rows")
    assert hoard_source.extract_records({"v": [1, 2]}, "v")[0] == [{"value": 1}, {"value": 2}]
    text = json.dumps({"items": rows})
    assert hoard_source.extract_records(text)[0] == rows
    assert hoard_source.extract_records({"content": [{"type": "text", "text": text}]})[0] == rows


def test_extract_records_object_of_objects_gives_one_row_per_key():
    stats = {"by_carrier": {"ups": {"n": 3, "avg": 2.5}, "dhl": {"n": 1, "avg": 4.0}}, "totals": {"n": 4}}
    rows, path = hoard_source.extract_records(stats, "by_carrier")
    assert path == "by_carrier" and rows == [{"key": "ups", "n": 3, "avg": 2.5}, {"key": "dhl", "n": 1, "avg": 4.0}]
    assert hoard_source.extract_records({"apps": {"chrome": 120, "code": 80}}, "apps")[0] == [
        {"key": "chrome", "value": 120}, {"key": "code", "value": 80}]


@pytest.mark.parametrize("answer,path,message", [
    ({"ok": True, "n": 3}, None, "no list of records"),
    ({"items": []}, None, "empty list"),
    ([], None, "empty list"),
    ({"items": [{"a": 1}]}, "data.rows", "not found"),
    ({"items": [{"a": 1}]}, "items.5", "not found"),
    ({"n": 3}, "n", "not a list"),
    ("plain text", None, "no list of records"),
])
def test_extract_records_errors_say_what_to_do(answer, path, message):
    with pytest.raises(DataError, match=message):
        hoard_source.extract_records(answer, path)


# ---------- shaping ----------
def test_shape_rows_flattens_lists_epochs_and_mixed_types():
    spec = {"epoch_columns": "_ts", "derive": [{"name": "transit_days", "from": "shipped_ts", "to": "delivered_ts"}]}
    rows = hoard_source.shape_rows([
        {"id": 1, "shipped_ts": 1_790_000_000, "delivered_ts": 1_790_000_000 + 3 * 86400 + 43200, "meta": {"carrier": "ups", "deep": {"x": 1}},
         "events": [1, 2], "code": 7, "flag": True},
        {"id": 2, "shipped_ts": 1_790_000_000, "delivered_ts": None, "code": "A-1", "flag": False},
        {"id": 3, "shipped_ts": 1_790_100_000, "delivered_ts": 1_790_000_000, "code": 9}], spec)
    first, second, third = rows
    assert first["meta_carrier"] == "ups" and first["meta_deep_x"] == 1 and first["events"] == "[1, 2]"
    assert first["shipped_ts"] == "2026-09-21 14:13:20" and first["transit_days"] == 3.5
    assert second["delivered_ts"] is None and second["transit_days"] is None
    assert third["transit_days"] is None  # delivered before it shipped: no negative days
    assert [r["code"] for r in rows] == ["7", "A-1", "9"]  # a column mixing numbers and text becomes text
    assert [r["flag"] for r in rows[:2]] == [True, False]


# ---------- spec ----------
def test_resolve_spec_merges_preset_and_explicit_fields():
    spec = hoard_source.resolve_spec("ledger_transactions", None, None, None, None, {})
    assert (spec["app"], spec["tool"], spec["list_path"]) == ("ledger", "list_entries", "items")
    assert spec["paginate"] == {"size": 200, "max_rows": 5000, "limit_arg": "limit", "offset_arg": "offset"}
    custom = hoard_source.resolve_spec("ledger_transactions", None, None, {"account": "Main"}, "rows", {"max_rows": 50})
    assert custom["args"] == {"account": "Main"} and custom["list_path"] == "rows" and custom["max_rows"] == 50
    assert hoard_source.PRESETS["ledger_transactions"]["args"] == {"order": "desc"}  # the preset itself is never modified
    plain = hoard_source.resolve_spec(None, "Phileas", "shipments_list", {"filter": "all"}, None, {})
    assert plain["app"] == "phileas" and "paginate" not in plain


@pytest.mark.parametrize("call,message", [
    (dict(preset="nope"), "unknown preset"),
    (dict(app="", tool="x"), "app is required"),
    (dict(app="Bad App!", tool="x"), "app is required"),
    (dict(app="ledger", tool=""), "tool is required"),
    (dict(app="ledger", tool="../x"), "tool is required"),
    (dict(app="ledger", tool="x", args=[1]), "args must be an object"),
    (dict(app="ledger", tool="x", list_path=3), "list_path must be text"),
    (dict(app="ledger", tool="x", options={"paginate": {"size": 0}}), "paginate.size"),
    (dict(app="ledger", tool="x", options={"paginate": "yes"}), "paginate must be an object"),
    (dict(app="ledger", tool="x", options={"max_rows": "many"}), "max_rows must be a whole number"),
])
def test_resolve_spec_validation(call, message):
    args = dict(preset=None, app=None, tool=None, args=None, list_path=None, options=None)
    args.update(call)
    with pytest.raises(DataError, match=message):
        hoard_source.resolve_spec(**args)


def test_presets_listing_marks_the_unverified_one():
    presets = {p["id"]: p for p in hoard_source.list_presets("en")}
    assert set(presets) == {"ledger_transactions", "phileas_shipments", "argus_app_time"}
    assert presets["ledger_transactions"]["verified"] and presets["phileas_shipments"]["verified"]
    assert presets["argus_app_time"]["verified"] is False and presets["argus_app_time"]["tool"] == "screen_app_time"
    assert "UNVERIFIED" in presets["argus_app_time"]["description"]
    assert "SIN VERIFICAR" in hoard_source.list_presets("es")[2]["description"]
    assert set(hoard_source.list_presets()[0]["title"]) == {"es", "en"}


# ---------- fetch ----------
def test_fetch_pages_until_a_short_page(hub):
    fake = hub({("ledger", "list_entries"): lambda a: ok({"items": entries(min(200, max(0, 450 - a["offset"])), a["offset"])})})
    spec = hoard_source.resolve_spec("ledger_transactions", None, None, None, None, {})
    got = hoard_source.fetch(spec)
    assert len(got["rows"]) == 450 and got["pages"] == 3 and got["truncated"] is False and got["list_path"] == "items"
    assert [c[2]["offset"] for c in fake.calls] == [0, 200, 400] and all(c[2]["limit"] == 200 and c[2]["order"] == "desc" for c in fake.calls)


def test_fetch_stops_at_the_cap_and_says_so(hub):
    hub({("ledger", "list_entries"): lambda a: ok({"items": entries(200, a["offset"])})})
    spec = hoard_source.resolve_spec("ledger_transactions", None, None, None, None, {"paginate": {"size": 200, "max_rows": 500}})
    got = hoard_source.fetch(spec)
    assert len(got["rows"]) == 500 and got["truncated"] is True and got["pages"] == 3


@pytest.mark.parametrize("reply,message", [
    ({"ok": False, "error": "hub not reachable at http://127.0.0.1:8810"}, "did not answer: hub not reachable"),
    ({"ok": False, "error": "app 'ledger' is not running"}, "not running"),
    (ok({"ok": False, "error": "Unknown account"}), "Unknown account"),
    (None, "did not answer"),
])
def test_fetch_reports_hub_and_tool_errors(hub, reply, message):
    hub({("ledger", "list_entries"): reply})
    with pytest.raises(DataError, match=message):
        hoard_source.fetch(hoard_source.resolve_spec(None, "ledger", "list_entries", None, None, {}))


# ---------- through the services ----------
def test_ingest_ledger_preset_builds_a_dataset_and_logs_it(services, hub):
    hub({("ledger", "list_entries"): lambda a: ok({"total": 250, "items": entries(min(200, max(0, 250 - a["offset"])), a["offset"])})})
    out = services.ingest_hoard(preset="ledger_transactions", source="agent")
    assert out["name"] == "ledger_transactions" and out["row_count"] == 250 and out["from"] == "hoard://ledger/list_entries"
    assert out["pages"] == 2 and out["truncated"] is False and out["unverified"] is False
    columns = {c["name"] for c in out["columns"]}
    assert {"id", "date", "amount_cents", "counterparty", "account_name", "tags"} <= columns
    assert services.query("SELECT COUNT(DISTINCT counterparty) AS n FROM ledger_transactions")["rows"][0]["n"] == 5
    source = [dict(s) for s in services.meta.list_sources() if s["name"] == "ledger_transactions"][0]
    assert source["kind"] == "hoard" and source["path"] == "hoard://ledger/list_entries"
    assert json.loads(source["options_json"])["list_path"] == "items"
    log = services.meta.search_log(source="agent")
    assert any("ingested 'ledger_transactions'" in (r["output_summary"] or "") for r in log)


def test_rerun_adds_a_new_version_and_undo_goes_back(services, hub):
    rows = {"n": 5}
    hub({("ledger", "list_entries"): lambda a: ok({"items": entries(rows["n"])})})
    first = services.ingest_hoard(app="ledger", tool="list_entries", name="mine")
    assert (first["row_count"], first["current_version"], first["version_count"]) == (5, 0, 1)
    rows["n"] = 8
    second = services.ingest_hoard(app="ledger", tool="list_entries", name="mine")
    assert (second["row_count"], second["current_version"], second["version_count"], second["new_version"]) == (8, 1, 2, 1)
    versions = services.meta.list_versions(second["id"])
    assert [v["op"] for v in versions] == ["ingest", "ingest"] and versions[1]["parent_version"] == 0
    assert services.query("SELECT COUNT(*) AS n FROM mine")["rows"][0]["n"] == 8
    back = services.undo("mine")
    assert back["row_count"] == 5 and services.query("SELECT COUNT(*) AS n FROM mine")["rows"][0]["n"] == 5


def test_name_taken_by_another_source_is_refused(services, hub, tmp_path):
    csv = tmp_path / "x.csv"
    csv.write_text("a\n1\n")
    services.ingest_file(str(csv), "taken")
    hub({("ledger", "list_entries"): ok({"items": entries(3)})})
    with pytest.raises(DataError, match="already exists"):
        services.ingest_hoard(app="ledger", tool="list_entries", name="taken")
    assert services.list_datasets()["datasets"][0]["row_count"] == 1  # untouched
    # another tool under the same name is refused too: a re-run only extends the same app+tool
    services.ingest_hoard(app="ledger", tool="list_entries", name="mine")
    hub({("ledger", "search_entries"): ok({"items": entries(3)})})
    with pytest.raises(DataError, match="already exists"):
        services.ingest_hoard(app="ledger", tool="search_entries", name="mine")


def test_phileas_preset_gets_timestamps_and_transit_days(services, hub):
    shipments = [{"id": f"s{i}", "carrier": "ups", "merchant": "Shop", "shipped_ts": 1_790_000_000, "delivered_ts": 1_790_000_000 + (i + 1) * 86400,
                  "status": "delivered"} for i in range(4)]
    fake = hub({("phileas", "shipments_list"): ok({"shipments": shipments, "count": 4})})
    out = services.ingest_hoard(preset="phileas_shipments")
    assert out["row_count"] == 4 and out["name"] == "phileas_shipments"
    types = {c["name"]: c["type"].upper() for c in out["columns"]}
    assert "TIMESTAMP" in types["shipped_ts"] and "DOUBLE" in types["transit_days"]
    assert services.query("SELECT MAX(transit_days) AS m FROM phileas_shipments")["rows"][0]["m"] == 4.0
    assert fake.calls == [("phileas", "shipments_list", {"filter": "delivered", "limit": 500})]


def test_argus_preset_is_flagged_unverified(services, hub):
    hub({("argus", "screen_app_time"): ok({"apps": [{"app": "code", "seconds": 100}, {"app": "browser", "seconds": 50}]})})
    out = services.ingest_hoard(preset="argus_app_time")
    assert out["unverified"] is True and out["row_count"] == 2 and out["name"] == "argus_app_time"


def test_refresh_rebuilds_from_the_app_and_replays_the_recipe(services, hub):
    data = {"n": 6}
    hub({("ledger", "list_entries"): lambda a: ok({"items": entries(data["n"])})})
    services.ingest_hoard(app="ledger", tool="list_entries", name="mine")
    services.transform_apply("mine", "filter", {"expr": "id >= 2"})
    assert services.query("SELECT COUNT(*) AS n FROM mine")["rows"][0]["n"] == 4
    data["n"] = 10
    refreshed = services.refresh("mine")
    assert refreshed["steps_replayed"] == 1 and refreshed["row_count"] == 8
    assert services.query("SELECT COUNT(*) AS n FROM mine")["rows"][0]["n"] == 8


def test_failed_calls_leave_nothing_behind(services, hub):
    hub({("ledger", "list_entries"): {"ok": False, "error": "hub not reachable at http://127.0.0.1:8810"}})
    with pytest.raises(DataError, match="hub not reachable"):
        services.ingest_hoard(app="ledger", tool="list_entries", name="ghost")
    assert services.list_datasets()["datasets"] == [] and list(services.meta.list_sources()) == []
    hub({("ledger", "list_entries"): ok({"items": []})})
    with pytest.raises(DataError, match="empty list"):
        services.ingest_hoard(app="ledger", tool="list_entries", name="ghost")
    assert services.list_datasets()["datasets"] == []


def test_agent_tool_and_rest_endpoints(client, hub):
    fake = hub({("ledger", "list_entries"): ok({"items": entries(4)})})

    async def run():
        return await call_tool(client.services, "data_ingest", {"kind": "hoard", "preset": "ledger_transactions", "name": "via_tool"})

    out = asyncio.run(run())
    assert out["row_count"] == 4 and out["name"] == "via_tool"
    presets = client.get("/api/sources/hoard-presets?lang=es").json()["presets"]
    assert [p["id"] for p in presets] == ["ledger_transactions", "phileas_shipments", "argus_app_time"] and presets[0]["title"] == "Movimientos de Ledger"
    r = client.post("/api/sources/ingest", json={"kind": "hoard", "app": "ledger", "tool": "list_entries", "args": {"text": "Shop"}, "name": "via_rest"})
    assert r.status_code == 200 and r.json()["row_count"] == 4
    assert fake.calls[-1][2] == {"text": "Shop"}
    bad = client.post("/api/sources/ingest", json={"kind": "hoard", "app": "ledger"})
    assert bad.status_code == 400 and "tool is required" in bad.json()["error"]
    unknown = client.post("/api/sources/ingest", json={"kind": "hoard", "preset": "nope"})
    assert unknown.status_code == 400

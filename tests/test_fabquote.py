"""Fabrication quotes by scenario: hand-computed figures, units, blocking, coarse estimates, diffs, actuals, Vulcan."""
import json
import threading
from http.server import BaseHTTPRequestHandler, HTTPServer

import pytest

from nightingale import fabquote as fq
from nightingale.agent_tools import call_tool_sync
from nightingale.texts import use_lang

FDM_FULL = {"volume_cm3": 100.0, "surface_cm2": 150.0, "part_height_mm": 50.0}


def mk(services, name="q", process="fdm", **kw):
    return fq.create_quote(services, name, process, **kw)


def item(view, iid):
    r = view["result"]
    return next(x for x in r["items"] + r["derived"] if x["id"] == iid)


# ---------------------------------------------------------------- expressions and units
def test_formula_units_and_conversions():
    env = {"a": fq._q(2, "m"), "b": fq._q(300, "mm"), "r": fq._q(10, "EUR/m")}
    q = fq.evaluate_formula("(a + b) * r", env)
    assert q.to("EUR").magnitude == pytest.approx(23.0)
    assert fq.evaluate_formula("ceil(a / b)", env).magnitude == pytest.approx(7)  # 2 m / 0.3 m = 6.67 -> 7
    with pytest.raises(fq.FabQuoteError):
        fq.evaluate_formula("a + r", env)  # m + EUR/m
    for bad in ("__import__('os')", "a.real", "open('x')", "[1]", "a if a else b"):
        with pytest.raises(fq.FabQuoteError):
            fq.evaluate_formula(bad, env)


def test_percent_parameter_and_unit_conversion_of_overrides(services):
    v = mk(services, measurements=FDM_FULL)
    # support_pct is 10 %: support = 10 % of the geometric material
    assert item(v, "mat_supports")["value"] == pytest.approx(item(v, "mat_geo")["value"] * 0.10)
    out = fq.set_parameters(services, "q", {"setup_time_h": {"value": 30, "unit": "min"}})
    stored = {p["name"]: p for p in fq.get_quote(services, "q")["result"]["parameters"]}["setup_time_h"]
    assert stored["value"] == pytest.approx(0.5) and stored["unit"] == "h"      # converted to the parameter's own unit
    assert out["diff"]["changes"] == []  # 30 min is the same 0.5 h


# ---------------------------------------------------------------- hand computation
def hand_fdm(volume=100.0, surface=150.0, price=22.0, labour=28.0):
    shell = min(volume, surface * 2 * 0.45 / 10)             # cm2 * perimeters * mm -> cm3
    extruded = shell + 0.20 * (volume - shell)
    mat = extruded * 1.24 * price / 1000
    return {"extruded": extruded, "mat_geo": mat, "mat_supports": mat * 0.10, "mat_purge": 5 * price / 1000,
            "setup": 0.5 * labour, "post": 0.25 * labour}


def test_fdm_with_explicit_slicer_time_matches_hand_computation(services):
    v = mk(services, measurements=FDM_FULL, parameters={"slicer_time_h": 3.0})
    h = hand_fdm()
    assert item(v, "extruded_cm3")["value"] == pytest.approx(h["extruded"])
    assert item(v, "mat_geo")["value"] == pytest.approx(h["mat_geo"])
    assert item(v, "mat_supports")["value"] == pytest.approx(h["mat_supports"])
    assert item(v, "mat_purge")["value"] == pytest.approx(h["mat_purge"])
    assert item(v, "setup_prep")["value"] == pytest.approx(h["setup"])
    assert item(v, "labour_post")["value"] == pytest.approx(h["post"])
    machine = 3.0 * 1.2
    assert item(v, "machine_time")["value"] == pytest.approx(machine)
    waste = 0.05 * (h["mat_geo"] + h["mat_supports"] + h["mat_purge"] + machine)
    assert item(v, "waste_scrap")["value"] == pytest.approx(waste)
    cost = h["mat_geo"] + h["mat_supports"] + h["mat_purge"] + h["setup"] + h["post"] + machine + waste
    assert item(v, "margin")["value"] == pytest.approx(0.20 * cost)
    assert v["result"]["total"]["value"] == pytest.approx(cost * 1.2)
    assert v["result"]["complete"] and v["result"]["quality"] == "exact"
    assert set(v["result"]["categories"]) == set(fq.CATEGORIES)


def test_fdm_model_time_uses_layers_perimeters_infill_and_speed(services):
    v = mk(services, measurements=FDM_FULL)  # no slicer time: model alternative (not coarse)
    h = hand_fdm()
    flow = 0.45 * 0.2 * 60                                    # mm3/s
    t_h = (h["extruded"] * 1000 / flow * 1.15 + 250 * 2) / 3600   # 50 mm / 0.2 mm = 250 layers
    assert item(v, "print_time_h")["value"] == pytest.approx(t_h, rel=1e-6)
    assert item(v, "print_time_h")["quality"] == "exact"


# ---------------------------------------------------------------- missing measurements block
def test_missing_measurement_blocks_only_dependent_items(services):
    v = mk(services, measurements={"surface_cm2": 150.0})
    r = v["result"]
    assert not r["complete"] and r["total"]["partial"]
    for iid in ("mat_geo", "mat_supports", "machine_time", "waste_scrap", "margin"):
        it = item(v, iid)
        assert it["status"] == "blocked" and "volume_cm3" in it["missing"], iid
    # independent items are still computed
    assert item(v, "setup_prep")["status"] == "ok" and item(v, "mat_purge")["status"] == "ok"
    assert r["total"]["value"] == pytest.approx(hand_fdm()["setup"] + hand_fdm()["post"] + hand_fdm()["mat_purge"])
    assert any("parcial" in w or "partial" in w for w in r["warnings"])


# ---------------------------------------------------------------- coarse FDM and concrete refusal
def test_fdm_volume_only_is_a_labelled_coarse_estimate(services):
    v = mk(services, measurements={"volume_cm3": 120.0})
    pt = item(v, "print_time_h")
    assert pt["value"] == pytest.approx(120 / 12) and pt["quality"] == "coarse"
    assert any("coarse estimate" in w for w in pt["warnings"])
    assert v["result"]["quality"] == "coarse"
    assert item(v, "machine_time")["quality"] == "coarse"
    with use_lang("es"):
        es = fq.get_quote(services, "q")
    assert any("estimación gruesa" in w for w in es["result"]["warnings"])


def test_fdm_volume_only_can_be_blocked_by_option(services):
    v = mk(services, measurements={"volume_cm3": 120.0}, volume_only="block")
    assert item(v, "print_time_h")["status"] == "blocked"
    assert "coarse" in item(v, "print_time_h")["reason"]
    assert item(v, "machine_time")["status"] == "blocked"
    # a slicer time unblocks it
    after = fq.set_parameters(services, "q", {"slicer_time_h": 2.0})
    assert "machine_time" not in after["result"]["blocked"]
    assert "mat_geo" in after["result"]["blocked"]          # its fill fraction is still a coarse estimate
    v2 = fq.get_quote(services, "q")
    assert item(v2, "print_time_h")["status"] == "ok" and item(v2, "print_time_h")["quality"] == "exact"


def concrete(services, **extra):
    meas = {"volume_cm3": 250000.0, "part_height_mm": 1200.0}
    meas.update(extra.pop("meas", {}))
    return fq.create_quote(services, "c", "concrete", measurements=meas, **extra)


def test_concrete_refuses_volume_only_time(services):
    v = concrete(services)
    pt = item(v, "print_time_h")
    assert pt["status"] == "blocked" and "toolpath_length_m" in pt["missing"] and "print_speed_mm_s" in pt["missing"]
    assert "solid volume" in pt["reason"]
    assert item(v, "machine_time")["status"] == "blocked" and item(v, "labour_print")["status"] == "blocked"
    # the material (geometric) line is fine: it only needs the volume
    assert item(v, "mat_geo")["value"] == pytest.approx(0.25 * 180)
    with use_lang("es"):
        es = fq.get_quote(services, "c")
    assert "no se deduce del volumen sólido" in item(es, "print_time_h")["reason"]


def test_concrete_time_from_toolpath_layers_and_speed(services):
    v = concrete(services, parameters={"toolpath_length_m": 3000.0, "print_speed_mm_s": 100.0, "layer_height_mm": 10.0,
                                       "layer_wait_s": 30.0})
    # 3000 m at 100 mm/s = 30000 s; 1200 / 10 = 120 layers * 30 s = 3600 s
    assert item(v, "layer_count_used")["value"] == pytest.approx(120)
    assert item(v, "print_time_h")["value"] == pytest.approx((30000 + 3600) / 3600)
    out = fq.set_parameters(services, "c", {"layer_count": 100})
    assert item({"result": out["result"]}, "print_time_h")["value"] == pytest.approx((30000 + 3000) / 3600)


def test_concrete_custom_spec_cannot_derive_time_from_volume(services):
    spec = fq.build_template("concrete")
    for d in spec["derived"]:
        if d["id"] == "print_time_h":
            d["alternatives"] = [{"formula": "volume_cm3 / coarse_rate", "quality": "exact"}]
    spec["parameters"]["coarse_rate"] = {"value": 5, "unit": "cm**3/h", "source": "assumed"}
    with pytest.raises(fq.FabQuoteError, match="never derived from the volume"):
        fq.create_quote(services, "bad", spec=spec)


# ---------------------------------------------------------------- scenarios: only dependents change
def test_changing_one_assumption_updates_only_dependent_items(services):
    mk(services, measurements=FDM_FULL, parameters={"slicer_time_h": 3.0})
    base = fq.evaluate(fq.get_quote.__globals__["_load"](services, "q")["spec"])
    out = fq.set_parameters(services, "q", {"machine_rate_eur_h": {"value": 2.0, "source": "rate card"}})
    d = out["diff"]
    changed = {c["id"] for c in d["changes"]}
    assert changed == {"machine_time", "waste_scrap", "margin"}               # exactly the dependents
    assert set(out["result"]["recomputed"]) == {"machine_time", "waste_scrap", "margin"}
    ch = {c["id"]: c for c in d["changes"]}
    assert ch["machine_time"]["before"] == pytest.approx(3.6) and ch["machine_time"]["after"] == pytest.approx(6.0)
    assert ch["machine_time"]["formula"] == "print_time_h * machine_rate_eur_h"
    assert ch["machine_time"]["because"][0]["parameter"] == "machine_rate_eur_h"
    assert ch["machine_time"]["because"][0]["source_after"] == "rate card"
    assert "machine_rate_eur_h" in ch["machine_time"]["sources"]
    h = hand_fdm()
    mat = h["mat_geo"] + h["mat_supports"] + h["mat_purge"]
    waste = 0.05 * (mat + 6.0)
    assert ch["waste_scrap"]["after"] == pytest.approx(waste)
    assert ch["margin"]["after"] == pytest.approx(0.2 * (mat + h["setup"] + h["post"] + 6.0 + waste))
    assert "mat_geo" in d["unchanged"] and "setup_prep" in d["unchanged"]
    # untouched values are exactly the ones from before
    after = {i["id"]: i["value"] for i in out["result"]["items"]}
    assert after["mat_geo"] == next(i["value"] for i in base["items"] if i["id"] == "mat_geo")


def test_scenario_compare_reports_diff_and_keeps_formulas_and_sources(services):
    mk(services, measurements=FDM_FULL, parameters={"slicer_time_h": 3.0})
    fq.save_scenario(services, "q", "pricey-material", {"material_price_eur_kg": 40})
    fq.save_scenario(services, "q", "fast", {"slicer_time_h": {"value": 90, "unit": "min", "source": "user"}})
    cmp = fq.compare_scenarios(services, "q")
    by = {s["scenario"]: s for s in cmp["scenarios"]}
    pm = by["pricey-material"]["diff"]
    assert {c["id"] for c in pm["changes"]} == {"mat_geo", "mat_supports", "mat_purge", "waste_scrap", "margin"}
    mg = next(c for c in pm["changes"] if c["id"] == "mat_geo")
    assert mg["after"] == pytest.approx(mg["before"] * 40 / 22)
    assert mg["formula"] == "extruded_cm3 * density_g_cm3 * material_price_eur_kg"
    assert mg["sources"]["material_price_eur_kg"] == "assumed"          # scenario override defaults to assumed
    assert mg["because"] == [{"parameter": "material_price_eur_kg", "before": 22, "after": 40, "unit": "EUR/kg",
                              "source_before": "rate card", "source_after": "assumed"}]
    fast = by["fast"]["diff"]
    assert {c["id"] for c in fast["changes"]} == {"print_time_h", "machine_time", "waste_scrap", "margin"}
    assert next(c for c in fast["changes"] if c["id"] == "print_time_h")["after"] == pytest.approx(1.5)
    assert fast["total"]["delta"] < 0 and cmp["base"]["total"]["value"] > by["fast"]["total"]["value"]


def test_sensitivity_one_at_a_time(services):
    mk(services, measurements=FDM_FULL, parameters={"slicer_time_h": 3.0})
    s = fq.run_sensitivity(services, "q", ["machine_rate_eur_h", "labour_rate_eur_h"], 10)
    ranking = {r["parameter"]: r for r in s["ranking"]}
    base = s["base_total"]["value"]
    m = ranking["machine_rate_eur_h"]
    # machine rate +-10 %: machine_time moves 0.36, scrap 5 % of that, margin 20 % of both
    d = 0.36 * (1 + 0.05) * 1.2
    assert m["high"]["total"] == pytest.approx(base + d) and m["low"]["total"] == pytest.approx(base - d)
    assert set(m["recomputed"]) == {"machine_time", "waste_scrap", "margin"}
    assert s["ranking"][0]["swing"] >= s["ranking"][1]["swing"]
    with pytest.raises(fq.FabQuoteError):
        fq.run_sensitivity(services, "q", ["nope"], 10)


# ---------------------------------------------------------------- actuals
def test_actuals_comparison_and_ledger_reference(services):
    mk(services, measurements=FDM_FULL, parameters={"slicer_time_h": 3.0})
    fq.record_actual(services, "q", "print_time_h", 200, "min", note="measured on the machine")
    fq.record_actual(services, "q", "mat_geo", 0.55, ledger_ref="mov-123")
    fq.record_actual(services, "q", "mat_geo", 0.10)
    with pytest.raises(fq.FabQuoteError):
        fq.record_actual(services, "q", "machine_time", 3, "kg")
    with pytest.raises(LookupError):
        fq.record_actual(services, "q", "nope", 3)
    cmp = fq.compare_actuals(services, "q")
    rows = {r["item"]: r for r in cmp["rows"]}
    assert rows["print_time_h"]["actual"] == pytest.approx(200 / 60)
    assert rows["print_time_h"]["delta"] == pytest.approx(200 / 60 - 3.0)
    quoted = hand_fdm()["mat_geo"]
    assert rows["mat_geo"]["actual"] == pytest.approx(0.65) and rows["mat_geo"]["n_records"] == 2
    assert rows["mat_geo"]["delta_pct"] == pytest.approx((0.65 - quoted) / quoted * 100)
    assert rows["mat_geo"]["ledger_refs"] == ["mov-123"]
    assert rows["margin"]["status"] == "no_actual"
    assert cmp["totals"]["items_compared"] == 1 and cmp["totals"]["actual"] == pytest.approx(0.65)
    assert "nothing is written to the books" in cmp["note"]


# ---------------------------------------------------------------- export
def test_export_json_csv_markdown_with_formulas_and_sources(services):
    mk(services, measurements=FDM_FULL, parameters={"slicer_time_h": 3.0})
    fq.save_scenario(services, "q", "s1", {"margin_pct": 30})
    j = json.loads(fq.export_quote(services, "q", "json")["content"])
    assert j["result"]["total"]["value"] > 0 and j["spec"]["parameters"]["volume_cm3"]["source"] == "measured"
    csv_text = fq.export_quote(services, "q", "csv")["content"]
    header, *rows = csv_text.splitlines()
    assert header.split(",")[:4] == ["kind", "category", "id", "label"]
    line = next(r for r in rows if ",machine_time," in r)
    assert "print_time_h * machine_rate_eur_h" in line and "machine_rate_eur_h (rate card)" in line
    md = fq.export_quote(services, "q", "markdown", "s1")["content"]
    assert "| `mat_geo` |" in md and "extruded_cm3 * density_g_cm3 * material_price_eur_kg" in md and "(measured)" in md
    with pytest.raises(fq.FabQuoteError):
        fq.export_quote(services, "q", "xml")


# ---------------------------------------------------------------- CNC / laser
def test_cnc_laser_hand_computation(services):
    v = mk(services, "l", "cnc_laser", measurements={"cut_length_mm": 6000.0, "blank_area_cm2": 400.0})
    # feed is mm/min: 6000 / 1500 = 4 min
    assert item(v, "cut_time_h")["value"] == pytest.approx(4 / 60 + 0.5 / 3600)
    mat = 400 * 18 / 10000
    assert item(v, "mat_geo")["value"] == pytest.approx(mat)
    assert item(v, "waste_nesting")["value"] == pytest.approx(mat * (1 / 0.7 - 1))
    assert item(v, "machine_time")["value"] == pytest.approx(item(v, "cut_time_h")["value"] * 45)
    assert v["result"]["complete"]
    miss = fq.create_quote(services, "l2", "cnc_laser", measurements={"blank_area_cm2": 400.0})
    assert item(miss, "machine_time")["status"] == "blocked" and "cut_length_mm" in item(miss, "machine_time")["missing"]


# ---------------------------------------------------------------- validation
def test_spec_validation_errors(services):
    with pytest.raises(fq.FabQuoteError):
        fq.create_quote(services, "x", "knitting")
    with pytest.raises(fq.FabQuoteError):
        fq.create_quote(services, "x", "fdm", measurements={"nope": 1})
    with pytest.raises(fq.FabQuoteError):
        fq.create_quote(services, "../x", "fdm")
    spec = fq.build_template("fdm")
    spec["line_items"][0]["alternatives"] = [{"formula": "mat_supports + 1"}]       # mat_geo <-> mat_supports loop
    with pytest.raises(fq.FabQuoteError, match="loop"):
        fq.create_quote(services, "x", spec=spec)
    spec = fq.build_template("fdm")
    spec["line_items"][0]["alternatives"] = [{"formula": "ghost * 2"}]
    with pytest.raises(fq.FabQuoteError, match="ghost"):
        fq.create_quote(services, "x", spec=spec)
    mk(services)
    with pytest.raises(fq.FabQuoteError, match="already exists"):
        mk(services)
    mk(services, replace=True)


# ---------------------------------------------------------------- Vulcan (fake local server)
class _Vulcan(BaseHTTPRequestHandler):
    def log_message(self, *a):
        pass

    def do_GET(self):
        if self.path == "/library/api/models/7":
            body = {"id": 7, "triangles": 1200, "bbox_x": 40.0, "bbox_y": 30.0, "bbox_z": 20.0, "volume_cm3": 14.5,
                    "surface_cm2": 36.0, "watertight": False, "bodies": 1, "units_guess": "mm", "sha256": "ab" * 32}
        elif self.path == "/library/api/models/8":
            body = {"id": 8, "bbox_x": 1.0, "bbox_y": 1.0, "bbox_z": 2.0, "volume_cm3": 3.0, "watertight": True,
                    "units_guess": "m"}
        elif self.path == "/library/api/models/9":
            body = {"id": 9, "volume_cm3": 3.0, "bbox_x": 1.0}
        elif self.path == "/library/api/models/7/inspection?target=fdm-print":
            body = {"target": "fdm-print", "verdict": "warnings", "issues": ["thin walls"]}
        else:
            self.send_response(404)
            self.end_headers()
            return
        raw = json.dumps(body).encode()
        self.send_response(200)
        self.send_header("Content-Type", "application/json")
        self.end_headers()
        self.wfile.write(raw)


@pytest.fixture
def vulcan_url():
    srv = HTTPServer(("127.0.0.1", 0), _Vulcan)
    th = threading.Thread(target=srv.serve_forever, daemon=True)
    th.start()
    yield f"http://127.0.0.1:{srv.server_port}"
    srv.shutdown()
    srv.server_close()


def test_vulcan_fetch_records_provenance_and_feeds_the_quote(services, vulcan_url):
    v = mk(services, vulcan={"model_id": 7, "base_url": vulcan_url}, parameters={"slicer_time_h": 2.0})
    p = {x["name"]: x for x in v["result"]["parameters"]}
    assert p["volume_cm3"]["value"] == 14.5 and p["volume_cm3"]["source"] == "measured"
    assert p["part_height_mm"]["value"] == 20.0 and p["surface_cm2"]["value"] == 36.0
    prov = v["provenance"]["vulcan"]
    assert prov["source"] == "vulcan" and prov["model_id"] == "7" and len(prov["response_sha256"]) == 64
    assert prov["model_sha256"] == "ab" * 32 and prov["fetched_at"].endswith("Z")
    assert prov["inspection"]["report"]["verdict"] == "warnings"
    assert any("watertight" in w for w in v["warnings"])
    assert v["result"]["complete"]


def test_vulcan_units_missing_fields_and_errors(services, vulcan_url):
    got = fq.fetch_vulcan(8, vulcan_url)
    assert got["measurements"]["part_height_mm"]["value"] == pytest.approx(2000.0)    # 2 m
    assert any("destination report" in w for w in got["warnings"])                           # no report for model 8
    odd = fq.fetch_vulcan(9, vulcan_url)
    assert odd["measurements"]["surface_cm2"]["value"] is None                       # never invented
    assert odd["measurements"]["part_height_mm"]["value"] is None
    assert any("units guess" in w for w in odd["warnings"])
    v = mk(services, vulcan={"model_id": 9, "base_url": vulcan_url})
    assert item(v, "mat_geo")["quality"] == "coarse"         # surface unknown: assumed fill fraction, labelled
    assert {p["name"]: p for p in v["result"]["parameters"]}["surface_cm2"]["missing"]
    with pytest.raises(fq.FabQuoteError, match="404"):
        fq.fetch_vulcan(99, vulcan_url)
    with pytest.raises(fq.FabQuoteError, match="not reachable"):
        fq.fetch_vulcan(7, "http://127.0.0.1:1")
    with use_lang("es"):
        with pytest.raises(fq.FabQuoteError, match="no responde"):
            fq.fetch_vulcan(7, "http://127.0.0.1:1")


# ---------------------------------------------------------------- REST, agent tool, Spanish
def test_rest_flow_and_spanish_messages(client):
    r = client.post("/api/fabquote/quotes", json={"name": "api", "process": "fdm", "measurements": {"volume_cm3": 80}})
    assert r.status_code == 200 and r.json()["result"]["quality"] == "coarse"
    es = client.get("/api/fabquote/quotes/api?lang=es").json()
    assert any("estimación gruesa" in w for w in es["result"]["warnings"])
    r = client.put("/api/fabquote/quotes/api/parameters", json={"parameters": {"slicer_time_h": 2}})
    assert r.status_code == 200 and "print_time_h" in {c["id"] for c in r.json()["diff"]["changes"]}
    assert client.put("/api/fabquote/quotes/api/scenarios/s1", json={"overrides": {"margin_pct": 35}}).status_code == 200
    c = client.get("/api/fabquote/quotes/api/compare").json()
    assert c["scenarios"][0]["diff"]["changes"][0]["id"] == "margin"
    assert client.post("/api/fabquote/quotes/api/sensitivity", json={"pct": 20, "params": ["margin_pct"]}).status_code == 200
    assert client.post("/api/fabquote/quotes/api/actuals", json={"item": "machine_time", "value": 3.1, "ledger_ref": "L1"}).status_code == 200
    a = client.get("/api/fabquote/quotes/api/actuals").json()
    assert a["rows"][0]["item"] == "machine_time" or any(x["item"] == "machine_time" and x["actual"] == 3.1 for x in a["rows"])
    dl = client.get("/api/fabquote/quotes/api/export?format=csv&download=true")
    assert dl.status_code == 200 and dl.headers["content-type"].startswith("text/csv") and "attachment" in dl.headers["content-disposition"]
    bad = client.post("/api/fabquote/quotes", json={"name": "z", "process": "knitting"})
    assert bad.status_code == 400 and "proceso desconocido" in client.post("/api/fabquote/quotes?lang=es", json={"name": "z", "process": "knitting"}).json()["error"]
    missing = client.get("/api/fabquote/quotes/none?lang=es")
    assert missing.status_code == 404 and "presupuesto desconocido" in missing.json()["error"]
    assert client.get("/api/fabquote/templates").json()["templates"][0]["process"] == "fdm"
    assert client.delete("/api/fabquote/quotes/api").json()["deleted"] is True


def test_agent_tool_actions_and_spanish(services):
    call = lambda args: call_tool_sync(services, "data_fabquote", args)  # noqa: E731
    assert [t["process"] for t in call({"action": "templates"})["templates"]] == ["fdm", "cnc_laser", "concrete"]
    v = call({"action": "create", "name": "t", "process": "concrete", "measurements": {"volume_cm3": 1000}})
    assert not v["result"]["complete"]
    es = call({"action": "get", "name": "t", "lang": "es"})
    assert "no se deduce del volumen sólido" in item(es, "print_time_h")["reason"]
    s = call({"action": "set", "name": "t", "parameters": {"toolpath_length_m": 100, "print_speed_mm_s": 50, "layer_height_mm": 10,
                                                           "layer_wait_s": 20, "part_height_mm": 100}})
    assert item({"result": s["result"]}, "print_time_h")["value"] == pytest.approx((100 / 0.05 + 10 * 20) / 3600)
    assert call({"action": "scenario_save", "name": "t", "scenario": "slow", "overrides": {"print_speed_mm_s": 25}})["saved"]
    cmp = call({"action": "compare", "name": "t"})
    assert cmp["scenarios"][0]["diff"]["total"]["delta"] > 0
    assert call({"action": "sensitivity", "name": "t", "params": ["print_speed_mm_s"]})["ranking"][0]["parameter"] == "print_speed_mm_s"
    assert call({"action": "actual", "name": "t", "item": "print_time_h", "value": 2, "unit": "h"})["actual"]["value"] == 2
    assert call({"action": "export", "name": "t", "format": "markdown"})["format"] == "markdown"
    assert call({"action": "list"})["count"] == 1
    with pytest.raises(ValueError, match="name is required"):
        call({"action": "get"})
    with pytest.raises(LookupError):
        call({"action": "get", "name": "ghost"})
    assert call({"action": "delete", "name": "t"})["deleted"]


def test_vulcan_and_templates_routes_take_the_request_language(tmp_path):
    """The notes on fetched measurements follow ?lang= like every other route."""
    import inspect
    from nightingale.api import fabquote as routes
    for fn in (routes.templates, routes.vulcan):
        assert "request" in inspect.signature(inspect.unwrap(fn)).parameters

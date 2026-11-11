"""Fabrication quotes: top/bottom skin, calibration from real jobs, slicer G-code and trajectory files, Vulcan inspection."""
import math
import random

import pytest

from nightingale import fabquote as fq
from nightingale import fabquote_calib as fqc
from nightingale import fabquote_import as fqi
from nightingale.agent_tools import call_tool_sync
from nightingale.texts import use_lang

FDM_FULL = {"volume_cm3": 100.0, "surface_cm2": 150.0, "part_height_mm": 50.0}


def item(view, iid):
    r = view["result"]
    return next(x for x in r["items"] + r["derived"] if x["id"] == iid)


# ------------------------------------------------------------------------------- top and bottom surfaces
def test_skin_is_a_line_item_when_the_area_is_known(services):
    v = fq.create_quote(services, "q", "fdm", measurements={**FDM_FULL, "top_bottom_area_cm2": 40.0})
    # shell = 150 cm2 * 2 * 0.045 cm = 13.5; skin = 40 cm2 * (4 + 4) * 0.02 cm = 6.4; infill 20 % of the rest
    assert item(v, "skin_cm3")["value"] == pytest.approx(6.4)
    assert item(v, "extruded_cm3")["value"] == pytest.approx(13.5 + 6.4 + 0.2 * (100 - 13.5 - 6.4))
    grams_price = 1.24 * 0.022  # g/cm3 * EUR/g
    assert item(v, "mat_skin")["value"] == pytest.approx(6.4 * grams_price)
    assert item(v, "mat_geo")["value"] == pytest.approx((13.5 + 0.2 * (100 - 13.5 - 6.4)) * grams_price)
    assert item(v, "mat_supports")["value"] == pytest.approx((item(v, "mat_geo")["value"] + item(v, "mat_skin")["value"]) * 0.10)
    assert not any("top_bottom_area_cm2" in w for w in v["result"]["warnings"])


def test_without_the_area_the_skin_is_zero_and_says_so(services):
    v = fq.create_quote(services, "q", "fdm", measurements=FDM_FULL)
    assert item(v, "skin_cm3")["value"] == 0 and item(v, "mat_skin")["value"] == 0
    assert any("top_bottom_area_cm2" in w for w in v["result"]["warnings"])
    with use_lang("es"):
        es = fq.get_quote(services, "q")
    assert any("capas sólidas" in w for w in es["result"]["warnings"])
    # the coarse volume-only path still works and does not need the surface
    c = fq.create_quote(services, "c", "fdm", measurements={"volume_cm3": 100.0})
    assert item(c, "mat_geo")["status"] == "ok" and item(c, "extruded_cm3")["quality"] == "coarse"


# ------------------------------------------------------------------------------- calibration
TRUE = {"print_speed_mm_s": 45.0, "machine_rate_eur_h": 2.5, "material_price_eur_kg": 31.0, "labour_rate_eur_h": 36.0}
TARGETS = ["print_time_h", "machine_time", "mat_geo", "setup_prep"]


def synthetic_jobs(n=6, noise=0.02, seed=5, targets=TARGETS):
    rng = random.Random(seed)
    base = fq.build_template("fdm")
    jobs = []
    for i in range(n):
        meas = {"volume_cm3": rng.uniform(20, 300), "surface_cm2": rng.uniform(60, 400), "part_height_mm": rng.uniform(15, 120)}
        truth = fqc.simulate_job(base, TRUE, meas, targets)
        jobs.append({"label": f"job {i + 1}", "parameters": meas,
                     "actuals": {k: v * (1 + rng.gauss(0, noise)) for k, v in truth.items()}})
    return jobs


def test_the_fit_recovers_known_rates_with_uncertainty_and_less_error():
    jobs = synthetic_jobs()
    out = fqc.calibrate(fq.normalize_spec(fq.build_template("fdm")), jobs)
    by = {r["parameter"]: r for r in out["parameters"]}
    for name, true in TRUE.items():
        r = by[name]
        assert r["identifiable"] and r["std_error"] is not None
        assert abs(r["after"] - true) <= 3 * r["std_error"] + 1e-9, name   # the truth sits inside the uncertainty
        assert abs(r["after"] / true - 1) < 0.06, name
        assert r["ci_low"] < r["after"] < r["ci_high"]
    e = out["error"]
    assert e["before"]["mape_pct"] > 20 and e["after"]["mape_pct"] < 4
    assert e["improvement_pct"] > 80
    assert out["observations"] == 24 and out["dof"] == 20 and out["converged"]
    # error on jobs the fit did not see is also small (leave-one-job-out)
    assert out["cross_validation"]["mape_pct"] < 6 and out["cross_validation"]["before_mape_pct"] > 20


def test_more_noise_means_wider_intervals():
    spec = fq.normalize_spec(fq.build_template("fdm"))
    quiet = fqc.calibrate(spec, synthetic_jobs(noise=0.01, seed=8))
    noisy = fqc.calibrate(spec, synthetic_jobs(noise=0.08, seed=8))
    w = lambda o: next(r for r in o["parameters"] if r["parameter"] == "print_speed_mm_s")["rel_uncertainty_pct"]  # noqa: E731
    assert w(noisy) > 3 * w(quiet)


def test_parameters_the_data_cannot_pin_down_are_reported_not_invented():
    spec = fq.normalize_spec(fq.build_template("fdm"))
    jobs = synthetic_jobs(targets=["print_time_h"])
    out = fqc.calibrate(spec, jobs, ["print_speed_mm_s", "machine_rate_eur_h"])
    by = {r["parameter"]: r for r in out["parameters"]}
    assert by["print_speed_mm_s"]["identifiable"] and not by["machine_rate_eur_h"]["identifiable"]
    assert by["machine_rate_eur_h"]["after"] == by["machine_rate_eur_h"]["before"]
    assert any("machine_rate_eur_h" in w for w in out["warnings"])
    two = synthetic_jobs(n=1, targets=["print_time_h", "machine_time"])
    with pytest.raises(fq.FabQuoteError, match="not enough"):
        fqc.calibrate(spec, two, ["print_speed_mm_s", "machine_rate_eur_h", "travel_overhead_pct"])
    exact = fqc.calibrate(spec, two, ["print_speed_mm_s", "machine_rate_eur_h"])      # as many observations as unknowns
    assert exact["dof"] == 0 and all(r["std_error"] is None for r in exact["parameters"])
    assert any("no uncertainty" in w for w in exact["warnings"]) and exact["error"]["after"]["mape_pct"] < 1e-4
    with pytest.raises(fq.FabQuoteError, match="nothing can be calibrated"):
        fqc.calibrate(spec, jobs, ["labour_rate_eur_h"])


def test_bad_jobs_are_rejected_with_a_reason():
    spec = fq.normalize_spec(fq.build_template("fdm"))
    good = synthetic_jobs(n=3)
    with pytest.raises(fq.FabQuoteError, match="list of real jobs"):
        fqc.calibrate(spec, [])
    with pytest.raises(fq.FabQuoteError, match="no actuals"):
        fqc.calibrate(spec, [{"label": "x", "parameters": good[0]["parameters"]}])
    with pytest.raises(fq.FabQuoteError, match="positive"):
        fqc.calibrate(spec, [{**good[0], "actuals": {"print_time_h": -2}}])
    with pytest.raises(fq.FabQuoteError, match="one of the parameters being calibrated"):
        fqc.calibrate(spec, [{**good[0], "parameters": {**good[0]["parameters"], "print_speed_mm_s": 50}}], ["print_speed_mm_s"])
    with pytest.raises(fq.FabQuoteError, match="unknown item"):
        fqc.calibrate(spec, [{**good[0], "actuals": {"ghost": 3}}])
    with pytest.raises(fq.FabQuoteError, match="cannot compute"):   # a job without its measurements
        fqc.calibrate(spec, [{"label": "j", "parameters": {}, "actuals": {"print_time_h": 2.0}}] * 3)


def test_applying_a_calibration_changes_the_quote_and_keeps_the_trace(services):
    fq.create_quote(services, "q", "fdm", measurements={"volume_cm3": 80.0, "surface_cm2": 200.0, "part_height_mm": 40.0})
    before_total = fq.get_quote(services, "q")["result"]["total"]["value"]
    dry = fqc.calibrate_quote(services, "q", synthetic_jobs(), apply=False)
    assert dry["applied"] is False
    assert fq.get_quote(services, "q")["result"]["total"]["value"] == before_total
    out = fqc.calibrate_quote(services, "q", synthetic_jobs(), apply=True)
    assert out["applied"] and out["diff"]["total"]["after"] != before_total
    view = fq.get_quote(services, "q")
    params = {p["name"]: p for p in view["result"]["parameters"]}
    assert params["print_speed_mm_s"]["source"] == "calibrated" and "calibrado" not in params["print_speed_mm_s"]["note"]
    assert params["print_speed_mm_s"]["value"] == pytest.approx(TRUE["print_speed_mm_s"], rel=0.06)
    assert "±" in params["print_speed_mm_s"]["note"]
    assert len(view["calibrations"]) == 1 and view["calibrations"][0]["error"]["after_mape_pct"] < 4
    assert not any("print_speed_mm_s" in w and "assumed" in w for w in view["result"]["warnings"] if "assumed" in w and "print_speed" in w.split("assumed")[0])


# ------------------------------------------------------------------------------- slicer G-code
def make_gcode():
    """A 3-layer part. Extrusion 0.05 mm of filament per mm of path; features known by hand:
    skirt 4.0 mm, bottom solid 5.0, perimeter 3 x 4.0 = 12.0, internal infill 3.0, support 2.0, top solid 5.0 (mm of filament)."""
    lines = ["; generated by PrusaSlicer 2.7.0 on 2026-01-01", "M83", "G21", "G90"]

    def run(feature, length, y, z, f=1800):
        lines.extend([";TYPE:" + feature, f"G1 E-0.8 F2400", f"G1 X10 Y{y} F9000", "G1 E0.8 F1500",
                      f"G1 X{10 + length} Y{y} E{length * 0.05:.5f} F{f}"])

    for layer, z in enumerate((0.2, 0.4, 0.6), start=1):
        lines += [";LAYER_CHANGE", f";Z:{z}", f"G1 Z{z} F1200"]
        if layer == 1:
            run("Skirt/Brim", 80, 5, z)
            run("Bottom solid infill", 100, 20, z)
        run("Perimeter", 80, 30, z)
        if layer == 2:
            run("Internal infill", 60, 40, z)
            run("Support material", 40, 50, z)
        if layer == 3:
            run("Top solid infill", 100, 40, z)
    lines += ["; filament used [mm] = 31.00", "; filament used [cm3] = 0.0746", "; filament used [g] = 0.09",
              "; estimated printing time (normal mode) = 1h 2m 3s", "; total layers count = 3", "; layer_height = 0.2",
              "; filament_diameter = 1.75"]
    return "\n".join(lines) + "\n"


def test_gcode_is_read_by_feature_and_retractions_do_not_inflate_it():
    g = fqi.parse_gcode(make_gcode())
    area = math.pi * 0.875 ** 2   # mm2 of a 1.75 mm filament
    cm3 = lambda mm: mm * area / 1000.0  # noqa: E731
    assert g["slicer"] == "PrusaSlicer"
    assert g["time_h"] == pytest.approx((3600 + 120 + 3) / 3600)
    assert g["layers"] == 3 and g["layer_height_mm"] == 0.2 and g["part_height_mm"] == pytest.approx(0.6)
    assert g["filament_length_m"] == pytest.approx(0.031, rel=1e-6)           # 8 retract/unretract pairs are not counted
    assert g["skin_cm3"] == pytest.approx(cm3(10.0), rel=1e-6)
    assert g["support_cm3"] == pytest.approx(cm3(2.0), rel=1e-6)
    assert g["extruded_cm3"] == pytest.approx(cm3(25.0), rel=1e-6)            # part + skin, without support and skirt
    assert g["groups_cm3"]["excluded"] == pytest.approx(cm3(4.0), rel=1e-6)
    assert g["path_length_m"] == pytest.approx((80 + 100 + 240 + 60 + 40 + 100) / 1000.0)
    assert g["time_h_from_moves"] > 0 and g["has_feature_types"]
    assert not any("slicer reports" in w for w in g["warnings"])


def test_gcode_without_types_or_slicer_time_is_honest():
    text = "\n".join(l for l in make_gcode().splitlines() if not l.startswith((";TYPE", "; estimated")))
    g = fqi.parse_gcode(text)
    assert g["time_h"] is None and g["skin_cm3"] is None and not g["has_feature_types"]
    assert any("TYPE" in w for w in g["warnings"]) and any("print time" in w for w in g["warnings"])
    with pytest.raises(fq.FabQuoteError, match="no extruding"):
        fqi.parse_gcode("G1 X10 Y10 F3000\nG1 X20 Y10\n")
    with pytest.raises(fq.FabQuoteError, match="empty"):
        fqi.parse_gcode("  \n")


def test_gcode_fills_the_fdm_quote_with_its_own_numbers(services):
    fq.create_quote(services, "q", "fdm", measurements=FDM_FULL)
    pre = fqi.import_toolpath(services, "q", make_gcode(), apply=False, label="part.gcode")
    assert not pre["applied"] and "slicer_time_h" in pre["parameters"]
    assert item(fq.get_quote(services, "q"), "print_time_h")["quality"] == "exact"
    out = fqi.import_toolpath(services, "q", make_gcode(), label="part.gcode")
    assert out["applied"]
    v = fq.get_quote(services, "q")
    area = math.pi * 0.875 ** 2
    assert item(v, "print_time_h")["value"] == pytest.approx(3723 / 3600)
    assert item(v, "extruded_cm3")["value"] == pytest.approx(25 * area / 1000)
    assert item(v, "skin_cm3")["value"] == pytest.approx(10 * area / 1000)
    price = 1.24 * 0.022
    assert item(v, "mat_skin")["value"] == pytest.approx(10 * area / 1000 * price)
    assert item(v, "mat_supports")["value"] == pytest.approx(2 * area / 1000 * price)
    params = {p["name"]: p for p in v["result"]["parameters"]}
    assert params["slicer_time_h"]["source"] == "measured" and "part.gcode" in params["slicer_time_h"]["note"]
    assert v["provenance"]["toolpath"][0]["sha256"] == out["reading"]["sha256"]
    # no skin warning once the slicer separated the layers
    assert not any("top_bottom_area_cm2" in w for w in v["result"]["warnings"])


def test_a_gcode_without_types_uses_total_filament_and_does_not_add_support_twice(services):
    text = "\n".join(l for l in make_gcode().splitlines() if not l.startswith(";TYPE"))
    fq.create_quote(services, "q", "fdm", measurements=FDM_FULL)
    fqi.import_toolpath(services, "q", text)
    v = fq.get_quote(services, "q")
    assert item(v, "mat_supports")["value"] == 0
    assert item(v, "extruded_cm3")["value"] == pytest.approx(31 * math.pi * 0.875 ** 2 / 1000, rel=1e-3)


# ------------------------------------------------------------------------------- trajectory files
def test_trajectory_csv_and_json_give_the_path_length(services):
    t = fqi.parse_trajectory("x,y,z\n0,0,0\n1000,0,0\n1000,1000,100\n")
    assert t["path_length_mm"] == pytest.approx(1000 + math.hypot(1000, 100)) and t["layers"] == 2
    j = fqi.parse_trajectory('{"points": [[0,0],[3,4]]}', unit="m")
    assert j["path_length_m"] == pytest.approx(5.0) and j["layers"] is None and j["warnings"]
    fq.create_quote(services, "c", "concrete", measurements={"volume_cm3": 1000.0})
    out = fqi.import_toolpath(services, "c", "0,0,0\n2000,0,0\n2000,500,300\n", kind="trajectory", label="walls.csv")
    p = {x["name"]: x for x in fq.get_quote(services, "c")["result"]["parameters"]}
    assert p["toolpath_length_m"]["value"] == pytest.approx((2000 + math.hypot(500, 300)) / 1000) and p["toolpath_length_m"]["source"] == "measured"
    assert out["applied"]
    for bad, msg in (("", "empty"), ("1,2", "at least 2"), ("0,0\n1,x", "not numeric"), ("[1,2]", "CSV rows")):
        with pytest.raises(fq.FabQuoteError, match=msg):
            fqi.parse_trajectory(bad)
    with pytest.raises(fq.FabQuoteError, match="unknown unit"):
        fqi.parse_trajectory("0,0\n1,1", unit="furlong")


# ------------------------------------------------------------------------------- Vulcan inspection
REPORT = {
    "kind": "draft", "profile": {"id": "fdm-print"}, "source": {"model_id": 7, "name": "bracket"},
    "summary": {"compliant": False, "fully_verified": False, "verdict": "2 defects"},
    "checks": [{"rule": "require_watertight", "status": "fail"}, {"rule": "min_wall_mm", "status": "fail"},
               {"rule": "max_extent_mm", "status": "not_evaluated"}],
    "defects": [{"id": "D1", "rule": "require_watertight", "observed": False, "limit": True, "message": "the mesh has open edges"},
                {"id": "D2", "rule": "min_wall_mm", "observed": 0.4, "limit": 0.8, "message": "walls of 0.4 mm are thinner than 0.8 mm"}],
    "blockers": [{"defect": "D1", "reason": "it cannot be repaired automatically"}],
    "recommendations": [{"id": "R1", "for_defect": "D1", "operation": "repair", "automatic": False, "text": "close the holes"},
                        {"id": "R2", "for_defect": None, "operation": "verify_units", "automatic": False, "text": "check the units"},
                        {"id": "R3", "for_defect": "D2", "operation": "thicken", "automatic": False, "text": "thicken the walls"}],
    "notes": ["measured on CPU"],
}


def test_inspection_becomes_structured_warnings():
    out = fqi.inspection_warnings(REPORT)
    ws = out["warnings"]
    assert [w["severity"] for w in ws] == ["error", "warning", "warning"]
    assert ws[0]["code"] == "require_watertight" and ws[0]["affects"] == ["volume_cm3", "surface_cm2"]
    assert ws[0]["suggestion"] == "close the holes" and ws[0]["blocker"] == "it cannot be repaired automatically" and ws[0]["ref"] == "D1"
    assert ws[1]["code"] == "min_wall_mm" and "perimeters" in ws[1]["affects"]
    assert ws[2]["code"] == "verify_units"
    assert out["counts"] == {"error": 1, "warning": 2, "info": 0} and out["compliant"] is False
    with pytest.raises(fq.FabQuoteError, match="not a Vulcan inspection"):
        fqi.inspection_warnings({"hello": 1})


def test_the_inspection_is_asked_through_the_hub_and_kept_on_the_quote(services):
    fq.create_quote(services, "q", "fdm", measurements=FDM_FULL)
    asked = {}

    def fake_call(app, tool, args, timeout=None):
        asked.update(app=app, tool=tool, args=args)
        return {"ok": True, "result": REPORT}

    out = fqi.inspect_quote(services, "q", 7, "fdm-print", call=fake_call)
    assert asked["app"] == "vulcan" and asked["tool"] == "asset_inspect" and asked["args"]["id"] == 7 and asked["args"]["target"] == "fdm-print"
    assert out["counts"]["error"] == 1
    v = fq.get_quote(services, "q")
    assert v["inspection"]["model_id"] == 7 and len(v["inspection"]["warnings"]) == 3
    assert any("[error]" in w and "open edges" in w for w in v["warnings"])
    # a Hub that is down or a Vulcan that refuses is an explained error, not a crash
    with pytest.raises(fq.FabQuoteError, match="Vulcan did not return"):
        fqi.inspect_quote(services, "q", 7, call=lambda *a, **k: {"ok": False, "error": "no such model"})
    def boom(*a, **k):
        raise ConnectionError("hub down")
    with pytest.raises(fq.FabQuoteError, match="Hub"):
        fqi.inspect_quote(services, "q", 7, call=boom)
    with pytest.raises(fq.FabQuoteError):
        fqi.inspect_quote(services, "q", "abc", call=fake_call)


# ------------------------------------------------------------------------------- REST, tool, language
def test_rest_calibration_toolpath_and_inspection(client):
    client.post("/api/fabquote/quotes", json={"name": "q", "process": "fdm", "measurements": FDM_FULL})
    r = client.post("/api/fabquote/quotes/q/calibrate", json={"jobs": synthetic_jobs()})
    assert r.status_code == 200, r.text
    assert r.json()["error"]["after"]["mape_pct"] < 4 and r.json()["applied"] is False
    bad = client.post("/api/fabquote/quotes/q/calibrate?lang=es", json={"jobs": []})
    assert bad.status_code == 400 and "trabajos reales" in bad.json()["error"]
    pre = client.post("/api/fabquote/toolpath", json={"content": make_gcode()})
    assert pre.status_code == 200 and pre.json()["reading"]["layers"] == 3
    ok = client.post("/api/fabquote/quotes/q/toolpath", json={"content": make_gcode(), "label": "x.gcode"})
    assert ok.status_code == 200 and ok.json()["applied"]
    assert client.post("/api/fabquote/quotes/q/toolpath", json={"content": "G1 X1 Y1\n"}).status_code == 400
    assert client.post("/api/fabquote/quotes/nope/inspect", json={"model_id": 1}).status_code == 404


def test_tool_actions_and_spanish(services):
    call = lambda args: call_tool_sync(services, "data_fabquote", args)  # noqa: E731
    call({"action": "create", "name": "t", "process": "fdm", "measurements": FDM_FULL})
    cal = call({"action": "calibrate", "name": "t", "jobs": synthetic_jobs(), "apply": True})
    assert cal["applied"] and cal["error"]["improvement_pct"] > 80
    tp = call({"action": "toolpath", "name": "t", "content": make_gcode(), "apply": False})
    assert tp["applied"] is False and tp["reading"]["slicer"] == "PrusaSlicer"
    tp2 = call({"action": "toolpath", "name": "t", "content": make_gcode(), "label": "a.gcode"})
    assert tp2["applied"] is True
    es = call({"action": "calibrate", "name": "t", "lang": "es", "jobs": synthetic_jobs()})
    assert "Mínimos cuadrados" in es["method"]
    with pytest.raises(ValueError, match="jobs is required"):
        call({"action": "calibrate", "name": "t"})
    with pytest.raises(ValueError, match="content is required"):
        call({"action": "toolpath", "name": "t"})

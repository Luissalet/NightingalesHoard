"""Twins, experimental campaigns and effects, against synthetic data with known answers."""
import asyncio

import numpy as np
import pytest

from nightingale import effects as ef
from nightingale import experiments as ex
from nightingale import twin as tw
from nightingale.agent_tools import call_tool


# ------------------------------------------------------------------ twin
def arx_data(n=420, seed=0, a1=0.7, b1=0.5, b2=0.2, noise=0.02):
    rng = np.random.default_rng(seed)
    u = np.repeat(rng.uniform(-1, 1, n // 8 + 1), 8)[:n]
    e = rng.normal(0, noise, n)
    y = np.zeros(n)
    for t in range(2, n):
        y[t] = a1 * y[t - 1] + b1 * u[t - 1] + b2 * u[t - 2] + e[t]
    return np.arange(n) * 60.0, y, u


def fit_arx(**kw):
    t, y, u = arx_data(**kw)
    return tw.fit_series(t, y, u, ["y"], ["u"], "arx", n_boot=80, seed=1)


def test_arx_recovers_known_coefficients_and_validates():
    res = fit_arx()
    rep = res["report"]
    coef = {p["term"]: p for p in rep["parameters"]}
    # the truth is a1=0.7, b1=0.5, b2=0.2; the lag-2 output term should be near zero
    assert coef["u[t-1]"]["estimate"] == pytest.approx(0.5, abs=0.03)
    assert coef["u[t-2]"]["estimate"] == pytest.approx(0.2, abs=0.04)
    assert coef["y[t-1]"]["estimate"] + coef.get("y[t-2]", {"estimate": 0})["estimate"] == pytest.approx(0.7, abs=0.06)
    assert coef["u[t-1]"]["ci_low"] < 0.5 < coef["u[t-1]"]["ci_high"]
    gain = rep["gains"][0]
    assert gain["gain"] == pytest.approx((0.5 + 0.2) / (1 - 0.7), rel=0.08)
    assert gain["ci_low"] < gain["gain"] < gain["ci_high"]
    v = rep["validation"]["free_run"]["y"]
    assert v["nrmse"] < 0.05 and v["r2"] > 0.95
    assert rep["validation"]["one_step"]["y"]["nrmse"] < 0.05
    assert rep["stable"] and rep["split"]["validation_rows"] >= 100
    # lag selection uses a block that is not the held-out block
    assert "kept apart" in rep["lag_selection"]["criterion"]
    assert rep["interval_coverage"]["y"] > 0.8
    assert all(abs(z["magnitude"]) < 1 for z in rep["poles"])


def test_residual_autocorrelation_check_flags_missing_dynamics():
    # a model forced to lag 1 cannot capture a two-lag input effect plus coloured noise
    rng = np.random.default_rng(3)
    n = 400
    u = np.repeat(rng.uniform(-1, 1, n // 8 + 1), 8)[:n]
    e = np.zeros(n)
    w = rng.normal(0, 0.1, n)
    for t in range(1, n):
        e[t] = 0.9 * e[t - 1] + w[t]          # strongly coloured disturbance
    y = np.zeros(n)
    for t in range(1, n):
        y[t] = 0.5 * y[t - 1] + 0.4 * u[t - 1] + e[t]
    res = tw.fit_series(np.arange(n, dtype=float), y, u, ["y"], ["u"], "arx", max_lag=1, n_boot=40)
    rc = res["report"]["residual_check"]
    assert not rc["ok"]
    assert any("autocorrelated" in w for w in res["report"]["warnings"])


def test_whatif_intervals_and_extrapolation_refusal():
    twin = fit_arx()["twin"]
    lo, hi = twin["training"]["input_range"]["u"]
    last = twin["history"]["U"][-1][0]
    target = -0.8 if last > 0 else 0.8
    out = tw.predict(twin, {"u": {"step": {"at": 5, "from": last, "to": target}}}, steps=30, seed=2)
    y = out["outputs"]["y"]
    # steady state of the new level is about gain * level
    assert y["nominal"][-1] == pytest.approx(target * (0.7 / 0.3), rel=0.12)
    assert all(a <= b <= c for a, b, c in zip(y["lo"], y["nominal"], y["hi"]))
    assert abs(out["delta_vs_baseline"]["y"]["final_nominal"]) > 1
    assert not out["extrapolation"]["outside_training_range"]
    # a mild excursion is flagged, not refused
    mild = tw.predict(twin, {"u": hi + 0.1 * (hi - lo)}, steps=10)
    assert mild["extrapolation"]["outside_training_range"]
    assert any("outside the training range" in w for w in mild["warnings"])
    # a far excursion is refused, and can be forced
    with pytest.raises(tw.TwinError, match="refusing to extrapolate"):
        tw.predict(twin, {"u": hi + 3 * (hi - lo)}, steps=10)
    forced = tw.predict(twin, {"u": hi + 3 * (hi - lo)}, steps=10, allow_extrapolation=True)
    assert forced["extrapolation"]["allowed_by_caller"] and forced["extrapolation"]["outside_training_range"]
    with pytest.raises(tw.TwinError, match="unknown input"):
        tw.predict(twin, {"nope": 1}, steps=3)


def test_fopdt_recovers_gain_time_constant_and_dead_time():
    rng = np.random.default_rng(5)
    n, dt = 300, 1.0
    u = np.where(np.arange(n) >= 40, 1.0, 0.0)
    u[200:] = 0.3
    x = np.zeros(n)
    a = np.exp(-dt / 15)
    ud = np.concatenate([np.zeros(5), u])[:n]            # dead time of 5 samples
    for k in range(1, n):
        x[k] = a * x[k - 1] + 2.0 * (1 - a) * ud[k - 1]
    y = 3 + x + rng.normal(0, 0.03, n)
    res = tw.fit_series(np.arange(n) * dt, y, u, ["y"], ["u"], "fopdt", n_boot=40, seed=2)
    p = {q["term"]: q for q in res["report"]["parameters"]}
    assert p["gain"]["estimate"] == pytest.approx(2.0, abs=0.1)
    assert p["time_constant"]["estimate"] == pytest.approx(15, rel=0.1)
    assert p["dead_time"]["estimate"] == pytest.approx(5, abs=1.0)
    assert p["gain"]["ci_low"] < 2.0 < p["gain"]["ci_high"]
    assert res["report"]["validation"]["free_run"]["y"]["nrmse"] < 0.12
    auto = tw.fit_series(np.arange(n) * dt, y, u, ["y"], ["u"], "auto", n_boot=30, seed=2)
    assert auto["report"]["model"] in ("arx", "fopdt")
    sim = tw.predict(res["twin"], {"u": {"step": {"at": 2, "from": 0.3, "to": 1.0}}}, steps=60)
    assert sim["outputs"]["y"]["nominal"][-1] > sim["outputs"]["y"]["nominal"][0]
    with pytest.raises(tw.TwinError, match="exactly one input"):
        tw.fit_series(np.arange(n) * dt, np.column_stack([y, y]), np.column_stack([u, u]), ["a", "b"], ["c", "d"], "fopdt")


def test_twin_refuses_unidentifiable_and_short_data():
    t = np.arange(100.0)
    with pytest.raises(tw.TwinError, match="does not vary"):
        tw.fit_series(t, np.sin(t), np.ones(100), ["y"], ["u"], "arx")
    with pytest.raises(tw.TwinError, match="usable rows"):
        tw.fit_series(t[:20], np.sin(t[:20]), np.cos(t[:20]), ["y"], ["u"], "arx")


def test_twin_saved_listed_simulated_via_service_api_and_tool(client):
    t, y, u = arx_data(n=300)
    csv = "ts,y,u\n" + "\n".join(f"{int(a)},{b:.6f},{c:.6f}" for a, b, c in zip(t, y, u))
    client.services.ingest_text(csv, "plant", "csv")
    r = client.post("/api/twin/fit", json={"dataset": "plant", "time": "ts", "outputs": ["y"], "inputs": ["u"], "n_boot": 40, "name": "Plant"})
    assert r.status_code == 200, r.text
    rep = r.json()
    assert rep["saved_as"] == "Plant" and rep["log_id"]
    assert rep["validation"]["free_run"]["y"]["nrmse"] < 0.08
    assert client.get("/api/twin/models").json()["count"] == 1
    assert client.get("/api/twin/models/Plant").json()["twin"]["outputs"] == ["y"]
    sim = client.post("/api/twin/simulate", json={"name": "Plant", "profile": {"u": 0.5}, "steps": 20, "save_as": "plant_whatif"}).json()
    assert sim["saved_dataset"] == "plant_whatif" and len(sim["time"]) == 20
    bad = client.post("/api/twin/simulate", json={"name": "Plant", "profile": {"u": 9}, "steps": 5})
    assert bad.status_code == 400 and "refusing to extrapolate" in bad.json()["error"]
    assert client.post("/api/twin/simulate", json={"name": "Nope", "steps": 5}).status_code == 404

    async def go():
        listed = await call_tool(client.services, "data_twin", {"action": "list"})
        ran = await call_tool(client.services, "data_twin", {"action": "simulate", "name": "Plant", "profile": {"u": {"ramp": {"to": 0.4}}}, "steps": 10})
        gone = await call_tool(client.services, "data_twin", {"action": "delete", "name": "Plant"})
        return listed, ran, gone
    listed, ran, gone = asyncio.run(go())
    assert listed["count"] == 1 and len(ran["outputs"]["y"]["lo"]) == 10 and gone["deleted"]


# ------------------------------------------------------------------ experiments: design
FACTORS5 = [{"name": n, "low": 0, "high": 10} for n in "ABCDE"]


def runs_matrix(d):
    return np.array([[r[f["name"]] for f in d["factors"]] for r in d["runs"]])


def test_fractional_factorial_resolution_aliases_and_balance():
    d = ex.design_experiment(FACTORS5, "fractional", min_resolution=5, center_points=0, randomize=False)
    assert d["n_runs"] == 16 and d["info"]["resolution"] == "V" and d["info"]["generators"] == ["E=ABCD"]
    assert d["properties"]["estimable"]["two_factor_interactions"]
    assert d["properties"]["max_abs_correlation_between_factors"] < 1e-9
    d4 = ex.design_experiment([{"name": n, "low": -1, "high": 1} for n in "ABCD"], "fractional", min_resolution=4, center_points=0)
    assert d4["n_runs"] == 8 and d4["info"]["resolution"] == "IV"
    al = {a["effect"]: a["aliased_with"] for a in d4["info"]["alias_structure"]}
    assert al["A"] == ["B:C:D"] and "C:D" in al["A:B"]
    # resolution of a 2^(5-2) design is III and says so
    d3 = ex.design_experiment(FACTORS5, "fractional", fraction=2, center_points=0)
    assert d3["info"]["resolution"] == "III" and d3["n_runs"] == 8
    assert any("Resolution III" in w for w in d3["warnings"])
    assert ex.fractional_structure(7, 4)["resolution"] == 3 and ex.fractional_structure(8, 2)["resolution"] == 5
    # generators are algebraically consistent: the added column equals the product of its word
    coded, _ = ex.coded_factorial(5, 1)
    assert np.allclose(coded[:, 4], coded[:, 0] * coded[:, 1] * coded[:, 2] * coded[:, 3])


def test_designs_ccd_lhs_constraints_and_errors():
    fs = FACTORS5[:3]
    ccd = ex.design_experiment(fs, "ccd", seed=1)
    assert ccd["n_runs"] == 8 + 6 + 4 and ccd["properties"]["estimable"]["quadratic"]
    assert {r["type"] for r in ccd["runs"]} == {"factorial", "axial", "center"}
    circ = ex.design_experiment(fs, "ccd", variant="circumscribed")
    assert circ["info"]["runs_outside_bounds"] and any("outside the stated bounds" in w for w in circ["warnings"])
    a = ex.design_experiment(fs, "lhs", runs=12, seed=7)
    b = ex.design_experiment(fs, "lhs", runs=12, seed=7)
    assert a["runs"] == b["runs"] and a["n_runs"] == 12
    X = runs_matrix(a)
    for j in range(3):   # one run in each tenth of the range
        assert sorted(np.floor(X[:, j] / 10 * 12).astype(int)) == list(range(12))
    c = ex.design_experiment(fs, "lhs", runs=12, seed=7, forbidden=["A + B > 14"])
    assert not (runs_matrix(c)[:, 0] + runs_matrix(c)[:, 1] > 14).any()
    full = ex.design_experiment(fs, "full", center_points=0, forbidden=[{"A": [10, 10], "B": [10, 10]}])
    assert full["n_runs"] == 6 and full["removed_runs"] and any("no longer orthogonal" in w for w in full["warnings"])
    assert full["properties"]["max_abs_correlation_between_factors"] > 0
    with pytest.raises(ex.ExperimentError, match="every planned run"):
        ex.design_experiment(fs, "full", forbidden=["A >= 0"])
    with pytest.raises(ex.ExperimentError, match="unknown factor"):
        ex.design_experiment(fs, "full", forbidden=["Z > 1"])
    with pytest.raises(ex.ExperimentError, match="not a valid expression|support"):
        ex.design_experiment(fs, "full", forbidden=["__import__('os')"])
    with pytest.raises(ex.ExperimentError, match="greater than low"):
        ex.design_experiment([{"name": "A", "low": 3, "high": 3}], "full")


# ------------------------------------------------------------------ experiments: analysis and next run
def test_main_effects_and_interactions_recovered_with_confidence():
    rng = np.random.default_rng(0)
    d = ex.design_experiment([{"name": n, "low": 0, "high": 10} for n in "ABCD"], "full", center_points=2, randomize=False)
    X = runs_matrix(d)
    C = ex.encode(X, d["factors"])
    y = 10 + 3 * C[:, 0] - 2 * C[:, 1] + 1.5 * C[:, 0] * C[:, 1] + rng.normal(0, 0.2, len(C))
    a = ex.analyze_experiment(X, y, d["factors"], "y")
    t = {r["term"]: r for r in a["terms"]}
    assert t["A"]["coefficient"] == pytest.approx(3, abs=0.2) and t["A"]["effect"] == pytest.approx(6, abs=0.4)
    assert t["B"]["coefficient"] == pytest.approx(-2, abs=0.2)
    assert t["A:B"]["coefficient"] == pytest.approx(1.5, abs=0.2)
    assert t["A"]["ci_low"] < 3 < t["A"]["ci_high"]
    assert not t["C"]["significant"] and not t["A:C"]["significant"] and t["A"]["significant"] and t["A:B"]["significant"]
    assert a["ranking"][:3] == ["A", "B", "A:B"]
    assert a["curvature"]["p_value"] > 0.05                      # linear truth: no curvature at the centre
    assert "main_effect_predictions" in a and a["surface"]["x"] == "A"


def test_fractional_analysis_uses_lenth_when_saturated_and_states_aliases():
    rng = np.random.default_rng(1)
    d = ex.design_experiment([{"name": n, "low": -1, "high": 1} for n in "ABCDEFG"], "fractional", fraction=3, center_points=0, randomize=False)
    X = runs_matrix(d)
    y = 5 + 4 * X[:, 0] + 3 * X[:, 3] + rng.normal(0, 0.05, len(X))
    a = ex.analyze_experiment(X, y, d["factors"], "y")
    assert a["fit"]["residual_df"] <= 1 and a["lenth"]["margin_of_error"] > 0
    sig = [r["term"] for r in a["terms"] if r.get("significant")]
    assert "A" in sig and "D" in sig and len(sig) <= 4
    assert any("Lenth" in w for w in a["warnings"])
    assert any("B:C" in t["term"] or "aliased" in " ".join(a["notes"]) for t in a["terms"]) or a["notes"]


def test_curvature_detected_and_quadratic_stationary_point():
    rng = np.random.default_rng(2)
    f2 = [{"name": "A", "low": 0, "high": 10}, {"name": "B", "low": 0, "high": 10}]
    d = ex.design_experiment(f2, "ccd", seed=3, center_points=5, randomize=False)
    X = runs_matrix(d)
    C = ex.encode(X, d["factors"])
    y = 20 - 3 * (C[:, 0] - 0.3) ** 2 - 2 * C[:, 1] ** 2 + 1.0 * C[:, 1] + rng.normal(0, 0.1, len(C))
    a = ex.analyze_experiment(X, y, d["factors"], "y")
    assert a["model"] == "quadratic"
    sp = a["stationary_point"]
    assert sp["type"] == "maximum" and sp["inside_region"]
    assert sp["settings"]["A"] == pytest.approx(5 + 5 * 0.3, abs=0.4)
    assert sp["ci_half"] > 0
    # a straight-line model on the same data warns about curvature
    fact = d["runs"]
    keep = [i for i, r in enumerate(fact) if r["type"] in ("factorial", "center")]
    lin = ex.analyze_experiment(X[keep], y[keep], d["factors"], "y", model="interactions")
    assert lin["curvature"]["p_value"] < 0.05 and any("Curvature detected" in w for w in lin["warnings"])


def test_next_run_picks_the_highest_variance_point():
    # all runs lie in the lower-left corner, so the most uncertain point is the far upper-right corner
    fs = [{"name": "A", "low": 0, "high": 10}, {"name": "B", "low": 0, "high": 10}]
    rng = np.random.default_rng(4)
    X = np.array([[0, 0], [1, 0], [0, 1], [1, 1], [2, 0], [0, 2], [2, 2], [1, 2], [2, 1], [0.5, 0.5]], float)
    y = 1 + 0.5 * X[:, 0] + 0.3 * X[:, 1] + rng.normal(0, 0.1, len(X))
    res = ex.next_runs(X, y, fs, "y", "explore", n=2, model="interactions", seed=0)
    first, second = res["recommendations"]
    assert first["settings"]["A"] == pytest.approx(10) and first["settings"]["B"] == pytest.approx(10)
    # brute force: nothing among the candidates has a larger prediction sd
    pts = ex.candidate_points([{**f, "integer": False} for f in fs], None, None, 0)
    s = ex.fit_surface(ex.encode(X, fs), y, ["A", "B"], "interactions")
    sds = ex.predict_surface(s, ex.encode(pts, fs))["se_mean"]
    assert first["predicted_sd"] == pytest.approx(float(sds.max()), rel=1e-9)
    assert second["settings"] != first["settings"] and second["max_prediction_sd_after"] <= first["max_prediction_sd_after"] + 1e-12
    assert "largest remaining prediction uncertainty" in first["why"] and "lower the largest prediction sd" in first["why"]
    # constraints are honoured and explained
    res2 = ex.next_runs(X, y, fs, "y", "explore", forbidden=["A + B > 12"], model="interactions")
    best = res2["recommendations"][0]["settings"]
    assert best["A"] + best["B"] <= 12


def test_next_run_expected_improvement_prefers_the_promising_uncertain_region():
    fs = [{"name": "A", "low": 0, "high": 10}]
    X = np.array([[0.0], [1.0], [2.0], [3.0], [4.0], [5.0]])
    y = np.array([1.0, 2.1, 2.9, 4.2, 4.9, 6.1])          # rising: maximising points towards the unexplored high end
    res = ex.next_runs(X, y, fs, "y", "maximize", model="interactions", seed=1)
    assert res["recommendations"][0]["settings"]["A"] >= 8
    assert res["recommendations"][0]["expected_improvement"] > 0
    low = ex.next_runs(X, y, fs, "y", "minimize", model="interactions", seed=1)
    assert low["recommendations"][0]["settings"]["A"] < 8 or low["recommendations"][0]["expected_improvement"] >= 0


def test_choose_cheapest_test_that_separates_the_hypotheses():
    tests = [
        {"name": "partial cheap", "cost": 1, "outcomes": {"pump": "low", "valve": "ok", "sensor": "ok"}},
        {"name": "separating cheap", "cost": 1, "outcomes": {"pump": "a", "valve": "b", "sensor": "c"}},
        {"name": "separating expensive", "cost": 8, "outcomes": {"pump": "x", "valve": "y", "sensor": "z"}},
        {"name": "useless", "cost": 0.1, "outcomes": {"pump": "same", "valve": "same", "sensor": "same"}},
    ]
    res = ex.choose_test(["pump", "valve", "sensor"], tests)
    rec = res["recommendation"]
    assert rec["test"] == "separating cheap" and rec["fully_separates"]
    assert rec["information_gain_bits"] == pytest.approx(np.log2(3), abs=1e-9)
    assert res["cheapest_fully_separating"]["test"] == "separating cheap"
    ranking = {r["test"]: r for r in res["ranking"]}
    assert ranking["useless"]["information_gain_bits"] == pytest.approx(0, abs=1e-12)
    assert ranking["partial cheap"]["information_gain_bits"] == pytest.approx(0.9183, abs=1e-3)
    assert "information gain per unit cost" in rec["why"]
    # with only a partial test and a pricey full one, the partial gets planned first and followed up
    res2 = ex.choose_test(["pump", "valve", "sensor"], [tests[0], {"name": "pairs", "cost": 1, "outcomes": {"pump": "a", "valve": "a", "sensor": "b"}}])
    assert res2["recommendation"]["test"] in ("partial cheap", "pairs") and not res2["recommendation"]["fully_separates"]
    assert res2["plan"]["branches"]
    # noisy outcomes and priors
    noisy = ex.choose_test([{"name": "H1", "prior": 0.8}, {"name": "H2", "prior": 0.2}],
                           [{"name": "T", "cost": 2, "outcomes": {"H1": {"hi": 0.9, "lo": 0.1}, "H2": {"hi": 0.2, "lo": 0.8}}}])
    assert 0 < noisy["recommendation"]["information_gain_bits"] < 0.72
    with pytest.raises(ex.ExperimentError, match="sum to 1"):
        ex.choose_test(["a", "b"], [{"name": "t", "outcomes": {"a": {"x": 0.5}, "b": "y"}}])
    with pytest.raises(ex.ExperimentError, match="discriminates"):
        ex.choose_test(["a", "b"], [{"name": "t", "outcomes": {"a": "x", "b": "x"}}])


def test_experiment_service_api_and_tool(client):
    d = ex.design_experiment([{"name": n, "low": 0, "high": 10} for n in "ABC"], "full", center_points=2, randomize=False)
    X = runs_matrix(d)
    C = ex.encode(X, d["factors"])
    rng = np.random.default_rng(6)
    y = 5 + 2 * C[:, 0] + rng.normal(0, 0.1, len(C))
    csv = "A,B,C,yield\n" + "\n".join(",".join(f"{v:.6f}" for v in list(r) + [yy]) for r, yy in zip(X, y))
    client.services.ingest_text(csv, "runs", "csv")
    r = client.post("/api/experiment/design", json={"factors": [{"name": "T", "low": 20, "high": 80}, {"name": "P", "low": 1, "high": 5}, {"name": "pH", "low": 4, "high": 9}],
                                                    "kind": "fractional", "min_resolution": 3, "save_as": "plan", "seed": 3})
    assert r.status_code == 200, r.text
    plan = r.json()
    assert plan["saved_dataset"] == "plan" and plan["info"]["resolution"] == "III" and plan["log_id"]
    an = client.post("/api/experiment/analyze", json={"dataset": "runs", "factors": [{"name": n, "low": 0, "high": 10} for n in "ABC"], "response": "yield"})
    assert an.status_code == 200, an.text
    an = an.json()
    assert an["ranking"][0] == "A" and an["fit"]["r2"] > 0.95
    inferred = client.post("/api/experiment/analyze", json={"dataset": "runs", "factors": ["A", "B", "C"], "response": "yield"}).json()
    assert any("taken from the data" in w for w in inferred["warnings"])
    nx = client.post("/api/experiment/next", json={"dataset": "runs", "factors": [{"name": n, "low": 0, "high": 10} for n in "ABC"], "response": "yield", "objective": "maximize", "n": 2}).json()
    assert len(nx["recommendations"]) == 2 and nx["recommendations"][0]["why"]
    ch = client.post("/api/experiment/choose-test", json={"hypotheses": ["x", "y"], "tests": [{"name": "t", "cost": 1, "outcomes": {"x": "p", "y": "q"}}]}).json()
    assert ch["recommendation"]["test"] == "t"
    assert client.post("/api/experiment/analyze", json={"dataset": "runs", "factors": ["A", "Zed"], "response": "yield"}).status_code == 400

    async def go():
        return await call_tool(client.services, "data_experiment", {"action": "design", "kind": "ccd", "factors": [{"name": "x", "low": 0, "high": 1}, {"name": "z", "low": 0, "high": 1}]})
    assert asyncio.run(go())["n_runs"] == 4 + 4 + 4


# ------------------------------------------------------------------ effects
def did_panel(seed=3, effect=5.0, extra_trend=0.0, units=40, periods=12, t0=8):
    rng = np.random.default_rng(seed)
    rows = []
    for u in range(units):
        treated = u < units // 2
        a = rng.normal(0, 2)
        for t in range(periods):
            y = 10 + a + 0.5 * t + (3 if treated else 0) + (extra_trend * t if treated else 0) + (effect if (treated and t >= t0) else 0) + rng.normal(0, 1)
            rows.append((u, treated, t, y))
    R = np.array(rows, float)
    return R[:, 3], R[:, 1] > 0, R[:, 2], np.array([str(int(v)) for v in R[:, 0]])


def test_did_recovers_effect_and_passes_pretrend_and_placebo():
    y, tr, t, unit = did_panel()
    r = ef.did_effect(y, tr, t, 8.0, unit=unit)
    e = r["effect"]
    assert e["estimate"] == pytest.approx(5.0, abs=0.5) and e["ci_low"] < 5.0 < e["ci_high"]
    assert r["pre_trend"]["p_value"] > 0.10 and r["placebo"]["excludes_zero"] is False
    assert not r["causal_claim"]["assumptions_failed"] and not r["causal_claim"]["established"]
    assert "no other change at the same time" in r["causal_claim"]["untestable_assumptions"]
    assert r["clustered_by_unit"] and r["n_clusters"] == 40


def test_did_flags_pretrend_violation_and_placebo_failure():
    y, tr, t, unit = did_panel(seed=4, extra_trend=0.6)
    r = ef.did_effect(y, tr, t, 8.0, unit=unit)
    assert r["pre_trend"]["p_value"] < 0.01 and r["pre_trend"]["differential_slope_per_period"] == pytest.approx(0.6, abs=0.15)
    assert "parallel pre-trends" in r["causal_claim"]["assumptions_failed"]
    assert any("Parallel pre-trends rejected" in w for w in r["warnings"])
    assert r["placebo"]["excludes_zero"] and "no effect at a placebo date" in r["causal_claim"]["assumptions_failed"]
    assert "do not read the estimate" in r["causal_claim"]["text"]
    assert r["effect"]["estimate"] > 6.5                     # the drift inflates the naive estimate


def test_did_no_effect_gives_zero_and_untestable_with_two_pre_periods():
    y, tr, t, unit = did_panel(seed=5, effect=0.0)
    r = ef.did_effect(y, tr, t, 8.0, unit=unit)
    assert r["effect"]["ci_low"] < 0 < r["effect"]["ci_high"]
    few = (t >= 6)
    r2 = ef.did_effect(y[few], tr[few], t[few], 8.0, unit=unit[few])
    assert r2["assumptions"][0]["status"] == "not_testable" and any("cannot be tested" in w for w in r2["warnings"])
    with pytest.raises(ef.EffectError, match="is empty"):
        ef.did_effect(y[t < 8], tr[t < 8], t[t < 8], 8.0)


def test_its_level_change_recovered_and_placebo_shows_nothing():
    rng = np.random.default_rng(11)
    n = 80
    t = np.arange(n, dtype=float)
    y = 5 + 0.2 * t + np.where(t >= 40, 4 + 0.1 * (t - 40), 0) + rng.normal(0, 0.5, n)
    r = ef.its_effect(y, t, 40.0)
    assert r["effect"]["estimate"] == pytest.approx(4.0, abs=0.7) and r["effect"]["ci_low"] < 4 < r["effect"]["ci_high"]
    assert r["slope_change"]["estimate"] == pytest.approx(0.1, abs=0.04)
    assert r["pre_trend"]["per_step"] == pytest.approx(0.2, abs=0.03)
    assert not any(p["excludes_zero"] for p in r["placebos"]) and len(r["placebos"]) == 3
    assert not r["causal_claim"]["assumptions_failed"]
    assert len(r["chart"]["counterfactual"]) == n and r["chart"]["counterfactual"][-1] < r["chart"]["observed"][-1]
    # no change in the series: the interval covers zero
    flat = ef.its_effect(5 + 0.2 * t + rng.normal(0, 0.5, n), t, 40.0)
    assert flat["effect"]["ci_low"] < 0 < flat["effect"]["ci_high"]


def test_its_seasonal_and_curved_pre_period_warnings():
    rng = np.random.default_rng(8)
    n = 96
    t = np.arange(n, dtype=float)
    y = 10 + 3 * np.sin(2 * np.pi * t / 12) + np.where(t >= 60, 2.5, 0) + rng.normal(0, 0.3, n)
    r = ef.its_effect(y, t, 60.0, season_period=12)
    assert r["effect"]["estimate"] == pytest.approx(2.5, abs=0.6)
    curved = 0.01 * (t - 30) ** 2 + np.where(t >= 60, 1.0, 0) + rng.normal(0, 0.3, n)
    c = ef.its_effect(curved, t, 60.0)
    assert any("curved" in w for w in c["warnings"])
    with pytest.raises(ef.EffectError, match="at least 5 points before"):
        ef.its_effect(y[:10], t[:10], 3.0)


def test_adjustment_recovers_effect_and_reports_sensitivity():
    rng = np.random.default_rng(9)
    n = 600
    x = rng.normal(0, 1, n)
    z = rng.normal(0, 1, n)
    treat = (rng.random(n) < 1 / (1 + np.exp(-1.0 * x))).astype(float)
    y = 2 + 3 * treat + 2.5 * x + 0.5 * z + rng.normal(0, 1, n)
    r = ef.adjust_effect(y, treat, np.column_stack([x, z]), ["x", "z"])
    assert r["effect"]["estimate"] == pytest.approx(3.0, abs=0.35)
    assert r["effect"]["naive_estimate"] > 4.0 and r["effect"]["shift_from_adjustment"] < -1
    assert [s["adjusted_for"] for s in r["stability"]] == [[], ["x"], ["x", "z"]]
    assert r["sensitivity"]["robustness_value"] > 0.2
    assert r["balance"][0]["imbalanced"] and r["sensitivity"]["strongest_observed_confounder"]["confounder"] == "x"
    omitted = ef.adjust_effect(y, treat, z[:, None], ["z"])
    assert omitted["effect"]["estimate"] > 4.0      # leaving out the real confounder leaves the bias in
    bad_placebo = ef.adjust_effect(y, treat, z[:, None], ["z"], placebo_y=2.5 * x + rng.normal(0, 1, n))
    assert "no effect on a placebo outcome" in bad_placebo["causal_claim"]["assumptions_failed"]
    assert any("placebo outcome shows an 'effect'" in w for w in bad_placebo["warnings"])
    none = ef.adjust_effect(y, treat, np.empty((n, 0)), [])
    assert any("No confounders were listed" in w for w in none["warnings"])


def test_effect_service_api_and_tool(client):
    y, tr, t, unit = did_panel(units=24)
    csv = "unit,grp,period,sales\n" + "\n".join(f"{u},{'treated' if g else 'control'},{int(p)},{v:.5f}" for u, g, p, v in zip(unit, tr, t, y))
    client.services.ingest_text(csv, "panel", "csv")
    r = client.post("/api/effect/estimate", json={"method": "did", "dataset": "panel", "outcome": "sales", "time": "period", "intervention": 8,
                                                  "group": "grp", "treated_value": "treated", "unit": "unit"})
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["effect"]["estimate"] == pytest.approx(5.0, abs=0.8) and body["log_id"] and body["causal_claim"]["established"] is False
    missing = client.post("/api/effect/estimate", json={"method": "did", "dataset": "panel", "outcome": "sales", "time": "period", "intervention": 8, "group": "grp"})
    assert missing.status_code == 400 and "treated_value" in missing.json()["error"]
    ts = "day,visits\n" + "\n".join(f"2026-01-{d:02d},{10 + 0.1 * d + (5 if d > 20 else 0):.3f}" for d in range(1, 29))
    client.services.ingest_text(ts, "visits", "csv")
    its = client.post("/api/effect/estimate", json={"method": "its", "dataset": "visits", "outcome": "visits", "time": "day", "intervention": "2026-01-21"})
    assert its.status_code == 200, its.text
    assert its.json()["effect"]["estimate"] == pytest.approx(5.0, abs=0.7) and its.json()["time_kind"] == "timestamp"

    async def go():
        return await call_tool(client.services, "data_effect", {"method": "adjust", "dataset": "panel", "outcome": "sales", "treatment": "grp",
                                                                 "treated_value": "treated", "confounders": ["period"]})
    assert asyncio.run(go())["method"] == "adjust"

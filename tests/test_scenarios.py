"""Scenario lab: the event engine against queueing theory, determinism, common random numbers, loss, stock."""
import asyncio

import pytest

from nightingale import scenarios as sc
from nightingale.agent_tools import call_tool


def mm(c, lam, mean, **extra):
    m = {"horizon_hours": 600, "warmup_hours": 60, "replications": 16, "seed": 11,
         "arrivals": {"type": "poisson", "rate": lam},
         "stations": [{"name": "S", "servers": c, "service": {"dist": "exponential", "mean": mean}}]}
    m.update(extra)
    return m


@pytest.mark.parametrize("c,lam,mean", [(1, 4.0, 0.125), (2, 6.0, 0.2)])
def test_mmc_waiting_time_matches_erlang_c(c, lam, mean):
    r = sc.run_model(mm(c, lam, mean))
    a = r["checks"]["analytic"]
    assert a["model"] == f"M/M/{c}"
    assert a["relative_difference"] < 0.15
    assert a["simulated_ci"][0] - 0.02 <= a["wq_hours"] <= a["simulated_ci"][1] + 0.02
    assert a["rho"] == pytest.approx(lam * mean / c)


def test_erlang_c_known_value():
    # M/M/2, lambda 6, mu 5: a=1.2, rho=0.6, P(wait)=1.8/(1+1.2+1.8)
    e = sc.erlang_c(2, 6.0, 5.0)
    assert e["p_wait"] == pytest.approx(0.45, abs=1e-6)
    assert e["wq_hours"] == pytest.approx(0.45 / (10 - 6), abs=1e-6)


def test_littles_law_holds():
    r = sc.run_model(mm(1, 4.0, 0.15))
    ll = r["checks"]["littles_law"]
    assert ll["ok"] and ll["relative_difference"] < 0.05


def test_same_seed_same_result_and_other_seed_differs():
    a = sc.run_model(mm(1, 4.0, 0.15))
    b = sc.run_model(mm(1, 4.0, 0.15))
    c = sc.run_model(mm(1, 4.0, 0.15), seed=99)
    assert a["metrics"] == b["metrics"]
    assert a["metrics"]["throughput"]["mean"] != c["metrics"]["throughput"]["mean"]


def test_compare_with_common_random_numbers_prefers_the_faster_server():
    base = mm(1, 4.0, 0.2)
    out = sc.compare_models(base, [{"name": "faster", "overrides": {"stations.S.service.mean": 0.15}},
                                   {"name": "same", "overrides": {"stations.S.service.mean": 0.2}}])
    rows = {r["metric"]: r for r in out["variants"][0]["rows"]}
    w = rows["lead_time_mean"]
    assert w["delta"] < 0 and w["distinguishable"] and w["better"] == "variant"
    assert rows["wait:S"]["better"] == "variant"
    # an identical variant has exactly zero difference under common random numbers
    same = {r["metric"]: r for r in out["variants"][1]["rows"]}["lead_time_mean"]
    assert same["delta"] == 0 and not same["distinguishable"]


def test_sweep_waiting_falls_as_servers_rise():
    out = sc.sweep_model(mm(1, 4.0, 0.2, replications=6, horizon_hours=300, warmup_hours=30), "stations.S.servers",
                         [1, 2, 3])
    waits = [p["metrics"]["wait:S"]["mean"] for p in out["points"]]
    assert waits[0] > waits[1] > waits[2]


def test_buffer_limit_loses_jobs_and_block_does_not():
    lossy = mm(1, 6.0, 0.2, replications=6)
    lossy["stations"][0].update(buffer=2, on_full="lose")
    r = sc.run_model(lossy)
    assert r["metrics"]["lost_jobs"]["mean"] > 10
    assert r["metrics"]["lost:S"]["mean"] == r["metrics"]["lost_jobs"]["mean"]
    # M/M/1/1 loss: fraction served matches Erlang-B (rho/(1+rho) blocked)
    pure = mm(1, 4.0, 0.25, replications=10)
    pure["stations"][0].update(buffer=0, on_full="lose")
    r = sc.run_model(pure)
    lost, thr = r["metrics"]["lost_jobs"]["mean"], r["metrics"]["throughput"]["mean"] * 540
    assert lost / (lost + thr) == pytest.approx(0.5, abs=0.05)


def test_two_stations_with_blocking_keeps_flow_conserved():
    m = {"horizon_hours": 300, "warmup_hours": 30, "replications": 6, "seed": 5,
         "arrivals": {"type": "poisson", "rate": 3},
         "stations": [{"name": "A", "servers": 1, "service": {"dist": "exponential", "mean": 0.2}},
                      {"name": "B", "servers": 1, "service": {"dist": "exponential", "mean": 0.3},
                       "buffer": 1, "on_full": "block"}]}
    r = sc.run_model(m)
    assert r["metrics"]["lost_jobs"]["mean"] == 0
    assert r["metrics"]["throughput"]["mean"] < 3.3
    assert r["metrics"]["wait:A"]["mean"] > 0


def test_inventory_stockouts_with_low_reorder_point():
    def model(point, qty, initial=10):
        return mm(1, 3.0, 0.15, replications=6, horizon_hours=200, warmup_hours=20,
                  inventory={"initial": initial, "reorder_point": point, "order_quantity": qty,
                             "lead_time": {"dist": "fixed", "value": 4}})
    low = sc.run_model(model(1, 10))
    high = sc.run_model(model(40, 40, 60))
    assert low["metrics"]["stockouts"]["mean"] > 0
    assert high["metrics"]["stockouts"]["mean"] == 0
    assert low["metrics"]["throughput"]["mean"] < high["metrics"]["throughput"]["mean"]
    assert low["metrics"]["lead_time_mean"]["mean"] > high["metrics"]["lead_time_mean"]["mean"]


def test_shifts_and_costs():
    m = mm(1, 2.0, 0.2, replications=4, horizon_hours=240, warmup_hours=24,
           costs={"server_hour": 10, "waiting_hour": 0, "lost_job": 0})
    m["stations"][0]["shifts"] = [{"from": 8, "to": 16, "servers": 1}]
    r = sc.run_model(m)
    # server cost only for the 8 hours a day the server is scheduled: 216 h window / 3 days
    assert r["metrics"]["total_cost"]["mean"] == pytest.approx(10 * 8 * 9, rel=0.02)
    assert r["metrics"]["wait:S"]["mean"] > 1  # work piles up overnight


def test_distributions_and_validation():
    for spec in ({"dist": "fixed", "value": 1}, {"dist": "uniform", "min": 1, "max": 3},
                 {"dist": "triangular", "min": 1, "mode": 2, "max": 4}, {"dist": "lognormal", "mean": 2, "sigma": .5},
                 {"dist": "empirical", "values": [1, 2, 3]}):
        f = sc.make_sampler(spec, "x")
        assert 0 <= f(0.01) <= f(0.99)
    with pytest.raises(sc.ScenarioError):
        sc.run_model({"arrivals": {"type": "poisson", "rate": -1}, "stations": []})
    with pytest.raises(sc.ScenarioError):
        sc.run_model(mm(0, 1, 1))
    with pytest.raises(sc.ScenarioError):
        sc.apply_overrides(mm(1, 1, 1), {"stations.Nope.servers": 2})


def test_calibration_from_dataset(services):
    rows = "\n".join(f"{0.1 + (i % 7) * 0.02:.3f}" for i in range(60))
    services.ingest_text("minutes\n" + "\n".join(str(round((0.1 + (i % 7) * 0.02) * 60, 3)) for i in range(60)),
                         "svc", "csv")
    res = sc.calibrate_scenario(services, "svc", "minutes", "service", "minutes")
    assert res["sample_size"] == 60 and not res["warnings"]
    assert res["summary"]["mean"] == pytest.approx(0.16, abs=0.01)
    assert res["suggestion"]["dist"] == "empirical"
    small = sc.calibrate_scenario(services, "svc", "minutes", "service", "minutes")
    services.ingest_text("minutes\n5\n6\n7\n", "tiny", "csv")
    tiny = sc.calibrate_scenario(services, "tiny", "minutes", "service", "minutes")
    assert tiny["sample_size"] == 3 and "too little" in tiny["warnings"][0]
    assert small["caveat"].startswith("Scenarios")
    # a model that reads its service times from the dataset reports the sample size
    m = mm(1, 4.0, 0.1, replications=4, horizon_hours=100, warmup_hours=10)
    m["stations"][0]["service"] = {"dist": "empirical", "dataset": "tiny", "column": "minutes", "unit": "minutes"}
    out = sc.run_scenario(services, model=m, save_as="runs")
    assert out["calibration"][0]["sample_size"] == 3
    assert any("too little" in w for w in out["warnings"])
    assert out["saved_dataset"] == "runs"


def test_timestamp_calibration(services):
    ts = "\n".join(f"2026-01-0{1 + d} {h:02d}:{m:02d}:00" for d in range(3) for h in range(8, 12) for m in (0, 20, 40))
    services.ingest_text("at\n" + ts, "arr", "csv")
    res = sc.calibrate_scenario(services, "arr", "at", "timestamps")
    assert res["sample_size"] == 35
    sched = res["arrivals"]["schedule"]["rates"]
    assert sched[9] == pytest.approx(3.0) and sched[3] == 0
    assert any("calendar days of data" in w for w in res["warnings"])


def test_persist_models_and_api_and_tool(client):
    svc = client.services
    m = mm(1, 4.0, 0.15, replications=4, horizon_hours=100, warmup_hours=10)
    assert client.post("/api/scenarios/models", json={"name": "Desk", "model": m}).json()["saved"]
    assert [x["name"] for x in client.get("/api/scenarios/models").json()["models"]] == ["Desk"]
    assert client.get("/api/scenarios/models/Desk").json()["model"]["seed"] == 11
    run = client.post("/api/scenarios/run", json={"name": "Desk"}).json()
    assert run["metrics"]["throughput"]["mean"] > 3 and run["log_id"]
    cmp_ = client.post("/api/scenarios/compare", json={"name": "Desk", "variants": [
        {"name": "two", "overrides": {"stations.S.servers": 2}}], "save_as": "cmp"}).json()
    assert cmp_["saved_dataset"] == "cmp"
    sw = client.post("/api/scenarios/sweep", json={"name": "Desk", "param": "arrivals.rate", "values": [2, 4]}).json()
    assert len(sw["points"]) == 2
    assert client.post("/api/scenarios/models", json={"name": "bad/name", "model": m}).status_code == 400
    assert client.post("/api/scenarios/run", json={"name": "Nope"}).status_code == 404
    assert client.get("/api/scenarios/template").json()["model"]["stations"]

    async def go():
        listed = await call_tool(svc, "data_scenario", {"action": "list"})
        ran = await call_tool(svc, "data_scenario", {"action": "run", "name": "Desk", "replications": 3})
        gone = await call_tool(svc, "data_scenario", {"action": "delete", "name": "Desk"})
        return listed, ran, gone
    listed, ran, gone = asyncio.run(go())
    assert listed is not None and ran is not None and gone is not None
    assert client.get("/api/scenarios/models").json()["count"] == 0

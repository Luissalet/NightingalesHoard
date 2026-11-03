"""Process mining on a synthetic log generated from a known process (rework loop, one deliberately slow step)."""
import asyncio
import heapq
import random
from collections import Counter, defaultdict
from datetime import datetime, timedelta, timezone

import pytest

from nightingale import process_mining as pm
from nightingale.agent_tools import call_tool

T0 = datetime(2026, 1, 5, 8, 0, 0, tzinfo=timezone.utc)
# activity -> (servers, mean service hours); Assess is the deliberately slow, single-server step
STATIONS = {"Register": (3, 0.10), "Check": (2, 0.20), "Assess": (1, 0.40), "Escalate": (1, 0.30),
            "Decide": (2, 0.15), "Notify": (2, 0.05)}
RATE = 1.4  # cases per hour
SKIP_CHECK, ESCALATE, REWORK = 0.10, 0.05, 0.25
MAPPING = {"case": "case_id", "activity": "activity", "timestamp": "end", "start": "start", "resource": "resource"}


def generate(n=500, seed=7):
    rng = random.Random(seed)
    free = {a: [0.0] * c for a, (c, _m) in STATIONS.items()}
    heap, seq, t = [], 0, 0.0
    for i in range(n):
        t += rng.expovariate(RATE)
        seq += 1
        heapq.heappush(heap, (t, seq, i, "Register"))
    rows = []
    while heap:
        arr, _s, case, act = heapq.heappop(heap)
        servers = free[act]
        k = min(range(len(servers)), key=lambda j: servers[j])
        start = max(arr, servers[k])
        end = start + rng.expovariate(1 / STATIONS[act][1])
        servers[k] = end
        rows.append((f"C{case:04d}", act, start, end, f"{act}-{k + 1}"))
        u = rng.random()
        nxt = None
        if act == "Register":
            nxt = "Assess" if u < SKIP_CHECK else "Check"
        elif act == "Check":
            nxt = "Assess"
        elif act == "Assess":
            nxt = "Escalate" if u < ESCALATE else "Decide"
        elif act == "Escalate":
            nxt = "Decide"
        elif act == "Decide":
            nxt = "Assess" if u < REWORK else "Notify"
        if nxt:
            seq += 1
            heapq.heappush(heap, (end, seq, case, nxt))
    return rows


def to_csv(rows, with_start=True):
    fmt = lambda h: (T0 + timedelta(hours=h)).strftime("%Y-%m-%d %H:%M:%S.%f")  # noqa: E731
    head = "case_id,activity,start,end,resource" if with_start else "case_id,activity,end,resource"
    random.Random(3).shuffle(rows := list(rows))  # file order must not matter
    body = [f"{c},{a},{fmt(s)},{fmt(e)},{r}" if with_start else f"{c},{a},{fmt(e)},{r}" for c, a, s, e, r in rows]
    return head + "\n" + "\n".join(body)


@pytest.fixture(scope="module")
def rows():
    return generate()


@pytest.fixture
def log(services, rows):
    services.ingest_text(to_csv(rows), "claims", "csv")
    return services


def scalar(svc, sql):
    row = svc.query(sql)["rows"][0]
    return list(row.values())[0] if isinstance(row, dict) else row[0]


def truth(rows):
    by = defaultdict(list)
    for c, a, s, e, _r in rows:
        by[c].append((e, a))
    traces = {c: tuple(a for _e, a in sorted(v)) for c, v in by.items()}
    return traces


def test_dfg_edges_and_counts_are_exact(log, rows):
    traces = truth(rows)
    res = pm.discover_process(log, "claims", MAPPING, max_variants=5)
    expected = Counter()
    for tr in traces.values():
        expected.update(zip(tr, tr[1:]))
    got = {(e["from"], e["to"]): e["count"] for e in res["graph"]["edges"]}
    assert got == dict(expected)
    assert res["cases"] == 500 and res["events"] == len(rows)
    assert {s["activity"]: s["count"] for s in res["start_activities"]} == {"Register": 500}
    assert {s["activity"]: s["count"] for s in res["end_activities"]} == {"Notify": 500}
    # the rework loop Decide -> Assess is kept by the loop rule although its direct dependency is negative
    kept = {(e["from"], e["to"]) for e in res["graph"]["edges"] if e["kept"]}
    assert ("Decide", "Assess") in kept and ("Assess", "Decide") in kept
    assert ("Check", "Assess") in kept
    assert any(l["activities"] == ["Assess", "Decide", "Assess"] for l in res["rework"]["loops"])
    assert res["log_id"]


def test_variants_share_and_throughput(log, rows):
    traces = truth(rows)
    res = pm.discover_process(log, "claims", MAPPING, max_variants=3)
    cnt = Counter(traces.values())
    top, n = cnt.most_common(1)[0]
    v = res["variants"][0]
    assert v["activities"] == list(top) and v["count"] == n and v["share"] == pytest.approx(n / 500)
    assert res["variants_count"] == len(cnt) and len(res["variants"]) == 3
    assert res["variants"][-1]["cumulative_share"] == pytest.approx(sum(c for _t, c in cnt.most_common(3)) / 500)
    by_case = defaultdict(list)
    for c, a, s, e, _r in rows:
        by_case[c].append((s, e))
    times = [max(e for _s, e in v) - min(s for s, _e in v) for v in by_case.values()]
    assert res["throughput"]["mean"] == pytest.approx(sum(times) / 500, rel=1e-6)
    assert res["throughput"]["p90"] >= res["throughput"]["median"]
    assert res["rework"]["share_of_cases"] == pytest.approx(
        sum(1 for t in traces.values() if len(set(t)) < len(t)) / 500)


def test_slow_step_is_the_bottleneck_and_resources(log):
    res = pm.discover_process(log, "claims", MAPPING)
    b = res["bottlenecks"][0]
    assert b["activity"] == "Assess" and b["wait_share"] > 0.5
    assert b["utilisation"] is not None and b["utilisation"] > 0.5
    assert "Assess" in res["bottleneck_verdict"]
    act = {a["activity"]: a for a in res["activities"]}
    assert act["Assess"]["resources"] == 1 and act["Register"]["resources"] == 3
    assert act["Assess"]["wait_total_hours"] > 5 * act["Check"]["wait_total_hours"]
    assert {r["resource"] for r in res["resources"]} >= {"Assess-1", "Register-3"}
    assert sum(r["events"] for r in res["resources"]) == res["events"]


def test_discover_can_save_tables(log):
    res = pm.discover_process(log, "claims", MAPPING, save_as="claims_map")
    assert set(res["saved_datasets"]) == {"edges", "variants", "cases"}
    assert scalar(log, "SELECT count(*) FROM claims_map_cases") == 500


def test_conformance_detects_skipped_inserted_repeated(log, rows):
    traces = truth(rows)
    ref = {"steps": ["Register", "Check", "Assess", "Decide", "Notify"]}
    res = pm.conform_process(log, "claims", MAPPING, ref, save_as="claims_conf")
    d = {(x["type"], x["detail"]): x for x in res["deviations"]}
    skipped = sum(1 for t in traces.values() if "Check" not in t)
    inserted = sum(1 for t in traces.values() if "Escalate" in t)
    repeated = sum(1 for t in traces.values() if t.count("Assess") > 1)
    assert d[("skipped", "Check")]["cases"] == skipped > 0
    assert d[("inserted", "Escalate")]["cases"] == inserted > 0
    assert d[("repeated", "Assess")]["cases"] == repeated > 0
    assert len(d[("skipped", "Check")]["examples"]) == 5
    fit = sum(1 for t in traces.values() if t == ("Register", "Check", "Assess", "Decide", "Notify"))
    assert res["fitting_cases"] == fit and res["fitting_share"] == pytest.approx(fit / 500)
    # an optional Check and the rework allowed through transitions: only the escalation is left
    lenient = pm.conform_log(*[pm.load_log(log, "claims", MAPPING)[0]],
                             reference={"steps": ["Register", {"activity": "Check", "optional": True},
                                                  "Assess", {"activity": "Escalate", "optional": True},
                                                  "Decide", "Notify"]})
    assert not any(x["type"] == "skipped" for x in lenient["deviations"])
    saved = scalar(log, "SELECT count(*) FROM claims_conf WHERE fits")
    assert saved == fit


def mk(**cases):
    return [(cid, [(float(i), a, None, None, None) for i, a in enumerate(tr.split(">"))])
            for cid, tr in cases.items()]


def test_conformance_parallel_optional_and_reordered():
    ref = {"steps": ["A", {"parallel": ["B", "C"]}, {"activity": "D", "optional": True}, "E"]}
    cases = mk(ok1="A>B>C>E", ok2="A>C>B>E", ok3="A>B>C>D>E", skip="A>B>E", swap="A>D>B>C>E", extra="A>B>C>X>E",
               rep="A>B>B>C>E")
    res = pm.conform_log(cases, ref)
    dev = {(x["type"], x["detail"]): x["cases"] for x in res["deviations"]}
    assert res["fitting_cases"] == 3
    assert dev[("skipped", "C")] == 1 and dev[("inserted", "X")] == 1 and dev[("repeated", "B")] == 1
    assert dev[("reordered", "D before B (expected B first)")] == 1
    assert dev[("reordered", "D before C (expected C first)")] == 1
    with pytest.raises(pm.ProcessError):
        pm.conform_log(cases, {"steps": ["A", "A"]})
    with pytest.raises(pm.ProcessError):
        pm.conform_log(cases, {})


def test_conformance_with_transitions_classifies_deviations():
    ref = {"transitions": [["A", "B"], ["B", "C"], ["C", "D"], ["B", "D"]], "start": ["A"], "end": ["D"]}
    res = pm.conform_log(mk(ok="A>B>C>D", ok2="A>B>D", skip="A>C>D", swap="A>C>B>D", ins="A>B>Z>C>D", bad="B>C>D>A"), ref)
    dev = {(x["type"], x["detail"]) for x in res["deviations"]}
    assert res["fitting_cases"] == 2
    assert ("skipped", "between A and C: B") in dev
    assert ("reordered", "A then C (expected C then A)") not in dev
    assert ("reordered", "C then B (expected B then C)") in dev
    assert ("inserted", "Z") in dev
    assert ("wrong start", "B") in dev and ("wrong end", "A") in dev


def test_baseline_validates_and_a_resource_at_the_bottleneck_helps(log):
    res = pm.rehearse_process(log, "claims", MAPPING, [
        {"name": "second assessor", "changes": [{"type": "add_resource", "activity": "Assess", "count": 1}]},
        {"name": "more registrars", "changes": [{"type": "add_resource", "activity": "Register", "count": 1}]},
        {"name": "less rework", "changes": [{"type": "set_routing", "from": "Decide", "to": "Assess", "share": 0.05}]},
        {"name": "no check", "changes": [{"type": "remove_step", "activity": "Check"}]},
        {"name": "faster assess", "changes": [{"type": "scale_duration", "activity": "Assess", "factor": 0.7}]},
    ], replications=12, seed=5, save_as="claims_reh", model_name="Claims process")
    v = res["validation"]
    assert v["representative"], v
    mean_row = v["rows"][0]
    assert mean_row["relative_difference"] < 0.15
    by = {x["name"]: x["headline"] for x in res["variants"]}
    b = by["second assessor"]
    assert b["distinguishable"] and b["better"] == "variant" and b["delta"] < 0
    assert b["delta_pct"] < -20 and b["ci_high"] < 0
    assert abs(b["delta"]) > 3 * abs(by["more registrars"]["delta"])
    for name in ("less rework", "no check", "faster assess"):
        assert by[name]["distinguishable"] and by[name]["delta"] < 0, name
    assert any(r["metric"] == "wait:Assess" for r in res["variants"][0]["rows"])
    assert res["scenario_model"] == "Claims process"
    assert scalar(log, "SELECT count(*) FROM claims_reh") > 10
    from nightingale import scenarios as sc
    again = sc.run_scenario(log, name="Claims process", replications=3)
    assert again["metrics"]["throughput"]["mean"] > 1


def test_not_representative_is_reported_and_capacity_needs_a_start_column(services, rows):
    services.ingest_text(to_csv(rows), "claims", "csv")
    harsh = pm.rehearse_process(services, "claims", MAPPING, [], replications=6, tolerance=0.0005)
    assert harsh["validation"]["representative"] is False
    assert "NOT reproduce" in harsh["warnings"][0]
    services.ingest_text(to_csv(rows, with_start=False), "claims_end", "csv")
    m = {"case": "case_id", "activity": "activity", "timestamp": "end"}
    disc = pm.discover_process(services, "claims_end", m)
    assert disc["wait_includes_work"] and any("No start column" in w for w in disc["warnings"])
    assert disc["bottlenecks"][0]["activity"] == "Assess"
    with pytest.raises(pm.ProcessError, match="not modelled"):
        pm.rehearse_process(services, "claims_end", m, [
            {"name": "x", "changes": [{"type": "add_resource", "activity": "Assess"}]}], replications=3)
    ok = pm.rehearse_process(services, "claims_end", m, [
        {"name": "shorter", "changes": [{"type": "scale_duration", "activity": "Assess", "factor": 0.5}]}],
        replications=6)
    assert ok["variants"][0]["headline"]["delta"] < 0


def test_remove_step_keeps_probabilities_and_changes_validate():
    model = {"stations": [{"name": n, "servers": 1, "service": {"dist": "empirical", "values": [1.0]}}
                          for n in ("A", "B", "C")],
             "routing": {"start": {"A": 1.0}, "next": {"A": {"B": 0.6, "C": 0.4}, "B": {"B": 0.5, "C": 0.5},
                                                       "C": {"END": 1.0}}}}
    import copy
    m = copy.deepcopy(model)
    pm.apply_change(m, {"type": "remove_step", "activity": "B"})
    assert m["routing"]["next"]["A"] == pytest.approx({"C": 1.0})
    assert "B" not in m["routing"]["next"]
    m = copy.deepcopy(model)
    pm.apply_change(m, {"type": "set_routing", "from": "A", "to": "B", "share": 0.9})
    assert m["routing"]["next"]["A"] == pytest.approx({"C": 0.1, "B": 0.9})
    for bad in ({"type": "nope"}, {"type": "add_resource", "activity": "Z"}, {"type": "scale_duration",
                "activity": "A", "factor": 0}, {"type": "set_routing", "from": "A", "to": "B", "share": 2}):
        with pytest.raises(pm.ProcessError):
            pm.apply_change(copy.deepcopy(model), bad)


def test_scenario_engine_routing_network_matches_theory():
    # one station, Markov rework with probability 0.3: the job visits it 1/(1-0.3) times on average
    from nightingale import scenarios as sc
    m = {"horizon_hours": 800, "warmup_hours": 50, "replications": 8, "seed": 3,
         "arrivals": {"type": "poisson", "rate": 0.5},
         "stations": [{"name": "S", "servers": 20, "service": {"dist": "fixed", "value": 1.0}}],
         "routing": {"start": {"S": 1}, "next": {"S": {"S": 0.3, "END": 0.7}}, "max_visits": 12}}
    r = sc.run_model(m)
    assert r["metrics"]["lead_time_mean"]["mean"] == pytest.approx(1 / 0.7, rel=0.05)
    with pytest.raises(sc.ScenarioError):
        sc.run_model({**m, "routing": {"start": {"Nope": 1}}})


def test_mapping_suggestion_and_errors(log):
    s = pm.suggest_mapping(log, "claims")
    assert s["mapping"] == {"timestamp": "end", "case": "case_id", "activity": "activity", "start": "start",
                            "resource": "resource"}
    with pytest.raises(pm.ProcessError, match="required"):
        pm.discover_process(log, "claims", {"case": "case_id"})
    with pytest.raises(pm.ProcessError, match="not found"):
        pm.discover_process(log, "claims", {**MAPPING, "activity": "nope"})
    with pytest.raises(pm.ProcessError, match="different columns"):
        pm.discover_process(log, "claims", {**MAPPING, "start": "end"})


def test_api_and_agent_tool(client, rows):
    svc = client.services
    svc.ingest_text(to_csv(rows), "claims", "csv")
    sug = client.post("/api/process/suggest", json={"dataset": "claims"}).json()
    assert sug["mapping"]["case"] == "case_id"
    d = client.post("/api/process/discover", json={"dataset": "claims", "mapping": MAPPING}).json()
    assert d["bottlenecks"][0]["activity"] == "Assess"
    c = client.post("/api/process/conform", json={"dataset": "claims", "mapping": MAPPING,
                                                  "reference": {"steps": ["Register", "Check", "Assess"]}}).json()
    assert c["deviating_cases"] > 0
    r = client.post("/api/process/rehearse", json={"dataset": "claims", "mapping": MAPPING, "replications": 4,
                    "variants": [{"name": "v", "changes": [{"type": "add_resource", "activity": "Assess"}]}]})
    assert r.status_code == 200 and r.json()["variants"][0]["headline"]["delta"] < 0
    assert client.post("/api/process/discover", json={"dataset": "nope", "mapping": MAPPING}).status_code == 404
    assert client.post("/api/process/discover", json={"dataset": "claims", "mapping": {"case": "case_id"}}).status_code == 400
    ops = [e["op"] for e in client.get("/api/log").json()["entries"]] if client.get("/api/log").status_code == 200 else []
    assert not ops or {"process_discover", "process_conform", "process_rehearse"} <= set(ops)

    async def go():
        a = await call_tool(svc, "data_process", {"action": "suggest", "dataset": "claims"})
        b = await call_tool(svc, "data_process", {"action": "discover", "dataset": "claims", "mapping": MAPPING})
        return a, b
    a, b = asyncio.run(go())
    assert a is not None and b is not None

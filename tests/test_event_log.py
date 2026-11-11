"""Event-log mapping, checks, profile and holdout calibration on small invented logs.

Every expected number is computed by hand in the test (literal hours, numpy percentiles), independently of the
module's own statistics."""
import asyncio
import json
import sys

import numpy as np
import pytest

import evlog_gen as gen
from nightingale import event_log as el
from nightingale import process_mining as pm
from nightingale import scenarios as sc
from nightingale.agent_tools import call_tool_sync

SEQ = """pedido,actividad,fecha
P1,Recepción,2026-02-02 09:00:00
P1,Revisión,2026-02-02 10:00:00
P1,Impresión,2026-02-02 13:00:00
P1,Entrega,2026-02-02 15:00:00
P2,Recepción,2026-02-02 09:30:00
P2,Revisión,2026-02-02 11:30:00
P2,Impresión,2026-02-02 12:30:00
P2,Entrega,2026-02-02 16:30:00
P3,Recepción,2026-02-02 10:00:00
P3,Revisión,2026-02-02 10:30:00
P3,Impresión,2026-02-02 14:00:00
P3,Entrega,2026-02-02 15:00:00
"""
SKIPS = """P4,Recepción,2026-02-03 09:00:00
P4,Impresión,2026-02-03 11:00:00
P4,Entrega,2026-02-03 12:00:00
P5,Recepción,2026-02-03 10:00:00
P5,Impresión,2026-02-03 10:30:00
P5,Entrega,2026-02-03 13:30:00
"""
M = {"case": "pedido", "activity": "actividad", "timestamp": "fecha"}
STEPS = ["Recepción", "Revisión", "Impresión", "Entrega"]


def q(xs, p):
    return float(np.percentile(np.array(xs, dtype=float), p))


def acts(prof):
    return {a["activity"]: a for a in prof["times"]["activities"]}


def test_sequential_completion_only_matches_hand_computation(services):
    services.ingest_text(SEQ, "pedidos", "csv")
    prof = el.profile_process(services, "pedidos", M)
    assert prof["cases"] == {"total": 3, "closed": 3, "open": 0}
    assert prof["variants_count"] == 1 and prof["variants"][0]["activities"] == STEPS
    edges = {(e["from"], e["to"]): e for e in prof["graph"]["edges"]}
    assert {k: e["count"] for k, e in edges.items()} == {("Recepción", "Revisión"): 3, ("Revisión", "Impresión"): 3,
                                                         ("Impresión", "Entrega"): 3}
    assert prof["graph"]["coverage"]["share_of_cases"] == 1.0
    # hand: elapsed between completions (hours)
    hand = {"Revisión": [1.0, 2.0, 0.5], "Impresión": [3.0, 1.0, 3.5], "Entrega": [2.0, 4.0, 1.0]}
    a = acts(prof)
    for name, xs in hand.items():
        st = a[name]["elapsed_since_previous_completion_hours"]
        assert st["n"] == 3 and st["mean"] == pytest.approx(sum(xs) / 3)
        assert st["p50"] == pytest.approx(q(xs, 50)) and st["p90"] == pytest.approx(q(xs, 90))
        assert a[name]["identifiable"] == "elapsed_only" and a[name]["service_hours"] is None
        assert "start" in a[name]["missing_data"]
    cyc = [6.0, 7.0, 5.0]
    ct = prof["times"]["cycle_time_hours"]
    assert ct["n"] == 3 and ct["mean"] == pytest.approx(6.0) and ct["p90"] == pytest.approx(q(cyc, 90))
    assert ct["p25"] == pytest.approx(q(cyc, 25)) and ct["p75"] == pytest.approx(q(cyc, 75))
    b = prof["bottlenecks"][0]
    assert b["activity"] == "Impresión" and b["measure"] == "elapsed"
    assert b["total_hours"] == pytest.approx(7.5) and b["share"] == pytest.approx(7.5 / 18)
    assert "not proven to be waiting" in prof["explanation"][0]
    assert prof["times"]["service_separable"] is False
    assert any("start times" in m for m in prof["missing_data"])
    assert any("which activities close a case" in m for m in prof["missing_data"])
    assert prof["log"]["proposed_end_activities"] == ["Entrega"] and not prof["log"]["end_declared"]
    assert "does not prove" in prof["caveat"]


def test_two_variants_shares_and_coverage(services):
    services.ingest_text(SEQ + SKIPS, "pedidos", "csv")
    prof = el.profile_process(services, "pedidos", M)
    v = {tuple(x["activities"]): x for x in prof["variants"]}
    assert v[tuple(STEPS)]["count"] == 3 and v[tuple(STEPS)]["share"] == pytest.approx(0.6)
    skip = ("Recepción", "Impresión", "Entrega")
    assert v[skip]["count"] == 2 and v[skip]["share"] == pytest.approx(0.4)
    assert prof["graph"]["coverage"]["share_of_transitions"] == 1.0
    strict = el.profile_process(services, "pedidos", M, threshold=0.7)
    cov = strict["graph"]["coverage"]
    # dependency(Recepción -> Impresión) = 2 / (2 + 1) < 0.7: dropped; 11 of 13 transitions and 3 of 5 cases remain
    assert cov["transitions"] == 13 and cov["transitions_in_kept_edges"] == 11
    assert cov["cases_fully_covered"] == 3 and cov["share_of_cases"] == pytest.approx(0.6)
    # the expected sequence: the two repeat orders skip the review, listed with examples and a reason
    exp = el.profile_process(services, "pedidos", M, {"expected": STEPS})
    d = {(x["type"], x["detail"]): x for x in exp["deviations"]}
    assert d[("skipped", "Revisión")]["count"] == 2 and d[("skipped", "Revisión")]["examples"] == ["P4", "P5"]
    assert d[("skipped", "Revisión")]["why"]
    assert exp["log"]["missing_steps"] == [{"activity": "Revisión", "cases": 2, "examples": ["P4", "P5"]}]


CONC = """pedido,actividad,momento,ciclo,operario
C1,Recepción,2026-02-02 08:00:00,start,Ana
C1,Recepción,2026-02-02 08:10:00,complete,Ana
C1,Revisión,2026-02-02 08:30:00,start,Luz
C1,Revisión,2026-02-02 09:00:00,complete,Luz
C1,Impresión,2026-02-02 09:30:00,start,Imp-1
C1,Impresión,2026-02-02 11:00:00,complete,Imp-1
C1,Corte,2026-02-02 10:00:00,start,Gui
C1,Corte,2026-02-02 10:30:00,complete,Gui
C1,Entrega,2026-02-02 11:30:00,start,Rep
C1,Entrega,2026-02-02 12:00:00,complete,Rep
C2,Recepción,2026-02-02 08:00:00,start,Ana
C2,Recepción,2026-02-02 08:05:00,complete,Ana
C2,Revisión,2026-02-02 08:20:00,start,Luz
C2,Revisión,2026-02-02 08:50:00,complete,Luz
C2,Impresión,2026-02-02 10:00:00,start,Imp-1
C2,Impresión,2026-02-02 11:00:00,complete,Imp-1
C2,Corte,2026-02-02 11:00:00,start,Gui
C2,Corte,2026-02-02 11:30:00,complete,Gui
C2,Entrega,2026-02-02 12:00:00,start,Rep
C2,Entrega,2026-02-02 12:30:00,complete,Rep
C3,Recepción,2026-02-02 09:00:00,start,Ana
C3,Recepción,2026-02-02 09:10:00,complete,Ana
C3,Revisión,2026-02-02 09:20:00,start,Luz
"""
LM = {"case": "pedido", "activity": "actividad", "timestamp": "momento", "lifecycle": "ciclo", "resource": "operario"}


def test_concurrency_with_intervals_separates_service_and_waiting(services):
    services.ingest_text(CONC, "conc", "csv")
    prof = el.profile_process(services, "conc", LM)
    # C3 has a review that started and never completed: in progress, so open even without declared end activities
    assert prof["cases"] == {"total": 3, "closed": 2, "open": 1}
    assert prof["log"]["start_without_complete"]["items"][0]["case"] == "C3"
    assert prof["log"]["open_cases"]["items"][0]["in_progress"] is True
    a = acts(prof)
    imp = a["Impresión"]
    assert imp["identifiable"] == "service_and_waiting"
    assert imp["service_hours"]["n"] == 2 and imp["service_hours"]["mean"] == pytest.approx((1.5 + 1.0) / 2)
    # waiting = start - latest completion before it: C1 09:30-09:00, C2 10:00-08:50
    assert imp["waiting_hours"]["mean"] == pytest.approx((0.5 + 70 / 60) / 2)
    assert a["Corte"]["waiting_hours"]["mean"] == pytest.approx((1.0 + 0.0) / 2)
    assert a["Entrega"]["waiting_hours"]["mean"] == pytest.approx(0.5)
    # Corte runs inside Impresión in C1 only (in C2 it starts exactly when printing ends: not an overlap)
    assert prof["concurrency"] == [{"pair": ["Corte", "Impresión"], "occurrences": 1, "cases": 1,
                                    "share_of_cases": 0.5, "examples": ["C1"]}]
    edges = {(e["from"], e["to"]): e for e in prof["graph"]["edges"]}
    assert edges[("Corte", "Impresión")]["count"] == 1 and edges[("Corte", "Impresión")]["overlapping"] == 1
    assert edges[("Impresión", "Corte")]["overlapping"] == 0
    assert any("overlap in time" in x for x in prof["explanation"])
    assert prof["times"]["service_separable"] is True
    # cycle times of the closed cases only: 4h and 4.5h
    assert prof["times"]["cycle_time_hours"]["n"] == 2
    assert prof["times"]["cycle_time_hours"]["mean"] == pytest.approx(4.25)


DUP = SEQ + "P1,Revisión,2026-02-02 10:00:00\n"
BAD = SEQ + """P1,Revisión,no-date
P2,Entrega,
P3,Entrega,2026-02-30 10:00:00
P9,Recepción,2026-03-29 02:30:00
,Recepción,2026-02-02 09:00:00
"""


def test_duplicates_are_removed_and_listed_with_identical_results(services):
    services.ingest_text(SEQ, "a", "csv")
    services.ingest_text(DUP, "b", "csv")
    pa, pb = el.profile_process(services, "a", M), el.profile_process(services, "b", M)
    assert pb["log"]["duplicates"]["total"] == 1
    assert pb["log"]["duplicates"]["items"][0]["row"] == 13 and pb["log"]["duplicates"]["items"][0]["duplicate_of"] == 2
    assert pa["times"] == pb["times"] and pa["graph"]["edges"] == pb["graph"]["edges"]
    assert any(d["type"] == "duplicate" and d["examples"] == ["row 13 = row 2"] for d in pb["deviations"])
    disc = pm.discover_process(services, "b", M)          # the existing discovery uses the same checked log
    assert disc["events"] == 12 and disc["quality"]["checks"]["duplicates_removed"] == 1
    assert any("duplicate" in w for w in disc["warnings"])


def test_invalid_and_nonexistent_timestamps_are_excluded_and_listed(services):
    services.ingest_text(BAD, "bad", "csv")
    prof = el.profile_process(services, "bad", M, {"timezone": "Europe/Madrid"})
    ex = prof["log"]["excluded"]
    inv = {r["row"]: r for r in ex["invalid_timestamp"]["items"]}
    assert ex["invalid_timestamp"]["total"] == 4
    assert inv[13]["value"] == "no-date" and inv[13]["reason"] == "unparseable"
    assert inv[14]["reason"] == "missing"
    assert inv[15]["value"] == "2026-02-30 10:00:00"
    assert inv[16]["reason"] == "nonexistent"              # 02:30 does not exist in Madrid on 29 March 2026
    assert ex["missing_case_or_activity"]["total"] == 1 and ex["missing_case_or_activity"]["items"][0]["row"] == 17
    assert prof["cases"]["total"] == 3                      # P9 only had the impossible time
    assert any(d["type"] == "invalid timestamp" and d["count"] == 4 for d in prof["deviations"])


def test_incomplete_cases_are_open_and_kept_out_of_times(services):
    open_case = "P6,Recepción,2026-02-04 09:00:00\nP6,Revisión,2026-02-04 10:00:00\n"
    services.ingest_text(SEQ + SKIPS + open_case, "inc", "csv")
    prof = el.profile_process(services, "inc", M, {"end_activities": ["Entrega"]})
    assert prof["cases"] == {"total": 6, "closed": 5, "open": 1}
    assert prof["log"]["open_cases"]["items"][0] == {"case": "P6", "last_activity": "Revisión",
                                                     "last_at": "2026-02-04T10:00:00Z", "in_progress": False,
                                                     "events": 2}
    assert prof["times"]["cycle_time_hours"]["n"] == 5
    assert prof["times"]["cycle_time_hours"]["mean"] == pytest.approx((6 + 7 + 5 + 3 + 3.5) / 5)
    assert any("open (censored)" in w for w in prof["warnings"])
    assert sum(v["count"] for v in prof["variants"]) == 5


ZONES = """pedido,actividad,fecha
Z1,Recepción,2026-03-28 23:00:00+01:00
Z1,Entrega,2026-03-29 04:00:00+02:00
Z2,Recepción,2026-03-29 01:00:00
Z2,Entrega,2026-03-29 03:30:00
Z3,Recepción,2026-03-29T08:00:00Z
Z3,Entrega,2026-03-29T12:00:00+02:00
Z4,Recepción,2026-10-25 01:30:00
Z4,Entrega,2026-10-25 02:30:00
"""


def test_zone_changes_are_normalised_to_utc_and_reported(services):
    # read as typed, the importer turns the column into instants and the written offsets are gone: said, not hidden
    services.ingest_text(ZONES, "zonas_tipadas", "csv")
    typed = el.profile_process(services, "zonas_tipadas", M, {"timezone": "Europe/Madrid"})
    assert typed["log"]["timezone"]["offsets"] == {"ingest": 8} and "text_columns" in typed["log"]["timezone"]["note"]
    services.ingest_text(ZONES, "zonas", "csv", {"text_columns": ["fecha"]})
    prof = el.profile_process(services, "zonas", M, {"timezone": "Europe/Madrid"})
    tz = prof["log"]["timezone"]
    assert tz["declared"] == "Europe/Madrid" and tz["normalised_to"] == "UTC" and tz["mixed_offsets"]
    assert tz["offsets"] == {"+01:00": 1, "+02:00": 2, "+00:00": 1, "naive": 4}
    assert [z["case"] for z in tz["zone_changes"]["items"]] == ["Z1", "Z3"]
    assert tz["ambiguous_local_times"]["items"][0]["case"] == "Z4"   # 02:30 happens twice on 25 October
    # hand: Z1 22:00Z -> 02:00Z = 4 h; Z2 00:00Z -> 01:30Z = 1.5 h; Z3 08:00Z -> 10:00Z = 2 h; Z4 23:30Z -> 00:30Z = 1 h
    xs = acts(prof)["Entrega"]["elapsed_since_previous_completion_hours"]
    assert xs["n"] == 4 and xs["mean"] == pytest.approx((4 + 1.5 + 2 + 1) / 4)
    assert xs["max"] == pytest.approx(4.0) and xs["min"] == pytest.approx(1.0)
    utc = el.profile_process(services, "zonas", M)             # read as UTC, naive times shift
    assert acts(utc)["Entrega"]["elapsed_since_previous_completion_hours"]["mean"] == pytest.approx(
        (4 + 2.5 + 2 + 1) / 4)
    with pytest.raises(pm.ProcessError, match="not a known zone"):
        el.profile_process(services, "zonas", M, {"timezone": "Mars/Base"})


def test_ties_are_flagged_and_their_edges_counted(services):
    services.ingest_text(SEQ + """T1,Recepción,2026-02-05 09:00:00
T1,Revisión,2026-02-05 10:00:00
T1,Impresión,2026-02-05 10:00:00
T1,Entrega,2026-02-05 11:00:00
""", "empates", "csv")
    prof = el.profile_process(services, "empates", M)
    assert prof["log"]["ties"]["items"] == [{"case": "T1", "at": "2026-02-05T10:00:00Z",
                                             "activities": ["Revisión", "Impresión"], "rows": [14, 15]}]
    e = {(x["from"], x["to"]): x for x in prof["graph"]["edges"]}
    assert e[("Revisión", "Impresión")]["count"] == 4 and e[("Revisión", "Impresión")]["tie_dependent"] == 1
    assert any("same moment" in w for w in prof["warnings"])


def test_mapping_errors(services):
    services.ingest_text(CONC, "conc", "csv")
    with pytest.raises(pm.ProcessError, match="not both"):
        el.profile_process(services, "conc", {**LM, "start": "operario"})
    with pytest.raises(pm.ProcessError, match="not found"):
        el.profile_process(services, "conc", {**LM, "lifecycle": "nope"})
    services.ingest_text(CONC.replace(",start,", ",begin-ish,", 1), "conc2", "csv")
    prof = el.profile_process(services, "conc2", LM)
    assert prof["log"]["excluded"]["unknown_lifecycle"]["items"][0]["value"] == "begin-ish"
    assert prof["log"]["complete_without_start"]["total"] == 1


# ------------------------------------------------------------------------------------- calibration
@pytest.fixture
def shop(services):
    services.ingest_text(gen.lifecycle_csv(gen.generate(seed=13)), "taller", "csv")
    return services


def test_holdout_calibration_passes_and_a_second_printer_is_compared_with_crn(shop):
    ref_before = el.dataset_reference(shop, "taller")
    alt = [{"name": "segunda impresora", "changes": [{"type": "add_resource", "activity": "Impresión", "count": 1}]},
           {"name": "otro operario de revisión", "changes": [{"type": "add_resource", "activity": "Revisión"}]}]
    res = el.calibrate_process(shop, "taller", gen.MAPPING, gen.OPTIONS, holdout=0.3, replications=10, seed=4,
                               variants=alt, model_name="Taller calibrado")
    v = res["validation"]
    assert v["passed"] is True, v
    assert {r["metric"] for r in v["rows"]} == set(el.DEFAULT_TOLERANCES)
    assert all(r["ok"] for r in v["rows"])
    assert res["bottleneck"] == "Impresión"
    assert res["split"]["fit_cases"] == 630 and res["split"]["holdout_cases"] == 270
    # the fit window ends before the held-out window starts
    assert res["split"]["fit_window"]["to"] < res["split"]["holdout_window"]["from"]
    # every parameter carries its provenance; the printer's capacity comes from the one machine seen
    p = {x["parameter"]: x for x in res["parameters"]}
    assert p["servers:Impresión"]["value"] == 1 and p["servers:Impresión"]["identifiable"]
    assert p["servers:Impresión"]["source"]["resources"] == ["Imp-1"]
    assert p["service:Impresión"]["n"] == 630 and "end − start" in p["service:Impresión"]["estimator"]
    assert p["routing:Recepción"]["n"] == 630 and set(p["routing:Recepción"]["value"]) == {"Revisión", "Impresión"}
    assert p["arrivals"]["source"]["cases"] == 630 and res["not_identifiable"] == []
    by = {a["name"]: a["headline"] for a in res["alternatives"]}
    two = by["segunda impresora"]
    assert two["distinguishable"] and two["better"] == "variant" and two["ci_high"] < 0
    assert abs(two["delta"]) > 5 * abs(by["otro operario de revisión"]["delta"])
    # common random numbers: the same seed and replications give the same baseline every time
    again = el.calibrate_process(shop, "taller", gen.MAPPING, gen.OPTIONS, holdout=0.3, replications=10, seed=4)
    assert again["baseline"]["metrics"] == res["baseline"]["metrics"]
    assert res["scenario_model"] == "Taller calibrado"
    stored = sc.get_model(shop, "Taller calibrado")
    assert stored["model"]["provenance"]["validation_passed"] is True
    assert stored["model"]["provenance"]["fit_window"]["cases"] == 630
    # the dataset was only read
    assert el.dataset_reference(shop, "taller") == ref_before
    assert shop.list_datasets()["datasets"][0]["version_count"] == 1


def test_holdout_calibration_failure_is_shown(services):
    rows = gen.generate(seed=13, drift_after=630, drift_rate=1.6)      # demand jumps in the held-out period
    services.ingest_text(gen.lifecycle_csv(rows), "deriva", "csv")
    res = el.calibrate_process(services, "deriva", gen.MAPPING, gen.OPTIONS, holdout=0.3, replications=8, seed=4,
                               variants=[{"name": "segunda impresora",
                                          "changes": [{"type": "add_resource", "activity": "Impresión"}]}])
    v = res["validation"]
    assert v["passed"] is False and v["verdict"].startswith("Calibration FAILED")
    rate = next(r for r in v["rows"] if r["metric"] == "cases_per_hour")
    assert rate["ok"] is False and rate["holdout_observed"] > 1.4 * rate["fit_observed"]
    assert res["alternatives"][0]["warnings"][0].startswith("The baseline failed")
    es = call_tool_sync(services, "data_process", {"action": "calibrate", "dataset": "deriva", "mapping": gen.MAPPING,
                                                   "log_options": gen.OPTIONS, "replications": 4, "lang": "es"})
    assert es["validation"]["verdict"].startswith("La calibración HA FALLADO")


def test_calibration_without_start_times_declares_what_is_not_identifiable(services):
    rows = gen.generate(n=300, seed=13)
    csv = "pedido,actividad,fecha\n" + "\n".join(
        f"{c},{a},{(gen.T0 + gen.timedelta(hours=e)).strftime('%Y-%m-%d %H:%M:%S')}" for c, a, _s, e, _r in rows)
    services.ingest_text(csv, "solo_fin", "csv")
    res = el.calibrate_process(services, "solo_fin", M, {"end_activities": ["Entrega"]}, replications=4)
    assert "servers:Impresión" in res["not_identifiable"]
    p = {x["parameter"]: x for x in res["parameters"]}
    assert "start" in p["servers:Impresión"]["missing_data"] and "waiting plus work" in p["service:Impresión"][
        "estimator"]
    w = next(r for r in res["validation"]["rows"] if r["metric"] == "bottleneck_wait_mean_hours")
    assert w["ok"] is None and "not identifiable" in w["note"]


def test_split_and_tolerance_errors(shop):
    with pytest.raises(pm.ProcessError, match="holdout"):
        el.calibrate_process(shop, "taller", gen.MAPPING, gen.OPTIONS, holdout=0.95)
    with pytest.raises(pm.ProcessError, match="unknown tolerance"):
        el.calibrate_process(shop, "taller", gen.MAPPING, gen.OPTIONS, tolerances={"nope": 0.1})
    with pytest.raises(pm.ProcessError, match="after the split"):
        el.calibrate_process(shop, "taller", gen.MAPPING, gen.OPTIONS, split_at="2027-01-01T00:00:00Z")


# ------------------------------------------------------------------------------------- export and surfaces
def test_export_recipe_references_the_dataset_and_reruns(shop):
    out = el.export_process(shop, "taller", gen.MAPPING, gen.OPTIONS, with_calibration=True, replications=4)
    r = out["recipe"]
    assert r["kind"] == el.RECIPE_KIND and r["dataset"]["name"] == "taller" and r["dataset"]["version"] == 0
    assert r["dataset"]["row_count"] == shop.profile("taller")["row_count"] and ":" in r["dataset"]["fingerprint"]
    assert r["mapping"] == gen.MAPPING and r["log_options"]["end_activities"] == ["Entrega"]
    assert r["calibration"]["replications"] == 4 and r["validation"]["rows"]
    assert r["model_proposal"]["parameters"] and r["profile"]["checks"]["duplicates"] == 0
    text = json.dumps(r, ensure_ascii=False)
    assert "Imp-1" in text and "2026-01-05 08" not in text          # references and estimates, not raw rows
    again = el.profile_process(shop, None, None, recipe=text)
    assert again["recipe_check"]["same_content"] is True
    shop.transform_apply("taller", "filter", {"expr": "pedido <> 'P0001'"})   # a new version: the data change
    changed = el.profile_process(shop, None, None, recipe=r)
    assert changed["recipe_check"]["same_content"] is False and "differ" in changed["warnings"][0]
    with pytest.raises(pm.ProcessError, match="not a process recipe"):
        el.profile_process(shop, None, None, recipe={"kind": "x"})


def test_rest_agent_tool_and_language(client):
    svc = client.services
    svc.ingest_text(SEQ + SKIPS, "pedidos", "csv")
    body = {"dataset": "pedidos", "mapping": M, "log_options": {"expected": STEPS}}
    p = client.post("/api/process/profile", json=body).json()
    assert p["cases"]["closed"] == 5 and p["recipe"]["dataset"]["name"] == "pedidos"
    es = client.post("/api/process/profile?lang=es", json=body).json()
    assert es["caveat"].startswith("Resultados observados")
    ex = client.post("/api/process/export", json=body).json()
    assert ex["recipe"]["kind"] == el.RECIPE_KIND and ex["filename"] == "pedidos.process-recipe.json"
    bad = client.post("/api/process/profile", json={**body, "log_options": {"timezone": "Nope/Zone"}})
    assert bad.status_code == 400
    svc.ingest_text(gen.lifecycle_csv(gen.generate(n=200, seed=13)), "taller", "csv")
    c = client.post("/api/process/calibrate", json={"dataset": "taller", "mapping": gen.MAPPING,
                                                    "log_options": gen.OPTIONS, "replications": 4, "holdout": 0.3,
                                                    "variants": [{"name": "x", "changes": [
                                                        {"type": "add_resource", "activity": "Impresión"}]}]})
    assert c.status_code == 200 and c.json()["alternatives"][0]["headline"]["ci_low"] is not None
    t = call_tool_sync(svc, "data_process", {"action": "profile", "dataset": "pedidos", "mapping": M})
    assert t["variants_count"] == 2
    e = call_tool_sync(svc, "data_process", {"action": "export", "dataset": "pedidos", "mapping": M})
    assert e["recipe"]["dataset"]["fingerprint"]
    ops = [x["op"] for x in client.get("/api/log").json()["entries"]]
    assert {"process_profile", "process_calibrate", "process_export"} <= set(ops)


def test_mcp_stdio_bridge_profiles_an_event_log(tmp_path):
    from test_e2e import ROOT, wait_health  # noqa: F401  (shared boot helpers)
    import os
    import subprocess

    import httpx

    from nightingale.hoard_link.net import free_port
    port = free_port()
    data_dir = tmp_path / "data"
    env = {**os.environ, "NIGHTINGALE_DATA_DIR": str(data_dir), "NIGHTINGALE_PORT": str(port), "PORT_STRICT": "1",
           "PYTHONUNBUFFERED": "1"}
    proc = subprocess.Popen([sys.executable, "-m", "nightingale"], cwd=ROOT, env=env, stdout=subprocess.DEVNULL,
                            stderr=subprocess.DEVNULL)
    url = f"http://127.0.0.1:{port}"
    try:
        wait_health(url, proc)
        assert httpx.put(f"{url}/api/modules/data", json={"enabled": True}, timeout=60).status_code == 200
        csv_path = tmp_path / "pedidos.csv"
        csv_path.write_text(SEQ + SKIPS, encoding="utf-8")

        async def go():
            from mcp.client.session import ClientSession
            from mcp.client.stdio import StdioServerParameters, stdio_client
            params = StdioServerParameters(command=sys.executable, args=[str(ROOT / "mcp_server.py")],
                                           env={**env, "NIGHTINGALE_URL": url,
                                                "NIGHTINGALE_TOKEN_FILE": str(data_dir / "mcp-token")})
            async with stdio_client(params) as (read, write):
                async with ClientSession(read, write) as session:
                    await session.initialize()
                    ing = await session.call_tool("data_ingest", {"kind": "file", "path": str(csv_path),
                                                                  "name": "pedidos"})
                    assert not ing.isError
                    out = await session.call_tool("data_process", {
                        "action": "profile", "dataset": "pedidos", "mapping": M,
                        "log_options": {"expected": STEPS}})
                    assert not out.isError, out.content[0].text
                    data = json.loads(out.content[0].text)
                    assert data["variants_count"] == 2 and data["log"]["missing_steps"][0]["activity"] == "Revisión"
        asyncio.run(go())
    finally:
        proc.terminate()
        try:
            proc.wait(10)
        except subprocess.TimeoutExpired:
            proc.kill()


# ------------------------------------------------------------------------- editable model, pairing, row wording
def _p(res):
    return {x["parameter"]: x for x in res["parameters"]}


def test_edited_model_changes_the_validation_deterministically_and_keeps_provenance(shop):
    kw = dict(holdout=0.3, replications=8, seed=4)
    fitted = el.calibrate_process(shop, "taller", gen.MAPPING, gen.OPTIONS, **kw)
    assert fitted["validation"]["passed"] is True and fitted["edits"] == [] and fitted["edit_diff"] is None
    assert fitted["model_source"] == "fitted"
    edits = {"stations": {"Impresión": {"servers": 3, "service": {"dist": "exponential", "mean": 0.2}}},
             "arrival_rate": 1.5,
             "routing": {"next": {"Recepción": {"Revisión": 3, "Impresión": 1}}}}
    alt = [{"name": "segunda impresora", "changes": [{"type": "add_resource", "activity": "Impresión"}]}]
    a = el.calibrate_process(shop, "taller", gen.MAPPING, gen.OPTIONS, variants=alt, model_edits=edits,
                             model_name="Taller editado", **kw)
    b = el.calibrate_process(shop, "taller", gen.MAPPING, gen.OPTIONS, model_edits=edits, **kw)
    assert a["model_source"] == "edited"
    # an edit that makes the printer far faster and the demand higher cannot reproduce the held-out cycle time
    assert a["validation"]["passed"] is False and a["edit_diff"]["fitted_passed"] is True
    assert a["validation"]["verdict"].endswith("not the fitted one.")
    assert a["edit_diff"]["verdict"].startswith("The edited model fails")
    # deterministic: same seeds, same edited result; and the fitted side of the diff is the fitted run itself
    assert a["baseline"]["metrics"] == b["baseline"]["metrics"]
    assert [r["edited_simulated"] for r in a["edit_diff"]["rows"]] == [r["simulated"] for r in a["validation"]["rows"]]
    assert [r["fitted_simulated"] for r in a["edit_diff"]["rows"]] == [r["simulated"] for r in fitted["validation"]["rows"]]
    cyc = next(r for r in a["edit_diff"]["rows"] if r["metric"] == "cycle_time_mean_hours")
    assert cyc["edited_simulated"] < cyc["fitted_simulated"] and cyc["delta"] < 0
    # provenance: edited parameters say so and keep the original value and the fitted source
    p = _p(a)
    assert p["servers:Impresión"]["edited"] and p["servers:Impresión"]["original_value"] == 1
    assert p["servers:Impresión"]["value"] == 3 and p["servers:Impresión"]["fitted"]["source"]["resources"] == ["Imp-1"]
    assert "edited by the user" in p["servers:Impresión"]["estimator"]
    assert p["service:Impresión"]["original_value"]["dist"] == "empirical" and p["service:Impresión"]["value"]["mean_hours"] == 0.2
    assert p["arrivals"]["edited"] and p["arrivals"]["value"] == {"type": "poisson", "rate": 1.5}
    # shares 3:1 are normalised and the record says so
    assert p["routing:Recepción"]["value"] == pytest.approx({"Revisión": 0.75, "Impresión": 0.25})
    assert next(e for e in a["edits"] if e["parameter"] == "routing:Recepción")["normalised"] is True
    assert not p["service:Revisión"].get("edited")
    # alternatives are compared with the edited model as the baseline
    assert a["alternatives"][0]["warnings"][0].startswith("The baseline failed")
    assert sc.get_model(shop, "Taller editado")["model"]["provenance"]["edited_by_user"] == [
        "arrivals", "servers:Impresión", "service:Impresión", "routing:Recepción"]
    # the edits travel in the recipe and re-run identically
    again = el.calibrate_process(shop, "taller", None, None, recipe=a["recipe"], replications=8, seed=4)
    assert again["baseline"]["metrics"] == a["baseline"]["metrics"] and again["model_source"] == "edited"


def test_invalid_model_edits_are_rejected_with_explicit_messages_in_both_languages(shop):
    cases = [
        ({"arrival_rate": -1}, "arrival rate must be greater than zero", "tasa de llegadas debe ser mayor que cero"),
        ({"arrival_rate": "x"}, "must be a finite number", "debe ser un número finito"),
        ({"stations": {"Nada": {"servers": 2}}}, "unknown activity 'Nada'", "actividad desconocida 'Nada'"),
        ({"stations": {"Impresión": {"servers": 0}}}, "at least 1", "al menos 1"),
        ({"stations": {"Impresión": {"servers": 1.5}}}, "whole number", "número entero"),
        ({"stations": {"Impresión": {"service": {"dist": "exponential", "mean": -2}}}}, "stations.Impresión.service",
         "stations.Impresión.service"),
        ({"stations": {"Impresión": {"service": {"dist": "empirical"}}}}, "give the complete list of values",
         "indica la lista completa de valores"),
        ({"stations": {"Impresión": {"speed": 3}}}, "unknown key 'speed'", "clave desconocida 'speed'"),
        ({"routing": {"next": {"Recepción": {"Revisión": 0, "Impresión": 0}}}}, "add up to zero", "suman cero"),
        ({"routing": {"next": {"Recepción": {"Revisión": -1, "Impresión": 2}}}}, "cannot be negative", "no puede ser negativa"),
        ({"routing": {"next": {"Recepción": {"Fantasma": 1}}}}, "unknown target 'Fantasma'", "destino desconocido 'Fantasma'"),
        ({"bogus": 1}, "unknown key 'bogus'", "clave desconocida 'bogus'"),
    ]
    for edits, en, es in cases:
        with pytest.raises(pm.ProcessError, match="Model edit rejected") as e:
            el.calibrate_process(shop, "taller", gen.MAPPING, gen.OPTIONS, replications=2, model_edits=edits)
        assert en in str(e.value), (edits, str(e.value))
        with pytest.raises(pm.ProcessError, match="Edición del modelo rechazada") as e2:
            call_tool_sync(shop, "data_process", {"action": "calibrate", "dataset": "taller", "mapping": gen.MAPPING,
                                                  "log_options": gen.OPTIONS, "replications": 2,
                                                  "model_edits": edits, "lang": "es"})
        assert es in str(e2.value), (edits, str(e2.value))


LIFE = """caso,paso,momento,ciclo,quien
C1,Imprimir,2026-02-02 09:00:00,start,Ana
C1,Imprimir,2026-02-02 09:30:00,start,Beto
C1,Imprimir,2026-02-02 10:00:00,complete,Beto
C1,Imprimir,2026-02-02 11:00:00,complete,Ana
C1,Entrega,2026-02-02 11:30:00,complete,Ana
C2,Imprimir,2026-02-02 12:00:00,start,Ana
C2,Imprimir,2026-02-02 12:10:00,start,Beto
C2,Imprimir,2026-02-02 12:50:00,complete,Ana
C2,Imprimir,2026-02-02 13:10:00,complete,Beto
C2,Entrega,2026-02-02 13:30:00,complete,Ana
"""
LMAP = {"case": "caso", "activity": "paso", "timestamp": "momento", "lifecycle": "ciclo"}


def _durations(log):
    return sorted((cid, e[5]["rows"], round((e[0] - e[2]) / 3600, 4)) for cid, evs in log["cases"] for e in evs
                  if e[1] == "Imprimir")


def test_overlapping_runs_are_paired_by_resource_when_mapped_else_fifo(services):
    services.ingest_text(LIFE, "solape", "csv")
    by_res = el.load(services, "solape", {**LMAP, "resource": "quien"}, {"end_activities": ["Entrega"]})
    fifo = el.load(services, "solape", LMAP, {"end_activities": ["Entrega"]})
    # by hand. C1: Beto 09:30->10:00 = 0.5 h (rows 2,3), Ana 09:00->11:00 = 2 h (rows 1,4).
    #           C2: Ana 12:00->12:50 = 5/6 h (rows 6,8), Beto 12:10->13:10 = 1 h (rows 7,9)
    got = {(c, tuple(r)): d for c, r, d in _durations(by_res)}
    assert got == {("C1", (2, 3)): 0.5, ("C1", (1, 4)): 2.0, ("C2", (6, 8)): 0.8333, ("C2", (7, 9)): 1.0}
    # first in, first out (no resource mapped): C1 09:00->10:00 = 1 h, 09:30->11:00 = 1.5 h; C2 same as by resource
    got = {(c, tuple(r)): d for c, r, d in _durations(fifo)}
    assert got == {("C1", (1, 3)): 1.0, ("C1", (2, 4)): 1.5, ("C2", (6, 8)): 0.8333, ("C2", (7, 9)): 1.0}
    pr = by_res["diag"]["pairing"]
    assert pr["rule"] == "resource_then_fifo"
    act = next(a for a in pr["activities"] if a["activity"] == "Imprimir")
    # four pairs, all overlapping on the second completion only ... C1: both completions saw 2 / 1 open starts
    assert act["pairs"] == 4 and act["by_resource"] == 4 and act["by_fifo"] == 0 and act["rule_used"] == "resource"
    assert act["overlapping"] == 2 and act["differs_from_fifo"] == 1       # only C1's first completion took the later start
    assert by_res["diag"]["resource_mismatch"]["total"] == 0
    assert fifo["diag"]["resource_mismatch"]["total"] == 0                   # no resource column, nothing to compare
    pf = fifo["diag"]["pairing"]
    assert pf["rule"] == "fifo" and "No resource column" in pf["description"]
    assert next(a for a in pf["activities"] if a["activity"] == "Imprimir")["rule_used"] == "fifo"
    # a step with a single run never overlaps
    ent = next(a for a in pr["activities"] if a["activity"] == "Entrega")
    assert ent["pairs"] == 0
    # reported in the profile too, and the completion-only mode has no pairing at all
    prof = el.profile_process(services, "solape", {**LMAP, "resource": "quien"}, {"end_activities": ["Entrega"]})
    assert prof["log"]["pairing"]["rule"] == "resource_then_fifo"
    services.ingest_text(SEQ, "pedidos2", "csv")
    assert el.profile_process(services, "pedidos2", M)["log"]["pairing"] is None


def test_row_references_are_localised(services):
    services.ingest_text(BAD, "bad2", "csv")
    en = el.profile_process(services, "bad2", M, {"timezone": "Europe/Madrid"})
    es = call_tool_sync(services, "data_process", {"action": "profile", "dataset": "bad2", "mapping": M,
                                                   "log_options": {"timezone": "Europe/Madrid"}, "lang": "es"})
    ex_en = next(d for d in en["deviations"] if d["type"] == "invalid timestamp")["examples"]
    ex_es = next(d for d in es["deviations"] if d["type"] == "invalid timestamp")["examples"]
    assert ex_en[0].startswith("row 13: ") and ex_es[0].startswith("fila 13: ")
    miss = next(d for d in es["deviations"] if d["type"] == "missing case or activity")
    assert miss["examples"] == ["fila 17"]
    services.ingest_text(DUP, "dup2", "csv")
    d = call_tool_sync(services, "data_process", {"action": "profile", "dataset": "dup2", "mapping": M, "lang": "es"})
    dup = next(x for x in d["deviations"] if x["type"] == "duplicate")["examples"]
    assert dup[0].startswith("fila ") and " = fila " in dup[0] and "row" not in dup[0]

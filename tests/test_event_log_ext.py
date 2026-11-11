"""Arrival seasonality in the calibration and value-by-value edits of empirical samples, on invented logs whose
pattern is known: arrivals peaking at 10:00 and 17:00 (weekends quieter in the second log)."""
from datetime import datetime, timedelta, timezone
from zoneinfo import ZoneInfo

import numpy as np
import pytest

from nightingale import event_log as el
from nightingale import process_mining as pm
from nightingale import seasonality as sn
from nightingale.agent_tools import call_tool_sync

T0 = datetime(2026, 1, 5, 0, 0, 0)            # a Monday
MAP = {"case": "pedido", "activity": "actividad", "timestamp": "momento", "lifecycle": "ciclo", "resource": "operario"}
OPT = {"end_activities": ["Entrega"], "expected": ["Recepción", "Entrega"]}


def hour_weights(peaks=((10, 6.0), (17, 5.0)), day=(8, 19)):
    w = np.full(24, 0.05)
    for h in range(*day):
        w[h] = 1.0
    for h, v in peaks:
        w[h] = v
    return w


def arrival_times(days=35, per_day=90.0, weekend=1.0, flat=False, seed=5):
    """Arrival instants (hours since T0) from a Poisson count per hour with a known rate."""
    rng = np.random.default_rng(seed)
    w = np.full(24, 1.0) if flat else hour_weights()
    w = w / w.sum() * per_day
    out = []
    for d in range(days):
        f = weekend if d % 7 >= 5 else 1.0
        for h in range(24):
            k = rng.poisson(w[h] * f)
            out += list(d * 24 + h + rng.random(k))
    return sorted(out)


def log_csv(arr, tz=None, seed=3):
    rng = np.random.default_rng(seed)
    rows = []
    for i, t in enumerate(arr):
        s1 = t
        e1 = s1 + rng.exponential(0.15)
        s2 = e1
        e2 = s2 + rng.exponential(0.10)
        for act, s, e in (("Recepción", s1, e1), ("Entrega", s2, e2)):
            r = f"{act[:3]}-{int(rng.integers(1, 4))}"
            for ts, lc in ((s, "start"), (e, "complete")):
                rows.append(f"P{i:05d},{act},{(T0 + timedelta(hours=ts)).strftime('%Y-%m-%d %H:%M:%S.%f')},{lc},{r}")
    return "pedido,actividad,momento,ciclo,operario\n" + "\n".join(rows)


def epoch(arr, tz=timezone.utc):
    base = T0.replace(tzinfo=tz).timestamp()
    return [base + t * 3600 for t in arr]


@pytest.fixture
def seasonal(services):
    services.ingest_text(log_csv(arrival_times()), "estacional", "csv")
    return services


# ------------------------------------------------------------------------------------- the test itself
def test_peaks_at_10_and_17_are_found_and_significant():
    a = sn.analyse(epoch(arrival_times()), timezone.utc)
    h = a["hour_of_day"]
    assert a["evaluable"] and a["significant"] and h["test"]["significant"] and h["test"]["p_value"] < 1e-10
    assert h["test"]["df"] == 23 and h["test"]["reliable"]
    assert [p["slot"] for p in h["peaks"]] == [10, 17]
    assert h["peaks"][0]["ratio_to_mean"] > 4 and h["peaks"][1]["ratio_to_mean"] > 3
    # no weekday effect was put in: the hourly schedule alone is used (24 slots), and it reproduces the counts
    assert a["day_of_week"]["test"]["significant"] is False and a["used"] == "hour"
    sched = a["schedule"]
    assert sched["cycle_hours"] == 24 and len(sched["rates"]) == 24
    assert sched["rates"][10] > 4 * sched["rates"][12] and sched["rates"][3] < 0.1 * sched["rates"][12]
    assert sum(r * e for r, e in zip(sched["rates"], h["exposure_hours"])) == pytest.approx(a["arrivals"], rel=1e-9)
    # by hand: the true rate at 10:00 is per_day * 6 / sum(w) per day
    w = hour_weights()
    assert sched["rates"][10] == pytest.approx(90 * w[10] / w.sum(), rel=0.12)


def test_a_constant_rate_is_not_called_seasonal():
    a = sn.analyse(epoch(arrival_times(flat=True)), timezone.utc)
    assert a["evaluable"] and a["significant"] is False and a["schedule"] is None and a["used"] is None
    assert a["hour_of_day"]["test"]["p_value"] > 0.01 and a["day_of_week"]["test"]["p_value"] > 0.01
    assert "No significant pattern" in a["verdict"]
    forced = sn.analyse(epoch(arrival_times(flat=True)), timezone.utc, mode="on")
    assert forced["schedule"]["cycle_hours"] == 24 and "requested although" in forced["verdict"]


def test_weekdays_quieter_at_the_weekend_give_a_168_hour_schedule():
    a = sn.analyse(epoch(arrival_times(days=56, weekend=0.3, seed=9)), timezone.utc)
    assert a["day_of_week"]["test"]["significant"] and a["used"] == "hour_day"
    r = a["schedule"]["rates"]
    assert a["schedule"]["cycle_hours"] == 168 and len(r) == 168 and a["schedule"]["origin"] == "monday_00:00"
    assert r[5 * 24 + 10] == pytest.approx(0.3 * r[10], rel=0.2)          # Saturday 10:00 against Monday 10:00
    assert [p["slot"] for p in a["day_of_week"]["peaks"]] == [] or "Sat" not in [p["slot"] for p in a["day_of_week"]["peaks"]]


def test_the_declared_zone_decides_the_hour():
    arr = arrival_times()
    madrid = ZoneInfo("Europe/Madrid")
    local = sn.analyse(epoch(arr, madrid), madrid)                        # local clock read in Madrid
    assert sorted(p["slot"] for p in local["hour_of_day"]["peaks"]) == [10, 17]
    utc = sn.analyse(epoch(arr, madrid), timezone.utc)                    # same instants seen in UTC: January, one hour earlier
    assert sorted(p["slot"] for p in utc["hour_of_day"]["peaks"]) == [9, 16]


def test_short_or_small_logs_say_why_instead_of_guessing():
    a = sn.analyse(epoch(arrival_times())[:20], timezone.utc)
    assert a["evaluable"] is False and a["schedule"] is None and "at least 30" in a["reason"]
    b = sn.analyse(epoch([0.1 * i for i in range(60)]), timezone.utc)
    assert b["evaluable"] is False and "daily pattern" in b["reason"]


# --------------------------------------------------------------------------------- calibration and profile
def test_calibration_uses_the_schedule_and_reports_provenance(seasonal):
    kw = dict(holdout=0.3, replications=6, seed=4)
    res = el.calibrate_process(seasonal, "estacional", MAP, OPT, **kw)
    seas = res["arrival_seasonality"]
    assert seas["significant"] and res["seasonal_arrivals"] == "auto"
    assert res["model"]["arrivals"]["type"] == "schedule" and res["model"]["arrivals"]["cycle_hours"] == 24
    p = next(x for x in res["parameters"] if x["parameter"] == "arrivals")
    assert p["value"]["type"] == "schedule" and p["value"]["peak_hours"] == [10, 17]
    assert "chi-square" in p["estimator"] and p["identifiable"]
    assert res["validation"]["rows"]                                       # the engine ran with the schedule
    assert any(r["metric"] == "cases_per_hour" for r in res["validation"]["rows"])
    # the setting travels in the recipe and re-runs identically
    assert res["recipe"]["calibration"]["seasonal_arrivals"] == "auto"
    again = el.calibrate_process(seasonal, None, None, None, recipe=res["recipe"], replications=6, seed=4)
    assert again["baseline"]["metrics"] == res["baseline"]["metrics"]
    # off: the plain fit, no test
    off = el.calibrate_process(seasonal, "estacional", MAP, OPT, seasonal_arrivals="off", **kw)
    assert off["model"]["arrivals"]["type"] in ("poisson", "empirical") and off["arrival_seasonality"] is None
    # editing the rate afterwards replaces the schedule and keeps it as the original value
    ed = el.calibrate_process(seasonal, "estacional", MAP, OPT, model_edits={"arrival_rate": 2.0}, **kw)
    pa = next(x for x in ed["parameters"] if x["parameter"] == "arrivals")
    assert pa["edited"] and pa["original_value"]["type"] == "schedule" and ed["model"]["arrivals"]["type"] == "poisson"


def test_profile_reports_the_test_and_the_recipe_keeps_a_summary(seasonal):
    prof = el.profile_process(seasonal, "estacional", MAP, OPT)
    a = prof["arrival_seasonality"]
    assert a["significant"] and a["hour_of_day"]["test"]["p_value"] < 1e-10
    exp = el.export_process(seasonal, "estacional", MAP, OPT)
    summ = exp["recipe"]["profile"]["arrival_seasonality"]
    assert summ["peak_hours"] == [10, 17] and "counts" not in str(summ)


def test_rest_tool_and_language(client):
    client.services.ingest_text(log_csv(arrival_times()), "estacional", "csv")
    body = {"dataset": "estacional", "mapping": MAP, "log_options": OPT, "replications": 4, "seasonal_arrivals": "auto"}
    r = client.post("/api/process/calibrate", json=body)
    assert r.status_code == 200, r.text
    assert r.json()["model"]["arrivals"]["type"] == "schedule"
    es = client.post("/api/process/profile?lang=es", json={"dataset": "estacional", "mapping": MAP, "log_options": OPT}).json()
    assert "patrón" in es["arrival_seasonality"]["verdict"]
    bad = client.post("/api/process/calibrate", json={**body, "seasonal_arrivals": "siempre"})
    assert bad.status_code == 400
    out = call_tool_sync(client.services, "data_process", {"action": "calibrate", "dataset": "estacional", "mapping": MAP,
                                                          "log_options": OPT, "replications": 4, "seasonal_arrivals": "off"})
    assert out["model"]["arrivals"]["type"] != "schedule"


# ------------------------------------------------------------------------- value-by-value empirical edits
@pytest.fixture
def shop(services):
    import evlog_gen as gen
    services.ingest_text(gen.lifecycle_csv(gen.generate(seed=13)), "taller", "csv")
    return services


def _station(res, name):
    return next(s for s in res["model"]["stations"] if s["name"] == name)


def test_empirical_sample_is_edited_value_by_value(shop):
    import evlog_gen as gen
    kw = dict(holdout=0.3, replications=6, seed=4)
    fitted = el.calibrate_process(shop, "taller", gen.MAPPING, gen.OPTIONS, seasonal_arrivals="off", **kw)
    vals = list(_station(fitted, "Impresión")["service"]["values"])
    assert len(vals) > 50
    edits = {"stations": {"Impresión": {"service_values": {"set": {"0": 9.5, 3: 0.0}, "remove": [1, 2], "add": [1.5, 2.5]}}}}
    ed = el.calibrate_process(shop, "taller", gen.MAPPING, gen.OPTIONS, model_edits=edits, seasonal_arrivals="off", **kw)
    new = _station(ed, "Impresión")["service"]
    assert new["dist"] == "empirical"
    want = [9.5, 0.0] + vals[4:] + [1.5, 2.5]                               # index 0 and 3 set, 1 and 2 removed, two added
    assert new["values"] == pytest.approx(want)
    p = next(x for x in ed["parameters"] if x["parameter"] == "service:Impresión")
    assert p["edited"] and p["original_value"]["dist"] == "empirical" and p["value"]["values_count"] == len(want)
    assert p["value"]["set"] == 2 and p["value"]["removed"] == 2 and p["value"]["added"] == 2
    assert p["value"]["mean_hours"] == pytest.approx(sum(want) / len(want))
    assert ed["model_source"] == "edited" and ed["edit_diff"] is not None
    # the complete list can also be given, and it replaces the sample
    full = el.calibrate_process(shop, "taller", gen.MAPPING, gen.OPTIONS, seasonal_arrivals="off", **kw,
                                model_edits={"stations": {"Impresión": {"service": {"dist": "empirical", "values": [0.2, 0.4, 0.6]}}}})
    assert _station(full, "Impresión")["service"]["values"] == [0.2, 0.4, 0.6]
    # a tiny sample makes the printer much faster than the fitted 0.55 h: the verdict changes
    assert full["validation"]["passed"] is False and fitted["validation"]["passed"] is True


def test_arrival_gaps_can_be_replaced_and_invalid_value_edits_are_rejected(shop):
    import evlog_gen as gen
    kw = dict(replications=2, seasonal_arrivals="off")
    ok = el.calibrate_process(shop, "taller", gen.MAPPING, gen.OPTIONS, model_edits={"arrival_gaps": [0.5, 1.0, 1.5]}, **kw)
    assert ok["model"]["arrivals"] == {"type": "empirical", "values": [0.5, 1.0, 1.5]}
    pa = next(x for x in ok["parameters"] if x["parameter"] == "arrivals")
    assert pa["edited"] and pa["value"]["mean_gap_hours"] == pytest.approx(1.0)
    cases = [
        ({"stations": {"Impresión": {"service_values": {"set": {"99999": 1}}}}}, "not a position of the sample", "no es una posición de la muestra"),
        ({"stations": {"Impresión": {"service_values": {"set": {"0": -1}}}}}, "cannot be negative", "no puede ser negativo"),
        ({"stations": {"Impresión": {"service_values": {"set": {"0": 1}, "remove": [0]}}}}, "both set and removed", "a la vez en set y en remove"),
        ({"stations": {"Impresión": {"service": {"dist": "empirical", "values": []}}}}, "cannot be left empty", "no puede quedar vacía"),
        ({"stations": {"Impresión": {"service": {"dist": "empirical", "values": [1]}, "service_values": {"add": [1]}}}}, "not both", "no ambos"),
        ({"arrival_gaps": {"add": [1.0]}}, "not an empirical sample of gaps", "no una muestra empírica de intervalos"),
        ({"arrival_gaps": [0.1], "arrival_rate": 1}, "cannot be edited together", "no se pueden editar a la vez"),
        ({"arrival_gaps": [0, 0]}, "add up to zero", "suman cero"),
        ({"stations": {"Impresión": {"service_values": {"remove": list(range(100000))}}}}, "not a position of the sample", "no es una posición"),
    ]
    for edits, en, es in cases:
        with pytest.raises(pm.ProcessError, match="Model edit rejected") as e:
            el.calibrate_process(shop, "taller", gen.MAPPING, gen.OPTIONS, model_edits=edits, **kw)
        assert en in str(e.value), (edits, str(e.value))
        with pytest.raises(pm.ProcessError, match="Edición del modelo rechazada") as e2:
            call_tool_sync(shop, "data_process", {"action": "calibrate", "dataset": "taller", "mapping": gen.MAPPING,
                                                  "log_options": gen.OPTIONS, "replications": 2, "model_edits": edits,
                                                  "seasonal_arrivals": "off", "lang": "es"})
        assert es in str(e2.value), (edits, str(e2.value))


# ------------------------------------------------------------------------------------------ lifecycle pairing
PAIR = """caso,paso,momento,ciclo,quien
C1,Imprimir,2026-02-02 09:00:00,start,Ana
C1,Imprimir,2026-02-02 09:30:00,start,Beto
C1,Imprimir,2026-02-02 10:00:00,complete,
C1,Imprimir,2026-02-02 11:00:00,complete,Ana
C1,Entrega,2026-02-02 11:30:00,complete,Ana
"""


def test_a_completion_without_resource_falls_back_to_first_in_first_out(services):
    services.ingest_text(PAIR, "sinrecurso", "csv")
    log = el.load(services, "sinrecurso", {"case": "caso", "activity": "paso", "timestamp": "momento", "lifecycle": "ciclo",
                                           "resource": "quien"}, {"end_activities": ["Entrega"]})
    act = next(a for a in log["diag"]["pairing"]["activities"] if a["activity"] == "Imprimir")
    # the first completion has no resource: it takes the earliest open start (Ana, 1 h); the second finds no start of
    # Ana left and takes the remaining one (Beto, 1.5 h): both decided by first in, first out
    assert act["pairs"] == 2 and act["overlapping"] == 1 and act["by_fifo"] >= 1
    durations = sorted(round((e[0] - e[2]) / 3600, 4) for _c, evs in log["cases"] for e in evs if e[1] == "Imprimir")
    assert durations == [1.0, 1.5]

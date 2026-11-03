"""Process discovery, conformance and change rehearsal from an event log.

An event log is a table with one row per event: a case id, an activity, a timestamp (the moment the step
finished, or, when a separate start column is mapped, the moment it started) and optionally a resource and a
cost. Everything here is descriptive and explainable:

* discovery counts what happened (directly-follows graph with frequencies and waiting times, variants, rework
  loops, throughput times, bottlenecks, resource workload) and filters the graph with the heuristic-miner
  dependency measure;
* conformance checks every case against a reference process (ordered steps with optional / parallel markers, or a
  list of allowed transitions) and reports skipped, inserted, repeated and reordered steps with counts and
  example case ids;
* rehearsal turns the discovered process into a model for the scenario engine (nightingale/scenarios.py: its
  routing network mode), checks that the baseline model reproduces the observed throughput time, and compares
  changes (add a resource, remove a step, change a routing share, reduce a duration) against it with common random
  numbers and 95% intervals.

All durations are in hours. The limits of each method are returned with its result, not hidden.
"""
from __future__ import annotations

import copy
import math
import re
from collections import Counter, defaultdict, deque
from datetime import datetime, timezone
from typing import Any, Optional

from . import scenarios as sc

MAX_EVENTS = 2_000_000
BIG = 10_000  # "unlimited" servers for steps whose capacity cannot be estimated
MIN_CASES = 5
SMALL_LOG = 30
CAVEAT = ("Descriptive results from the log as given; rehearsals are scenarios under the stated assumptions "
          "(first-order routing, independent durations), not predictions.")
TIME_UNITS = "hours"


class ProcessError(ValueError):
    pass


# ----------------------------------------------------------------------- helpers
def _q(name: str) -> str:
    return '"' + str(name).replace('"', '""') + '"'


def _f(v: Any) -> Optional[float]:
    if v is None:
        return None
    try:
        v = float(v)
    except (TypeError, ValueError):
        return None
    return v if math.isfinite(v) else None


def stats(xs: list[float]) -> dict:
    if not xs:
        return {"n": 0, "mean": None, "median": None, "p90": None, "min": None, "max": None, "total": 0.0}
    s = sorted(xs)
    return {"n": len(s), "mean": sum(s) / len(s), "median": sc.percentile(s, 0.5), "p90": sc.percentile(s, 0.9),
            "min": s[0], "max": s[-1], "total": sum(s)}


def _iso(t: float) -> str:
    return datetime.fromtimestamp(t, tz=timezone.utc).strftime("%Y-%m-%d %H:%M:%S")


# ------------------------------------------------------------------- the event log
# an event is (end time, activity, start time or None, resource or None, cost or None), times in epoch seconds
Event = tuple


def _table_columns(services, dataset: str):
    table = services._resolve_table_by_name(dataset)
    with services.engine.lock() as conn:
        cols = [(r[0], str(r[1])) for r in conn.execute(f"DESCRIBE {_q(table)}").fetchall()]
    return table, cols


def _check_mapping(cols: list[str], mapping: dict) -> dict:
    if not isinstance(mapping, dict):
        raise ProcessError("mapping: expected {case, activity, timestamp, start?, resource?, cost?}")
    out = {}
    for key in ("case", "activity", "timestamp"):
        if not mapping.get(key):
            raise ProcessError(f"mapping: {key!r} is required (use suggest to get proposals)")
    for key in ("case", "activity", "timestamp", "start", "resource", "cost"):
        v = mapping.get(key)
        if v:
            if v not in cols:
                raise ProcessError(f"mapping: column {v!r} not found for {key!r}; columns: {cols}")
            out[key] = v
    used = [out[k] for k in ("case", "activity", "timestamp", "start") if k in out]
    if len(used) != len(set(used)):
        raise ProcessError("mapping: case, activity, timestamp and start must be different columns")
    return out


def load_log(services, dataset: str, mapping: dict) -> tuple[list[tuple[str, list[Event]]], dict]:
    table, typed = _table_columns(services, dataset)
    m = _check_mapping([c for c, _ in typed], mapping)
    sel = [f"CAST({_q(m['case'])} AS VARCHAR)", f"CAST({_q(m['activity'])} AS VARCHAR)",
           f"epoch(TRY_CAST({_q(m['timestamp'])} AS TIMESTAMP))",
           f"epoch(TRY_CAST({_q(m['start'])} AS TIMESTAMP))" if "start" in m else "NULL",
           f"CAST({_q(m['resource'])} AS VARCHAR)" if "resource" in m else "NULL",
           f"TRY_CAST({_q(m['cost'])} AS DOUBLE)" if "cost" in m else "NULL"]
    with services.engine.lock() as conn:
        total = conn.execute(f"SELECT count(*) FROM {_q(table)}").fetchone()[0]
        if total > MAX_EVENTS:
            raise ProcessError(f"{total:,} rows is above the {MAX_EVENTS:,}-event limit; filter the dataset first")
        rows = conn.execute(
            f"SELECT * FROM (SELECT {', '.join(sel)}, row_number() OVER () AS rn FROM {_q(table)}) "
            "ORDER BY 1, 3, 7").fetchall()
    cases: list[tuple[str, list[Event]]] = []
    dropped = Counter()
    neg_dur = 0
    cur_id, cur = None, None
    used = 0
    for cid, act, t, st, res, cost, _rn in rows:
        if cid is None or act is None:
            dropped["missing case or activity"] += 1
            continue
        if t is None or not math.isfinite(t):
            dropped["missing or unreadable timestamp"] += 1
            continue
        if "start" in m:
            if st is None or not math.isfinite(st):
                dropped["missing or unreadable start"] += 1
                continue
            if st > t:
                neg_dur += 1
                st = t
        else:
            st = None
        used += 1
        if cid != cur_id:
            cur_id, cur = cid, []
            cases.append((cid, cur))
        cur.append((t, act, st, res, _f(cost)))
    # with a start column the events of a case are ordered by completion; sorting was done in SQL on column 3
    quality = {"rows": total, "events_used": used, "cases": len(cases),
               "dropped": dict(dropped), "dropped_total": sum(dropped.values()),
               "start_after_end": neg_dur, "has_start": "start" in m, "has_resource": "resource" in m,
               "has_cost": "cost" in m, "mapping": m}
    if not cases:
        raise ProcessError("no usable events: check the mapping (case, activity, timestamp) and the timestamp format")
    return cases, quality


# ------------------------------------------------------------------ mapping suggestion
_NAMES = {
    "case": ("case", "trace", "ticket", "instance", "order", "caseid", "case_id", "process", "incident", "claim"),
    "activity": ("activity", "event", "step", "task", "action", "status", "stage", "operation"),
    "timestamp": ("timestamp", "time", "date", "complete", "end", "finish", "datetime", "when", "at"),
    "start": ("start", "begin", "from"),
    "resource": ("resource", "user", "employee", "agent", "owner", "worker", "actor", "role", "assignee",
                 "operator", "staff", "person"),
    "cost": ("cost", "amount", "price", "value", "expense", "eur", "usd"),
}


def _name_score(name: str, key: str) -> float:
    n = re.sub(r"[^a-z0-9]+", " ", name.lower())
    toks = n.split()
    best = 0.0
    for w in _NAMES[key]:
        if n.strip() == w or w in toks:
            best = max(best, 1.0)
        elif w in n.replace(" ", ""):
            best = max(best, 0.6)
    if name.lower().endswith("id") and key == "case":
        best = max(best, 0.7)
    return best


def suggest_mapping(services, dataset: str) -> dict:
    table, typed = _table_columns(services, dataset)
    with services.engine.lock() as conn:
        n = conn.execute(f"SELECT count(*) FROM {_q(table)}").fetchone()[0]
        info = {}
        for col, typ in typed:
            nn, dist, parsed = conn.execute(
                f"SELECT count({_q(col)}), count(DISTINCT {_q(col)}), count(TRY_CAST({_q(col)} AS TIMESTAMP)) "
                f"FROM (SELECT {_q(col)} FROM {_q(table)} LIMIT 20000)").fetchone()
            numeric = typ.upper().startswith(("TINYINT", "SMALLINT", "INTEGER", "BIGINT", "HUGEINT", "FLOAT", "DOUBLE",
                                              "DECIMAL", "UBIGINT", "UINTEGER", "REAL"))
            is_date = typ.upper().startswith(("DATE", "TIMESTAMP"))
            ts_like = nn > 0 and parsed / nn >= 0.95 and (is_date or not numeric)
            info[col] = {"type": typ, "non_null": nn, "distinct": dist, "numeric": numeric, "timestamp_like": ts_like}
    m = min(n, 20000)

    def rank(key, ok):
        out = []
        for col, i in info.items():
            if ok(col, i):
                out.append((_name_score(col, key) + extra(col, i, key), col))
        out.sort(key=lambda x: (-x[0], x[1]))
        return [{"column": c, "score": round(s, 3)} for s, c in out]

    def extra(col, i, key):
        ratio = i["distinct"] / m if m else 1
        if key == "case":
            return 0.3 if 0 < ratio <= 0.7 and i["distinct"] > 1 else 0
        if key == "activity":
            return 0.3 if i["distinct"] <= max(2, min(200, 0.2 * m)) else 0
        if key == "resource":
            return 0.1 if 1 < i["distinct"] <= 0.5 * m else 0
        return 0

    cand = {
        "case": rank("case", lambda c, i: not i["timestamp_like"] and i["distinct"] > 1 and i["distinct"] < m),
        "activity": rank("activity", lambda c, i: not i["timestamp_like"] and not i["numeric"] and 1 < i["distinct"]
                         <= max(2, min(500, 0.5 * m))),
        "timestamp": rank("timestamp", lambda c, i: i["timestamp_like"]),
        "start": rank("start", lambda c, i: i["timestamp_like"] and _name_score(c, "start") > 0),
        "resource": rank("resource", lambda c, i: not i["timestamp_like"] and not i["numeric"] and i["distinct"] > 1
                         and _name_score(c, "resource") > 0),
        "cost": rank("cost", lambda c, i: i["numeric"] and _name_score(c, "cost") > 0),
    }
    chosen: dict[str, str] = {}
    for key in ("timestamp", "case", "activity", "start", "resource", "cost"):
        for c in cand[key]:
            if c["column"] not in chosen.values():
                if key in ("case", "activity") and c["score"] < 0.3:
                    continue
                chosen[key] = c["column"]
                break
    if "timestamp" in chosen and "start" in chosen and chosen["start"] == chosen["timestamp"]:
        chosen.pop("start")
    if "start" in chosen and "timestamp" in chosen:
        # the end-like column is the timestamp; if the names say the other way round, swap
        if _name_score(chosen["timestamp"], "start") > 0 and _name_score(chosen["start"], "start") == 0:
            chosen["timestamp"], chosen["start"] = chosen["start"], chosen["timestamp"]
    if "start" not in chosen:
        starts = [c["column"] for c in cand["start"] if c["column"] != chosen.get("timestamp")]
        if starts:
            chosen["start"] = starts[0]
    warnings = []
    for key in ("case", "activity", "timestamp"):
        if key not in chosen:
            warnings.append(f"No column looks like {key}: pick it by hand.")
    return {"dataset": dataset, "mapping": chosen, "candidates": cand, "rows": n, "warnings": warnings,
            "columns": [{"name": c, **info[c]} for c, _ in typed]}


# -------------------------------------------------------------------- discovery
def _gap(prev: Event, cur: Event) -> float:
    """Hours between the end of prev and the start of cur (or its end when no start column is mapped)."""
    ref = cur[2] if cur[2] is not None else cur[0]
    return (ref - prev[0]) / 3600.0


def _duration(ev: Event) -> Optional[float]:
    return (ev[0] - ev[2]) / 3600.0 if ev[2] is not None else None


def case_span(events: list[Event]) -> tuple[float, float]:
    first = events[0][2] if events[0][2] is not None else events[0][0]
    return first, events[-1][0]


def discover_log(cases: list[tuple[str, list[Event]]], quality: dict, threshold: float = 0.5, min_count: int = 1,
                 max_variants: int = 20) -> dict:
    has_start = quality["has_start"]
    n_cases = len(cases)
    edges = defaultdict(lambda: {"count": 0, "cases": set(), "waits": []})
    act = defaultdict(lambda: {"count": 0, "cases": set(), "durations": [], "waits": [], "resources": Counter(),
                               "busy": 0.0, "repeat_cases": 0, "extra": 0, "cost": 0.0})
    starts, ends = Counter(), Counter()
    variants: dict[tuple, dict] = {}
    triple = Counter()
    times: list[float] = []
    overlaps = 0
    selfloops: dict = defaultdict(lambda: [0, set()])
    back_forth: dict = defaultdict(lambda: [0, set()])
    res_stats = defaultdict(lambda: {"events": 0, "busy": 0.0, "activities": Counter(), "first": None, "last": None})
    case_starts: list[float] = []
    case_rows = []
    for cid, evs in cases:
        trace = tuple(e[1] for e in evs)
        v = variants.setdefault(trace, {"count": 0, "times": [], "cases": []})
        t0, t1 = case_span(evs)
        v["count"] += 1
        v["times"].append((t1 - t0) / 3600.0)
        if len(v["cases"]) < 3:
            v["cases"].append(cid)
        times.append((t1 - t0) / 3600.0)
        case_starts.append(t0)
        starts[trace[0]] += 1
        ends[trace[-1]] += 1
        seen = Counter(trace)
        for i, e in enumerate(evs):
            a = act[e[1]]
            a["count"] += 1
            a["cases"].add(cid)
            d = _duration(e)
            if d is not None:
                a["durations"].append(d)
                a["busy"] += d
            if e[4] is not None:
                a["cost"] += e[4]
            if e[3] is not None:
                a["resources"][e[3]] += 1
                r = res_stats[e[3]]
                r["events"] += 1
                r["activities"][e[1]] += 1
                if d is not None:
                    r["busy"] += d
                tt = e[2] if e[2] is not None else e[0]
                r["first"] = tt if r["first"] is None else min(r["first"], tt)
                r["last"] = e[0] if r["last"] is None else max(r["last"], e[0])
            if i:
                p = evs[i - 1]
                g = _gap(p, e)
                if g < 0:
                    overlaps += 1
                    g = 0.0
                ed = edges[(p[1], e[1])]
                ed["count"] += 1
                ed["cases"].add(cid)
                ed["waits"].append(g)
                a["waits"].append(g)
                if p[1] == e[1]:
                    selfloops[e[1]][0] += 1
                    selfloops[e[1]][1].add(cid)
            if i >= 2 and evs[i - 2][1] == e[1] and evs[i - 1][1] != e[1]:
                key = (e[1], evs[i - 1][1])
                triple[key] += 1
                back_forth[key][0] += 1
                back_forth[key][1].add(cid)
        for name, k in seen.items():
            if k > 1:
                act[name]["repeat_cases"] += 1
                act[name]["extra"] += k - 1
    n_events = quality["events_used"]
    total_wait = sum(sum(a["waits"]) for a in act.values())
    span_h = (max(e[0] for _c, ev in cases for e in ev[-1:]) - min(case_starts)) / 3600.0
    # ---- graph with heuristic-miner dependency measure
    count = {k: v["count"] for k, v in edges.items()}
    graph = []
    for (a, b), ed in edges.items():
        n_ab = ed["count"]
        if a == b:
            dep = n_ab / (n_ab + 1)
            l2 = 0.0
        else:
            n_ba = count.get((b, a), 0)
            dep = (n_ab - n_ba) / (n_ab + n_ba + 1)
            n_aba, n_bab = triple.get((a, b), 0), triple.get((b, a), 0)
            l2 = (n_aba + n_bab) / (n_aba + n_bab + 1)
        kept = n_ab >= min_count and (dep >= threshold or (a != b and l2 >= threshold and (b, a) in count))
        st = stats(ed["waits"])
        graph.append({"from": a, "to": b, "count": n_ab, "cases": len(ed["cases"]),
                      "share_of_cases": len(ed["cases"]) / n_cases, "dependency": dep, "loop2": l2, "kept": bool(kept),
                      "wait_mean_hours": st["mean"], "wait_median_hours": st["median"], "wait_p90_hours": st["p90"],
                      "wait_total_hours": st["total"]})
    graph.sort(key=lambda e: (-e["count"], e["from"], e["to"]))
    # ---- activities
    activities = []
    for name, a in act.items():
        w, d = stats(a["waits"]), stats(a["durations"])
        res = a["resources"]
        n_res = len(res)
        span_busy = a["busy"]
        servers = n_res if n_res else None
        activities.append({
            "activity": name, "count": a["count"], "cases": len(a["cases"]), "share_of_cases": len(a["cases"]) / n_cases,
            "start_count": starts.get(name, 0), "end_count": ends.get(name, 0),
            "duration_mean_hours": d["mean"], "duration_median_hours": d["median"], "duration_p90_hours": d["p90"],
            "wait_mean_hours": w["mean"], "wait_median_hours": w["median"], "wait_p90_hours": w["p90"],
            "wait_total_hours": w["total"], "wait_share": (w["total"] / total_wait) if total_wait > 0 else 0.0,
            "repeat_cases": a["repeat_cases"], "extra_visits": a["extra"], "resources": n_res or None,
            "utilisation": (span_busy / (servers * span_h)) if servers and span_h > 0 and has_start else None,
            "cost_total": a["cost"] if quality["has_cost"] else None})
    activities.sort(key=lambda a: (-a["count"], a["activity"]))
    # ---- variants
    vlist = sorted(variants.items(), key=lambda kv: (-kv[1]["count"], kv[0]))
    cum = 0.0
    vout = []
    for i, (trace, v) in enumerate(vlist):
        share = v["count"] / n_cases
        cum += share
        st = stats(v["times"])
        vout.append({"id": i + 1, "activities": list(trace), "count": v["count"], "share": share, "cumulative_share": cum,
                     "steps": len(trace), "throughput_mean_hours": st["mean"], "throughput_median_hours": st["median"],
                     "example_cases": v["cases"]})
    # ---- bottlenecks
    ranked = sorted((a for a in activities if a["wait_total_hours"] > 0), key=lambda a: -a["wait_total_hours"])
    bottlenecks = [{"activity": a["activity"], "wait_total_hours": a["wait_total_hours"], "wait_share": a["wait_share"],
                    "wait_mean_hours": a["wait_mean_hours"], "wait_median_hours": a["wait_median_hours"],
                    "wait_p90_hours": a["wait_p90_hours"], "visits": a["count"], "utilisation": a["utilisation"]}
                   for a in ranked[:10]]
    top_edges = sorted((e for e in graph if e["wait_total_hours"] > 0), key=lambda e: -e["wait_total_hours"])[:10]
    verdict = None
    if bottlenecks:
        b = bottlenecks[0]
        if b["wait_share"] >= 0.3:
            verdict = (f"{b['activity']!r} concentrates {b['wait_share'] * 100:.0f}% of all waiting time "
                       f"(median {b['wait_median_hours']:.2f} h, p90 {b['wait_p90_hours']:.2f} h before the step).")
        else:
            verdict = "Waiting time is spread out: no single step holds 30% or more of it."
    # ---- rework
    loops = []
    for name, (n, cs) in selfloops.items():
        loops.append({"type": "self-loop", "activities": [name], "occurrences": n, "cases": len(cs),
                      "share_of_cases": len(cs) / n_cases})
    for (a, b), (n, cs) in back_forth.items():
        loops.append({"type": "back-and-forth", "activities": [a, b, a], "occurrences": n, "cases": len(cs),
                      "share_of_cases": len(cs) / n_cases})
    loops.sort(key=lambda r: (-r["cases"], r["activities"]))
    # ---- resources
    resources = None
    if quality["has_resource"]:
        resources = []
        for name, r in res_stats.items():
            resources.append({"resource": name, "events": r["events"], "share_of_events": r["events"] / n_events,
                              "busy_hours": r["busy"] if has_start else None,
                              "activities": [a for a, _n in r["activities"].most_common()],
                              "utilisation": (r["busy"] / ((r["last"] - r["first"]) / 3600.0))
                              if has_start and r["last"] and r["last"] > r["first"] else None})
        resources.sort(key=lambda r: (-r["events"], r["resource"]))
    warnings = []
    if n_cases < SMALL_LOG:
        warnings.append(f"Only {n_cases} cases: variant shares and waiting statistics are very uncertain.")
    if quality["dropped_total"]:
        warnings.append(f"{quality['dropped_total']} rows were dropped ({quality['dropped']}).")
    if quality["start_after_end"]:
        warnings.append(f"{quality['start_after_end']} events had a start after their end; their duration was set "
                        "to 0.")
    if overlaps:
        warnings.append(f"{overlaps} steps overlapped the previous one (parallel work); their waiting time was set "
                        "to 0.")
    if not has_start:
        warnings.append("No start column: the time before a step is the whole elapsed time until it finished "
                        "(waiting plus working), so bottlenecks mark slow steps, not only queues.")
    return {
        "cases": n_cases, "events": n_events, "activities_count": len(activities), "variants_count": len(vlist),
        "span_hours": span_h, "time_unit": TIME_UNITS, "wait_includes_work": not has_start,
        "throughput": {**stats(times), "cases_per_hour": ((n_cases - 1) / ((max(case_starts) - min(case_starts)) / 3600)
                                                          if n_cases > 1 and max(case_starts) > min(case_starts) else None)},
        "start_activities": [{"activity": a, "count": c, "share": c / n_cases} for a, c in starts.most_common()],
        "end_activities": [{"activity": a, "count": c, "share": c / n_cases} for a, c in ends.most_common()],
        "graph": {"threshold": threshold, "min_count": min_count, "edges": graph,
                  "kept_edges": sum(1 for e in graph if e["kept"]),
                  "method": "Heuristic-miner dependency (a>b - b>a)/(a>b + b>a + 1), self-loops n/(n+1); a pair is "
                            "also kept when it forms a length-two loop with the same measure on a-b-a patterns."},
        "activities": activities, "variants": vout[:max_variants], "variants_shown": min(len(vout), max_variants),
        "rework": {"loops": loops, "activities": [{"activity": a["activity"], "cases": a["repeat_cases"],
                                                   "extra_visits": a["extra_visits"]}
                                                  for a in activities if a["repeat_cases"]],
                   "cases_with_rework": sum(1 for _c, evs in cases if len({e[1] for e in evs}) < len(evs)),
                   "share_of_cases": sum(1 for _c, evs in cases if len({e[1] for e in evs}) < len(evs)) / n_cases},
        "bottlenecks": bottlenecks, "slowest_edges": top_edges, "bottleneck_verdict": verdict,
        "resources": resources, "quality": {k: v for k, v in quality.items() if k != "mapping"},
        "warnings": warnings, "caveat": CAVEAT,
        "_cases": cases,
        "_variant_ids": {tuple(v["activities"]): v["id"] for v in vout},
        "_case_times": times, "_case_starts": case_starts,
    }


# ------------------------------------------------------------------ conformance
def _norm_steps(steps: Any) -> list[dict]:
    if not isinstance(steps, list) or not steps:
        raise ProcessError("reference.steps: give an ordered list of steps")
    slots = []
    for i, s in enumerate(steps):
        if isinstance(s, str):
            slots.append({"parallel": False, "members": [{"activity": s, "optional": False}]})
        elif isinstance(s, dict) and "parallel" in s:
            mem = s["parallel"]
            if not isinstance(mem, list) or len(mem) < 2:
                raise ProcessError(f"reference.steps[{i}]: 'parallel' needs a list of at least two activities")
            opt = bool(s.get("optional", False))
            members = []
            for m in mem:
                if isinstance(m, str):
                    members.append({"activity": m, "optional": opt})
                elif isinstance(m, dict) and m.get("activity"):
                    members.append({"activity": str(m["activity"]), "optional": opt or bool(m.get("optional", False))})
                else:
                    raise ProcessError(f"reference.steps[{i}]: parallel members are names or {{activity, optional}}")
            slots.append({"parallel": True, "members": members})
        elif isinstance(s, dict) and s.get("activity"):
            slots.append({"parallel": False,
                          "members": [{"activity": str(s["activity"]), "optional": bool(s.get("optional", False))}]})
        else:
            raise ProcessError(f"reference.steps[{i}]: expected a name, {{activity, optional}} or {{parallel: [...]}}")
    seen = set()
    for sl in slots:
        for m in sl["members"]:
            if m["activity"] in seen:
                raise ProcessError(f"reference.steps: {m['activity']!r} appears twice; give each step once")
            seen.add(m["activity"])
    return slots


def _norm_reference(ref: Any) -> dict:
    if not isinstance(ref, dict):
        raise ProcessError("reference: expected {steps: [...]} or {transitions: [[a, b], ...], start?, end?}")
    if ref.get("steps") is not None:
        return {"kind": "steps", "slots": _norm_steps(ref["steps"])}
    if ref.get("transitions") is not None:
        tr = ref["transitions"]
        if not isinstance(tr, list) or not tr or not all(isinstance(x, (list, tuple)) and len(x) == 2 for x in tr):
            raise ProcessError("reference.transitions: expected a list of [from, to] pairs")
        pairs = {(str(a), str(b)) for a, b in tr}
        start = {str(x) for x in ref["start"]} if ref.get("start") else None
        end = {str(x) for x in ref["end"]} if ref.get("end") else None
        return {"kind": "transitions", "pairs": pairs, "start": start, "end": end}
    raise ProcessError("reference: give 'steps' (ordered, with optional / parallel) or 'transitions'")


def _describe_reference(r: dict) -> dict:
    if r["kind"] == "steps":
        return {"kind": "steps", "steps": [
            [m["activity"] + ("?" if m["optional"] else "") for m in s["members"]] if s["parallel"]
            else s["members"][0]["activity"] + ("?" if s["members"][0]["optional"] else "") for s in r["slots"]]}
    return {"kind": "transitions", "transitions": sorted([list(p) for p in r["pairs"]]),
            "start": sorted(r["start"]) if r["start"] else None, "end": sorted(r["end"]) if r["end"] else None}


def _check_steps(trace: tuple, r: dict) -> list[tuple[str, str]]:
    slot_of, optional = {}, {}
    for i, s in enumerate(r["slots"]):
        for m in s["members"]:
            slot_of[m["activity"]] = i
            optional[m["activity"]] = m["optional"]
    devs: list[tuple[str, str]] = []
    first: dict[str, int] = {}
    count = Counter(trace)
    for i, a in enumerate(trace):
        first.setdefault(a, i)
    for a in trace:
        if a not in slot_of:
            devs.append(("inserted", a))
    for a, k in count.items():
        if a in slot_of and k > 1:
            devs.append(("repeated", a))
    for a in slot_of:
        if not optional[a] and a not in count:
            devs.append(("skipped", a))
    present = sorted((a for a in first if a in slot_of), key=lambda a: first[a])
    for i, x in enumerate(present):
        for y in present[i + 1:]:
            if slot_of[y] < slot_of[x]:
                devs.append(("reordered", f"{x} before {y} (expected {y} first)"))
    return devs


def _shortest_between(adj: dict, a: str, b: str, limit: int = 8) -> Optional[list[str]]:
    prev = {a: None}
    dq = deque([a])
    while dq:
        x = dq.popleft()
        for y in adj.get(x, ()):
            if y in prev:
                continue
            prev[y] = x
            if y == b:
                path = [b]
                while prev[path[-1]] is not None:
                    path.append(prev[path[-1]])
                path.reverse()
                return path if len(path) <= limit else None
            dq.append(y)
    return None


def _check_transitions(trace: tuple, r: dict, adj: dict, acts: set) -> list[tuple[str, str]]:
    devs = []
    for a in trace:
        if a not in acts:
            devs.append(("inserted", a))
    if r["start"] and trace[0] in acts and trace[0] not in r["start"]:
        devs.append(("wrong start", trace[0]))
    for a, b in zip(trace, trace[1:]):
        if (a, b) in r["pairs"] or a not in acts or b not in acts:
            continue
        if (b, a) in r["pairs"]:
            devs.append(("reordered", f"{a} then {b} (expected {b} then {a})"))
            continue
        path = _shortest_between(adj, a, b)
        if path and len(path) > 2:
            devs.append(("skipped", f"between {a} and {b}: {', '.join(path[1:-1])}"))
        else:
            devs.append(("unexpected transition", f"{a} -> {b}"))
    if r["end"] and trace[-1] in acts and trace[-1] not in r["end"]:
        devs.append(("wrong end", trace[-1]))
    return devs


def conform_log(cases: list[tuple[str, list[Event]]], reference: Any, max_examples: int = 5,
                max_variants: int = 30) -> dict:
    r = _norm_reference(reference)
    adj: dict = {}
    acts: set = set()
    if r["kind"] == "transitions":
        for a, b in r["pairs"]:
            adj.setdefault(a, set()).add(b)
            acts.update((a, b))
        if r["start"]:
            acts |= r["start"]
        if r["end"]:
            acts |= r["end"]
    by_trace: dict[tuple, list[str]] = defaultdict(list)
    for cid, evs in cases:
        by_trace[tuple(e[1] for e in evs)].append(cid)
    agg: dict[tuple, dict] = {}
    per_case: dict[str, list[tuple[str, str]]] = {}
    var_rows = []
    fitting = 0
    for trace, ids in by_trace.items():
        devs = _check_steps(trace, r) if r["kind"] == "steps" else _check_transitions(trace, r, adj, acts)
        if not devs:
            fitting += len(ids)
        distinct = list(dict.fromkeys(devs))
        for d in distinct:
            e = agg.setdefault(d, {"cases": 0, "occurrences": 0, "examples": []})
            e["cases"] += len(ids)
            e["occurrences"] += len(ids) * devs.count(d)
            for cid in ids:
                if len(e["examples"]) < max_examples:
                    e["examples"].append(cid)
        var_rows.append({"activities": list(trace), "count": len(ids), "fits": not devs,
                         "deviations": [f"{k}: {v}" for k, v in distinct], "example_cases": ids[:3]})
        for cid in ids:
            per_case[cid] = distinct
    n = len(cases)
    var_rows.sort(key=lambda v: (-v["count"], v["activities"]))
    devs_out = [{"type": k, "detail": d, "cases": e["cases"], "share_of_cases": e["cases"] / n,
                 "occurrences": e["occurrences"], "examples": e["examples"]}
                for (k, d), e in agg.items()]
    devs_out.sort(key=lambda x: (-x["cases"], x["type"], x["detail"]))
    by_type = Counter()
    for (k, _d), e in agg.items():
        by_type[k] += e["cases"]
    warnings = []
    if r["kind"] == "steps":
        known = {m["activity"] for s in r["slots"] for m in s["members"]}
    else:
        known = acts
    seen = {a for tr in by_trace for a in tr}
    if not (known & seen):
        warnings.append("None of the reference activities appears in the log: check spelling and case.")
    return {"cases": n, "fitting_cases": fitting, "fitting_share": fitting / n, "deviating_cases": n - fitting,
            "deviation_types": dict(by_type), "deviations": devs_out, "variants": var_rows[:max_variants],
            "variants_total": len(var_rows), "reference": _describe_reference(r),
            "method": ("Per case: skipped = required step missing; inserted = activity not in the reference; "
                       "repeated = a reference step done more than once; reordered = steps in a different slot "
                       "order (inside a parallel group any order fits). With transitions: a forbidden transition is "
                       "labelled reordered when the reverse is allowed, skipped when allowed steps could fill "
                       "the gap, otherwise unexpected."),
            "warnings": warnings, "caveat": CAVEAT, "_per_case": per_case}


# ------------------------------------------------------------- model for the scenario engine
def _concurrency(intervals: list[tuple[float, float]]) -> int:
    ev = []
    for s, e in intervals:
        ev.append((s, 1))
        ev.append((e, -1))
    ev.sort(key=lambda x: (x[0], x[1]))  # departures before arrivals at equal times
    cur = best = 0
    for _t, d in ev:
        cur += d
        best = max(best, cur)
    return best


def build_model(cases: list[tuple[str, list[Event]]], quality: dict, replications: int = 20,
                seed: int = 1) -> tuple[dict, dict]:
    """Scenario-engine model from the log, plus a description of how it was built."""
    n = len(cases)
    if n < MIN_CASES:
        raise ProcessError(f"{n} cases are too few to build a model (need at least {MIN_CASES}).")
    has_start = quality["has_start"]
    names: list[str] = []
    dur: dict[str, list[float]] = defaultdict(list)
    spans: dict[str, list[tuple[float, float]]] = defaultdict(list)
    res: dict[str, set] = defaultdict(set)
    res_acts: dict[str, set] = defaultdict(set)
    nxt: dict[str, Counter] = defaultdict(Counter)
    start = Counter()
    visits: dict[str, int] = defaultdict(int)
    max_visits = 1
    for _cid, evs in cases:
        start[evs[0][1]] += 1
        cnt = Counter()
        for i, e in enumerate(evs):
            a = e[1]
            if a not in names:
                names.append(a)
            cnt[a] += 1
            visits[a] += 1
            if has_start:
                dur[a].append(max(0.0, (e[0] - e[2]) / 3600.0))
                spans[a].append((e[2], e[0]))
            elif i:
                dur[a].append(max(0.0, (e[0] - evs[i - 1][0]) / 3600.0))
            if e[3] is not None:
                res[a].add(e[3])
                res_acts[e[3]].add(a)
            nxt[a][evs[i + 1][1] if i + 1 < len(evs) else "END"] += 1
        max_visits = max(max_visits, max(cnt.values()))
    stations, notes, warnings = [], [], []
    for a in names:
        xs = dur.get(a) or []
        if not xs:
            xs = [0.0]
            warnings.append(f"{a!r} never has a measurable duration (it is always the first step and there is no "
                            "start column); it is modelled as instantaneous.")
        capacity = has_start
        if has_start:
            servers = len(res[a]) if res[a] else max(1, _concurrency(spans[a]))
            basis = "distinct resources" if res[a] else "maximum observed concurrency (a lower bound)"
        else:
            servers, basis = BIG, "not modelled (no start column): the step is a pure delay"
        spec = {"dist": "empirical", "values": sc.thin(xs, 500)}
        stations.append({"name": a, "servers": servers, "service": spec})
        notes.append({"activity": a, "servers": servers if capacity else None, "capacity_modelled": capacity,
                      "capacity_basis": basis, "duration_samples": len(xs),
                      "duration_mean_hours": sum(xs) / len(xs)})
        if len(xs) < SMALL_LOG:
            warnings.append(f"{a!r}: only {len(xs)} duration samples; its distribution is uncertain.")
    shared = sorted(r for r, acts_ in res_acts.items() if len(acts_) > 1)
    if shared:
        warnings.append(f"{len(shared)} resources work on several activities; each activity is modelled with its own "
                        "pool, which overstates capacity when they are shared.")
    routing = {"start": {a: c / n for a, c in start.items()},
               "next": {a: {b: c / sum(cn.values()) for b, c in cn.items()} for a, cn in nxt.items()},
               "max_visits": max(2, min(8, max_visits + 2))}
    # arrivals
    t0s = sorted(sc_ for sc_ in (c[1][0][2] if c[1][0][2] is not None else c[1][0][0] for c in cases))
    gaps = [(b - a) / 3600.0 for a, b in zip(t0s, t0s[1:])]
    span = (t0s[-1] - t0s[0]) / 3600.0
    if span <= 0:
        raise ProcessError("all cases start at the same moment: the arrival rate cannot be estimated.")
    rate = (n - 1) / span
    g = sc.describe(gaps)
    if g["cv"] is not None and 0.8 <= g["cv"] <= 1.25:
        arrivals = {"type": "poisson", "rate": rate}
        arr_note = f"Poisson at {rate:.4g} cases/hour (inter-arrival CV {g['cv']:.2f}, close to 1)"
    else:
        arrivals = {"type": "empirical", "values": sc.thin(gaps, 500)}
        arr_note = f"measured inter-arrival times resampled (CV {g['cv']:.2f}, not close to 1)" if g["cv"] is not None \
            else "measured inter-arrival times"
    horizon = min(max(span, 300 / rate), 1500 / rate)
    model = {"horizon_hours": horizon, "warmup_hours": 0.05 * horizon, "replications": replications, "seed": seed,
             "arrivals": arrivals, "stations": stations, "routing": routing, "costs": {}}
    if n < SMALL_LOG:
        warnings.append(f"Only {n} cases: the model rests on very little data.")
    if not has_start:
        warnings.append("No start column: capacity is not modelled, so adding a resource has no effect; map a start "
                        "column to rehearse capacity changes.")
    desc = {"arrivals": arr_note, "arrival_rate_per_hour": rate, "horizon_hours": horizon, "activities": notes,
            "cases_used": n, "warnings": warnings}
    return model, desc


def _norm(shares: dict) -> dict:
    tot = sum(shares.values())
    return {k: v / tot for k, v in shares.items()} if tot > 0 else dict(shares)


def _idx(model: dict, activity: str) -> int:
    for i, s in enumerate(model["stations"]):
        if s["name"] == activity:
            return i
    raise ProcessError(f"unknown activity {activity!r}; activities: {[s['name'] for s in model['stations']]}")


def describe_change(ch: dict) -> str:
    t = ch.get("type")
    if t == "add_resource":
        if ch.get("servers") is not None:
            return f"set {ch['activity']} to {ch['servers']} resources"
        return f"add {ch.get('count', 1)} resource(s) at {ch['activity']}"
    if t == "remove_step":
        return f"remove step {ch['activity']}"
    if t == "set_routing":
        return f"send {ch['share'] * 100:.0f}% of {ch['from']} to {ch['to']}"
    if t == "scale_duration":
        return f"scale {ch['activity']} durations by {ch['factor']}"
    return str(t)


def apply_change(model: dict, ch: dict) -> None:
    """Edit the model in place for one change."""
    if not isinstance(ch, dict) or "type" not in ch:
        raise ProcessError("change: expected {type: add_resource|remove_step|set_routing|scale_duration, ...}")
    t = ch["type"]
    if t == "add_resource":
        i = _idx(model, ch.get("activity"))
        st = model["stations"][i]
        if st["servers"] >= BIG:
            raise ProcessError(f"capacity of {st['name']!r} is not modelled (no start column in the log), so adding a "
                               "resource would change nothing; map a start column.")
        if ch.get("servers") is not None:
            new = int(ch["servers"])
        else:
            new = st["servers"] + int(ch.get("count", 1))
        if new < 1:
            raise ProcessError(f"add_resource: {st['name']!r} would have {new} resources; need at least 1")
        st["servers"] = new
    elif t == "scale_duration":
        i = _idx(model, ch.get("activity"))
        try:
            f = float(ch.get("factor"))
        except (TypeError, ValueError):
            raise ProcessError("scale_duration: 'factor' must be a number") from None
        if not f > 0 or not math.isfinite(f):
            raise ProcessError("scale_duration: factor must be greater than 0 (0.7 = 30% shorter)")
        spec = model["stations"][i]["service"]
        spec["values"] = [v * f for v in spec["values"]]
    elif t == "set_routing":
        a, b = ch.get("from"), ch.get("to")
        _idx(model, a), _idx(model, b if b != "END" else a)
        try:
            share = float(ch.get("share"))
        except (TypeError, ValueError):
            raise ProcessError("set_routing: 'share' must be a number between 0 and 1") from None
        if not 0 <= share <= 1:
            raise ProcessError("set_routing: 'share' must be between 0 and 1")
        cur = _norm(dict(model["routing"]["next"].get(a, {"END": 1.0})))
        others = {k: v for k, v in cur.items() if k != b}
        rest = sum(others.values())
        if rest <= 0 and share < 1:
            raise ProcessError(f"set_routing: {a!r} has no other destination to take the remaining {1 - share:.0%}")
        new = {k: v / rest * (1 - share) for k, v in others.items()} if rest > 0 else {}
        new[b] = share
        model["routing"]["next"][a] = new
    elif t == "remove_step":
        x = ch.get("activity")
        _idx(model, x)
        nx = _norm(dict(model["routing"]["next"].get(x, {"END": 1.0})))
        selfp = nx.get(x, 0.0)
        if selfp >= 1:
            raise ProcessError(f"remove_step: {x!r} only loops to itself")
        succ = {y: p / (1 - selfp) for y, p in nx.items() if y != x}

        def redirect(shares: dict) -> dict:
            shares = _norm(dict(shares))
            w = shares.pop(x, 0.0)
            for y, p in succ.items():
                shares[y] = shares.get(y, 0.0) + w * p
            return shares
        start = redirect(model["routing"]["start"])
        if not start or sum(start.values()) <= 0 or set(start) <= {"END"}:
            raise ProcessError(f"remove_step: removing {x!r} leaves no step to start with")
        model["routing"]["start"] = {k: v for k, v in start.items() if k != "END"}
        for p in list(model["routing"]["next"]):
            if p == x:
                continue
            model["routing"]["next"][p] = redirect(model["routing"]["next"][p])
        model["routing"]["next"].pop(x, None)
    else:
        raise ProcessError(f"unknown change type {t!r}; use add_resource, remove_step, set_routing or scale_duration")


def variant_overrides(base: dict, changes: list[dict]) -> dict:
    if not isinstance(changes, list) or not changes:
        raise ProcessError("each variant needs a non-empty 'changes' list")
    m = copy.deepcopy(base)
    for ch in changes:
        apply_change(m, ch)
    return {"stations": m["stations"], "routing": m["routing"]}


def validate_baseline(cases_times: list[float], arrival_rate: float, run: dict, tolerance: float) -> dict:
    obs = stats(cases_times)
    sim_mean = run["metrics"]["lead_time_mean"]
    sim_p50 = run["metrics"]["lead_time_p50"]
    sim_p90 = run["metrics"]["lead_time_p90"]
    sim_x = run["metrics"]["throughput"]

    def row(name, o, m, tol):
        if o in (None, 0) or m["mean"] is None:
            return {"metric": name, "observed": o, "simulated": m["mean"], "relative_difference": None, "ok": False,
                    "tolerance": tol, "simulated_ci": [m["ci_low"], m["ci_high"]]}
        rel = abs(m["mean"] - o) / abs(o)
        return {"metric": name, "observed": o, "simulated": m["mean"], "relative_difference": rel, "ok": rel <= tol,
                "tolerance": tol, "simulated_ci": [m["ci_low"], m["ci_high"]],
                "observed_inside_ci": (m["ci_low"] <= o <= m["ci_high"]) if m["ci_low"] is not None else None}
    rows = [row("throughput_time_mean_hours", obs["mean"], sim_mean, tolerance),
            row("throughput_time_median_hours", obs["median"], sim_p50, 2 * tolerance),
            row("throughput_time_p90_hours", obs["p90"], sim_p90, 2 * tolerance),
            row("cases_per_hour", arrival_rate, sim_x, tolerance)]
    ok = rows[0]["ok"] and rows[2]["ok"] and rows[3]["ok"]
    note = ("The baseline model reproduces the observed throughput time within tolerance, so its what-ifs are worth "
            "reading." if ok else
            "The baseline model does NOT reproduce the observed log within tolerance: it is not representative "
            "(typical causes: a step that depends on previous steps, work calendars, shared resources, or too "
            "little data). Treat the rehearsal results as illustrative only.")
    return {"representative": ok, "tolerance": tolerance, "rows": rows, "note": note}


# ----------------------------------------------------------------- services layer
def _case_rows(res: dict) -> list[list]:
    vid = res["_variant_ids"]
    rows = []
    for (cid, evs), tt in zip(res["_cases"], res["_case_times"]):
        trace = tuple(e[1] for e in evs)
        rows.append([cid, _iso(case_span(evs)[0]), _iso(case_span(evs)[1]), tt, len(evs), vid.get(trace)])
    return rows


def _strip(res: dict) -> dict:
    return {k: v for k, v in res.items() if not k.startswith("_")}


def discover_process(services, dataset: str, mapping: dict, threshold: float = 0.5, min_count: int = 1,
                     max_variants: int = 20, save_as: Optional[str] = None, source: str = "ui") -> dict:
    def do():
        if not 0 <= threshold <= 1:
            raise ProcessError("threshold must be between 0 and 1")
        cases, quality = load_log(services, dataset, mapping)
        res = discover_log(cases, quality, threshold, max(1, int(min_count)), max(1, int(max_variants)))
        if save_as:
            res["saved_datasets"] = {
                "edges": sc._save_rows(services, f"{save_as}_edges",
                                       ["from", "to", "count", "cases", "dependency", "kept", "wait_mean_hours",
                                        "wait_median_hours", "wait_p90_hours"],
                                       [[e["from"], e["to"], e["count"], e["cases"], e["dependency"], e["kept"],
                                         e["wait_mean_hours"], e["wait_median_hours"], e["wait_p90_hours"]]
                                        for e in res["graph"]["edges"]], source),
                "variants": sc._save_rows(services, f"{save_as}_variants",
                                          ["variant", "activities", "count", "share", "throughput_mean_hours"],
                                          [[v["id"], " > ".join(v["activities"]), v["count"], v["share"],
                                            v["throughput_mean_hours"]] for v in _all_variants(res, cases)], source),
                "cases": sc._save_rows(services, f"{save_as}_cases",
                                       ["case", "start", "end", "throughput_hours", "events", "variant"],
                                       _case_rows(res), source)}
        out = _strip(res)
        out["dataset"] = dataset
        out["mapping"] = quality["mapping"]
        m = out["throughput"]
        out["_log_summary"] = (f"discovered process in {dataset}: {out['cases']} cases, {out['variants_count']} variants,"
                               f" mean throughput {m['mean']:.2f} h")
        return out
    return services._log("process_discover", source, dataset,
                         {"mapping": mapping, "threshold": threshold, "min_count": min_count}, do)


def _all_variants(res: dict, cases) -> list[dict]:
    """The saved table keeps every variant, not only the displayed ones."""
    ids = res["_variant_ids"]
    agg: dict[tuple, list] = {}
    for (cid, evs), tt in zip(cases, res["_case_times"]):
        tr = tuple(e[1] for e in evs)
        a = agg.setdefault(tr, [0, 0.0])
        a[0] += 1
        a[1] += tt
    n = len(cases)
    return sorted(({"id": ids[t], "activities": list(t), "count": c, "share": c / n, "throughput_mean_hours": s / c}
                   for t, (c, s) in agg.items()), key=lambda v: v["id"])


def conform_process(services, dataset: str, mapping: dict, reference: dict, max_examples: int = 5,
                    save_as: Optional[str] = None, source: str = "ui") -> dict:
    def do():
        cases, quality = load_log(services, dataset, mapping)
        res = conform_log(cases, reference, max(1, int(max_examples)))
        per = res.pop("_per_case")
        if save_as:
            res["saved_dataset"] = sc._save_rows(
                services, save_as, ["case", "fits", "deviations"],
                [[cid, not per[cid], "; ".join(f"{k}: {d}" for k, d in per[cid])] for cid, _ in cases], source)
        res["dataset"] = dataset
        res["quality"] = {k: v for k, v in quality.items() if k != "mapping"}
        if quality["dropped_total"]:
            res["warnings"].append(f"{quality['dropped_total']} rows were dropped ({quality['dropped']}).")
        res["_log_summary"] = (f"conformance in {dataset}: {res['fitting_cases']}/{res['cases']} cases fit, "
                               f"{len(res['deviations'])} kinds of deviation")
        return res
    return services._log("process_conform", source, dataset, {"mapping": mapping, "reference": reference}, do)


HEADLINE = ("lead_time_mean", "lead_time_p50", "lead_time_p90", "throughput", "wip")


def rehearse_process(services, dataset: str, mapping: dict, variants: Optional[list[dict]] = None,
                     replications: int = 20, seed: int = 1, tolerance: float = 0.15,
                     save_as: Optional[str] = None, model_name: Optional[str] = None, source: str = "ui") -> dict:
    def do():
        if not 1 <= int(replications) <= 100:
            raise ProcessError("replications must be between 1 and 100")
        if not 0 < tolerance < 1:
            raise ProcessError("tolerance must be between 0 and 1 (0.15 = 15%)")
        cases, quality = load_log(services, dataset, mapping)
        model, desc = build_model(cases, quality, int(replications), int(seed))
        times = [case_span(evs)[1] - case_span(evs)[0] for _c, evs in cases]
        times = [t / 3600.0 for t in times]
        vlist = []
        names = set()
        for i, v in enumerate(variants or []):
            if not isinstance(v, dict):
                raise ProcessError("variants: expected [{name, changes: [...]}]")
            name = str(v.get("name") or f"variant {i + 1}")
            if name in names:
                raise ProcessError(f"variant names must be unique ({name!r})")
            names.add(name)
            ch = v.get("changes")
            if not isinstance(ch, list) or not ch:
                raise ProcessError(f"variant {name!r}: give a non-empty 'changes' list")
            vlist.append({"name": name, "overrides": variant_overrides(model, ch),
                          "changes": [describe_change(c) for c in ch], "raw": ch})
        try:
            if vlist:
                cmp_ = sc.compare_models(model, [{"name": v["name"], "overrides": v["overrides"]} for v in vlist],
                                         int(replications), int(seed))
                base_run = cmp_["base"]
            else:
                cmp_ = None
                base_run = sc._public(sc.run_model(model, int(replications), int(seed)))
        except sc.ScenarioError as exc:
            raise ProcessError(f"the discovered model could not be simulated: {exc}") from None
        validation = validate_baseline(times, desc["arrival_rate_per_hour"], base_run, float(tolerance))
        warnings = list(desc["warnings"]) + list(base_run["warnings"])
        if not validation["representative"]:
            warnings.insert(0, validation["note"])
        outs = []
        for v, cv in zip(vlist, (cmp_["variants"] if cmp_ else [])):
            touched = {c.get("activity") or c.get("from") for c in v["raw"]}
            keep = [r for r in cv["rows"] if r["metric"] in HEADLINE or
                    (":" in r["metric"] and r["metric"].split(":", 1)[0] in ("wait", "util", "queue")
                     and r["metric"].split(":", 1)[1] in touched)]
            head = next(r for r in cv["rows"] if r["metric"] == "lead_time_mean")
            if head["distinguishable"]:
                direction = "shorter" if head["delta"] < 0 else "longer"
                summary = (f"Mean throughput time {direction}: {head['base']:.2f} h -> {head['variant']:.2f} h "
                           f"({head['delta_pct']:+.0f}%, paired 95% interval [{head['delta_ci_low']:.2f}, "
                           f"{head['delta_ci_high']:.2f}] h)")
            else:
                summary = (f"Mean throughput time is not distinguishable from the baseline "
                           f"(paired 95% interval of the difference includes 0)")
            outs.append({"name": v["name"], "changes": v["changes"], "rows": keep, "summary": summary,
                         "headline": {"metric": "lead_time_mean", "base": head["base"], "variant": head["variant"],
                                      "delta": head["delta"], "delta_pct": head["delta_pct"],
                                      "ci_low": head["delta_ci_low"], "ci_high": head["delta_ci_high"],
                                      "distinguishable": head["distinguishable"], "better": head["better"]},
                         "warnings": cv["warnings"]})
        res = {
            "dataset": dataset, "mapping": quality["mapping"], "model": {
                "arrivals": desc["arrivals"], "arrival_rate_per_hour": desc["arrival_rate_per_hour"],
                "horizon_hours": desc["horizon_hours"], "replications": int(replications), "seed": int(seed),
                "activities": desc["activities"], "cases_used": desc["cases_used"],
                "routing": {"start": model["routing"]["start"], "next": model["routing"]["next"]}},
            "baseline": {"metrics": {k: base_run["metrics"][k] for k in HEADLINE if k in base_run["metrics"]},
                         "stations": base_run["stations"], "checks": base_run["checks"]},
            "observed": {**stats(times), "cases_per_hour": desc["arrival_rate_per_hour"]},
            "validation": validation, "variants": outs, "warnings": warnings,
            "method": ("The log gives an empirical routing chain, per-step duration samples, resource pools and "
                       "arrival times; the scenario engine simulates it over replications. Variants share the seed "
                       "(common random numbers), so differences are paired by replication; 'distinguishable' means "
                       "their 95% interval excludes 0."),
            "caveat": CAVEAT}
        if model_name:
            res["scenario_model"] = sc.save_model(services, model_name, model, source=source)["name"]
        if save_as:
            rows = [["baseline", k, m["mean"], m["ci_low"], m["ci_high"], None, None, None, None]
                    for k, m in base_run["metrics"].items()]
            for v in (cmp_["variants"] if cmp_ else []):
                rows += [[v["name"], r["metric"], r["variant"], None, None, r["delta"], r["delta_ci_low"],
                          r["delta_ci_high"], r["distinguishable"]] for r in v["rows"]]
            res["saved_dataset"] = sc._save_rows(
                services, save_as, ["scenario", "metric", "mean", "ci_low", "ci_high", "delta_vs_baseline",
                                    "delta_ci_low", "delta_ci_high", "distinguishable"], rows, source)
        rep = "representative" if validation["representative"] else "NOT representative"
        res["_log_summary"] = (f"rehearsal on {dataset}: baseline {rep}, {len(outs)} variants, "
                               f"{desc['cases_used']} cases")
        return res
    return services._log("process_rehearse", source, dataset,
                         {"mapping": mapping, "variants": [v.get("name") for v in variants or []]}, do)

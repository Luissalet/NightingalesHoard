"""Event-log mapping, checks and profile, and the bridge from a profile to the scenario lab.

A dataset becomes an event log through an explicit mapping: case, activity and timestamp columns, plus optional
resource, cost and either a start column (one row per activity instance with its start and end) or a lifecycle
column (one row per transition, ``start`` / ``complete``). The dataset itself is only read, never changed.

Loading keeps every decision visible:

* timestamps are parsed in Python from their text form so that UTC offsets survive: naive values are read in the
  declared zone (default UTC), values with an offset keep it; everything is normalised to UTC, and the offsets
  seen, the cases whose offset changes and the local times that do not exist or are ambiguous in the declared zone
  (daylight-saving gaps and repeats) are reported;
* every event keeps its source row (1-based position in the dataset version);
* ordering is deterministic: raw events by (timestamp, lifecycle start before complete, source row); activity
  instances by completion time, then source row. Instances that complete at the same moment in a case are flagged
  as ties: their relative order is not inferred, and the graph edges that depend on it are counted;
* exact duplicates are removed and listed, unreadable timestamps and rows without case or activity are excluded and
  listed, lifecycle starts without a completion and completions without a start are listed;
* a case is closed when its last activity is a declared end activity (``end_activities``, or the last step of the
  declared ``expected`` sequence); other cases are open (censored). Open cases are counted and listed but kept out
  of the time statistics and of model fitting. Without a declaration no case can be called open, and the profile
  says so and proposes the most frequent last activity as a candidate;
* with ``expected`` (the reference sequence of steps) every closed case is checked for missing steps.

The profile (``profile_log``) is descriptive: variants, the directly-follows graph with frequencies and coverage,
observed times with sample size and percentiles, observed bottlenecks and an explainable list of deviations. With
start and completion times, service time and waiting are separated; without them the time between completions is
reported as elapsed time (waiting plus work, not separable) and the missing data is named. A directly-follows
count does not prove that one step causes the next, and it is not a full concurrency model.

``calibrate_log`` turns the cases that started before a split moment into a reviewable scenario-lab model (every
parameter with its provenance: events used, sample size and estimator, or "not identifiable" with the data that
would make it so), simulates it with the existing engine (nightingale/scenarios.py) and compares it with the cases
held out after the split. The verdict can be a failure and is shown as such. Alternatives are compared with the
calibrated baseline using the same seeds and replications (common random numbers) and 95% intervals.

All durations are in hours; all instants are UTC.
"""
from __future__ import annotations

import json
import math
import re
from collections import Counter, defaultdict
from datetime import datetime, timedelta, timezone
from typing import Any, Optional
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

from . import process_mining as pm
from . import scenarios as sc
from .texts import t as tx

ProcessError = pm.ProcessError

RECIPE_KIND = "nightingale.process_recipe"
RECIPE_SCHEMA = 1
MAX_LIST = 50          # rows listed per diagnostic category (the totals are always complete)
LC_START = frozenset({"start", "started", "begin", "begun", "inicio", "iniciado", "comienzo", "empieza", "assign"})
LC_COMPLETE = frozenset({"complete", "completed", "end", "ended", "finish", "finished", "done", "fin", "final",
                         "completado", "terminado", "terminada", "completada"})
DAYFIRST = ("%d/%m/%Y %H:%M:%S", "%d/%m/%Y %H:%M", "%d/%m/%Y", "%d-%m-%Y %H:%M:%S", "%d-%m-%Y %H:%M",
            "%d.%m.%Y %H:%M:%S", "%d.%m.%Y %H:%M")
DEFAULT_TOLERANCES = {"cycle_time_mean_hours": 0.20, "cycle_time_p50_hours": 0.25, "cycle_time_p90_hours": 0.30,
                      "cases_per_hour": 0.20, "bottleneck_wait_mean_hours": 0.40}
MIN_TEST_CASES = 5


# --------------------------------------------------------------------------------------------- statistics
def pstats(xs: list[float]) -> dict:
    """Sample size, mean and percentiles (linear interpolation between order statistics)."""
    if not xs:
        return {"n": 0, "mean": None, "min": None, "p10": None, "p25": None, "p50": None, "p75": None, "p90": None,
                "p95": None, "max": None, "total": 0.0}
    s = sorted(xs)
    out = {"n": len(s), "mean": sum(s) / len(s), "min": s[0], "max": s[-1], "total": sum(s)}
    for p in (10, 25, 50, 75, 90, 95):
        out[f"p{p}"] = sc.percentile(s, p / 100)
    return out


def _iso(epoch: Optional[float]) -> Optional[str]:
    if epoch is None:
        return None
    return datetime.fromtimestamp(epoch, tz=timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def _offset_text(off: Optional[timedelta]) -> str:
    if off is None:
        return "naive"
    mins = int(off.total_seconds() // 60)
    sign = "+" if mins >= 0 else "-"
    mins = abs(mins)
    return f"{sign}{mins // 60:02d}:{mins % 60:02d}"


# ------------------------------------------------------------------------------------------------ options
def norm_options(options: Optional[dict]) -> dict:
    o = dict(options or {})
    out: dict[str, Any] = {}
    zone = o.get("timezone") or "UTC"
    try:
        tz = timezone.utc if str(zone).upper() in ("UTC", "Z") else ZoneInfo(str(zone))
    except (ZoneInfoNotFoundError, ValueError):
        m = re.fullmatch(r"([+-])(\d{2}):?(\d{2})", str(zone))
        if not m:
            raise ProcessError(tx("el.bad_zone", zone=zone)) from None
        delta = timedelta(hours=int(m.group(2)), minutes=int(m.group(3)))
        tz = timezone(delta if m.group(1) == "+" else -delta)
    out["timezone"] = str(zone)
    out["_tz"] = tz
    fmt = o.get("timestamp_format")
    out["timestamp_format"] = str(fmt) if fmt else None
    for key in ("end_activities", "expected"):
        v = o.get(key)
        if v in (None, "", []):
            out[key] = None
            continue
        if isinstance(v, str):
            v = [x.strip() for x in v.split(",") if x.strip()]
        if not isinstance(v, list) or not all(isinstance(x, str) and x for x in v):
            raise ProcessError(tx("el.bad_list", key=key))
        out[key] = list(v)
    lv = o.get("lifecycle_values") or {}
    if not isinstance(lv, dict):
        raise ProcessError(tx("el.bad_lifecycle_values"))
    out["lifecycle_values"] = {"start": sorted({str(x).strip().lower() for x in lv.get("start", [])} or LC_START),
                               "complete": sorted({str(x).strip().lower() for x in lv.get("complete", [])}
                                                  or LC_COMPLETE)}
    return out


def public_options(opts: dict) -> dict:
    return {k: v for k, v in opts.items() if not k.startswith("_")}


# --------------------------------------------------------------------------------------------- timestamps
def parse_timestamp(raw: Any, tz, fmt: Optional[str] = None) -> tuple[Optional[float], dict]:
    """(epoch seconds or None, info). info: offset ('naive' when read in the declared zone), format, problem."""
    if raw is None:
        return None, {"problem": "missing"}
    s = str(raw).strip()
    if not s:
        return None, {"problem": "missing"}
    dt = None
    used = None
    if fmt:
        try:
            dt, used = datetime.strptime(s, fmt), "declared"
        except ValueError:
            return None, {"problem": "unparseable", "raw": s}
    else:
        iso = s[:-1] + "+00:00" if s.endswith(("Z", "z")) else s
        try:
            dt, used = datetime.fromisoformat(iso), "iso"
        except ValueError:
            for f in DAYFIRST:
                try:
                    dt, used = datetime.strptime(s, f), "day-first"
                    break
                except ValueError:
                    continue
    if dt is None:
        return None, {"problem": "unparseable", "raw": s}
    info: dict[str, Any] = {"format": used}
    if len(s) <= 10 and ":" not in s:
        info["date_only"] = True
    if dt.tzinfo is not None:
        info["offset"] = _offset_text(dt.utcoffset())
        return dt.timestamp(), info
    info["offset"] = "naive"
    if tz is timezone.utc or isinstance(tz, timezone):
        return dt.replace(tzinfo=tz).timestamp(), info
    a, b = dt.replace(tzinfo=tz, fold=0), dt.replace(tzinfo=tz, fold=1)
    back = a.astimezone(timezone.utc).astimezone(tz).replace(tzinfo=None)
    if back != dt:
        return None, {"problem": "nonexistent", "raw": s}
    if a.utcoffset() != b.utcoffset():
        info["ambiguous"] = True       # repeated local hour: the first occurrence is used, and it is reported
    return a.timestamp(), info


# ------------------------------------------------------------------------------------------------ loading
def _check_mapping(cols: list[str], mapping: dict) -> dict:
    m = dict(mapping or {})
    lc = m.pop("lifecycle", None)
    base = pm._check_mapping(cols, {k: v for k, v in m.items() if k in ("case", "activity", "timestamp", "start",
                                                                          "resource", "cost")})
    if lc:
        if lc not in cols:
            raise ProcessError(tx("pm.map_notfound", col=lc, key="lifecycle", cols=cols))
        if "start" in base:
            raise ProcessError(tx("el.start_and_lifecycle"))
        if lc in (base["case"], base["activity"], base["timestamp"]):
            raise ProcessError(tx("pm.map_distinct"))
        base["lifecycle"] = lc
    return base


def _rowid_sql(conn, table: str) -> str:
    try:
        conn.execute(f"SELECT rowid FROM {pm._q(table)} LIMIT 1").fetchall()
        return "rowid"
    except Exception:  # noqa: BLE001 - views have no rowid
        return "row_number() OVER () - 1"


def _limited(items: list, total: Optional[int] = None) -> dict:
    return {"total": len(items) if total is None else total, "items": items[:MAX_LIST]}


def load(services, dataset: str, mapping: dict, options: Optional[dict] = None) -> dict:
    """Read the mapped columns (never writing) and build the checked event log."""
    opts = norm_options(options)
    table, typed = pm._table_columns(services, dataset)
    types = dict(typed)
    m = _check_mapping([c for c, _ in typed], mapping)

    def time_expr(col: str) -> str:
        if "WITH TIME ZONE" in types[col].upper():
            return f"epoch({pm._q(col)})"
        return f"CAST({pm._q(col)} AS VARCHAR)"
    with services.engine.lock() as conn:
        total = conn.execute(f"SELECT count(*) FROM {pm._q(table)}").fetchone()[0]
        if total > pm.MAX_EVENTS:
            raise ProcessError(tx("pm.too_many_rows", total=total, limit=pm.MAX_EVENTS))
        rid = _rowid_sql(conn, table)
        sel = [rid, f"CAST({pm._q(m['case'])} AS VARCHAR)", f"CAST({pm._q(m['activity'])} AS VARCHAR)",
               time_expr(m["timestamp"]), time_expr(m["start"]) if "start" in m else "NULL",
               f"CAST({pm._q(m['resource'])} AS VARCHAR)" if "resource" in m else "NULL",
               f"TRY_CAST({pm._q(m['cost'])} AS DOUBLE)" if "cost" in m else "NULL",
               f"CAST({pm._q(m['lifecycle'])} AS VARCHAR)" if "lifecycle" in m else "NULL"]
        rows = conn.execute(f"SELECT {', '.join(sel)} FROM {pm._q(table)} ORDER BY 1").fetchall()
    tz_cols = {k for k in ("timestamp", "start") if k in m and "WITH TIME ZONE" in types[m[k]].upper()}
    return build_log(rows, m, opts, total, tz_cols, dataset)


def build_log(rows: list[tuple], m: dict, opts: dict, total: int, tz_cols: set = frozenset(),
              dataset: Optional[str] = None) -> dict:
    """rows: (rowid, case, activity, timestamp, start, resource, cost, lifecycle) as read from the dataset."""
    tz, fmt = opts["_tz"], opts["timestamp_format"]
    lc_start, lc_complete = set(opts["lifecycle_values"]["start"]), set(opts["lifecycle_values"]["complete"])
    mode = "lifecycle" if "lifecycle" in m else ("interval" if "start" in m else "completion")
    excluded = {"missing_case_or_activity": [], "invalid_timestamp": [], "invalid_start": [], "unknown_lifecycle": []}
    dropped = Counter()
    offsets = Counter()
    formats = Counter()
    ambiguous, date_only = [], 0
    case_offsets: dict[str, Counter] = defaultdict(Counter)
    raw_events = []   # (case, activity, epoch, start epoch|None, lc 0/1/None, resource, cost, row)

    def when(v, col_key):
        if col_key in tz_cols:
            if v is None:
                return None, {"problem": "missing"}
            return float(v), {"offset": "ingest", "format": "typed"}
        return parse_timestamp(v, tz, fmt)
    for rowid, cid, act, ts, st, res, cost, lc in rows:
        row = int(rowid) + 1
        if cid is None or act is None or not str(cid).strip() or not str(act).strip():
            excluded["missing_case_or_activity"].append({"row": row, "case": cid, "activity": act})
            dropped["missing case or activity"] += 1
            continue
        t, info = when(ts, "timestamp")
        if t is None:
            excluded["invalid_timestamp"].append({"row": row, "case": cid, "activity": act,
                                                  "value": None if ts is None else str(ts), "reason": info["problem"]})
            dropped["missing or unreadable timestamp"] += 1
            continue
        s_epoch = None
        if mode == "interval":
            s_epoch, sinfo = when(st, "start")
            if s_epoch is None:
                excluded["invalid_start"].append({"row": row, "case": cid, "activity": act,
                                                  "value": None if st is None else str(st), "reason": sinfo["problem"]})
                dropped["missing or unreadable start"] += 1
                continue
            if sinfo.get("offset") not in (None, "naive", "ingest"):
                case_offsets[cid][sinfo["offset"]] += 1
        lcv = None
        if mode == "lifecycle":
            key = str(lc).strip().lower() if lc is not None else ""
            if key in lc_start:
                lcv = 0
            elif key in lc_complete:
                lcv = 1
            else:
                excluded["unknown_lifecycle"].append({"row": row, "case": cid, "activity": act, "value": lc})
                dropped["unknown lifecycle value"] += 1
                continue
        off = info.get("offset")
        offsets[off] += 1
        formats[info.get("format")] += 1
        if off not in ("naive", "ingest"):
            case_offsets[cid][off] += 1
        if info.get("ambiguous"):
            ambiguous.append({"row": row, "case": cid, "value": str(ts)})
        if info.get("date_only"):
            date_only += 1
        raw_events.append((cid, act, t, s_epoch, lcv, res, pm._f(cost), row))
    # ---- exact duplicates (same case, activity, instants, lifecycle, resource and cost)
    seen: dict[tuple, int] = {}
    duplicates = []
    unique = []
    for ev in raw_events:
        key = ev[:7]
        if key in seen:
            duplicates.append({"row": ev[7], "duplicate_of": seen[key], "case": ev[0], "activity": ev[1],
                               "at": _iso(ev[2])})
            continue
        seen[key] = ev[7]
        unique.append(ev)
    # ---- activity instances: (end, activity, start, resource, cost, meta)
    by_case: dict[str, list] = defaultdict(list)
    start_after_end = []
    no_start, no_complete, res_mismatch = [], [], []
    in_progress: set = set()
    if mode == "lifecycle":
        groups: dict[tuple, list] = defaultdict(list)
        for ev in unique:
            groups[(ev[0], ev[1])].append(ev)
        for (cid, act), evs in groups.items():
            evs.sort(key=lambda e: (e[2], e[4], e[7]))      # timestamp, start before complete, source row
            queue: list = []
            for e in evs:
                if e[4] == 0:
                    queue.append(e)
                    continue
                if queue:
                    s = queue.pop(0)
                    if s[5] is not None and e[5] is not None and s[5] != e[5]:
                        res_mismatch.append({"case": cid, "activity": act, "rows": [s[7], e[7]],
                                             "start_resource": s[5], "complete_resource": e[5]})
                    by_case[cid].append((e[2], act, s[2], e[5] if e[5] is not None else s[5],
                                         e[6] if e[6] is not None else s[6], {"rows": [s[7], e[7]]}))
                else:
                    no_start.append({"row": e[7], "case": cid, "activity": act, "at": _iso(e[2])})
                    by_case[cid].append((e[2], act, None, e[5], e[6], {"rows": [e[7]]}))
            for s in queue:
                no_complete.append({"row": s[7], "case": cid, "activity": act, "started": _iso(s[2])})
                in_progress.add(cid)
                by_case.setdefault(cid, [])
    else:
        for cid, act, t, st, _lc, res, cost, row in unique:
            if st is not None and st > t:
                start_after_end.append({"row": row, "case": cid, "activity": act, "start": _iso(st), "end": _iso(t)})
                st = t
            by_case[cid].append((t, act, st, res, cost, {"rows": [row]}))
    # ---- order inside each case, ties
    ties = []
    cases: list[tuple[str, list]] = []
    for cid in sorted(by_case):
        evs = sorted(by_case[cid], key=lambda e: (e[0], e[5]["rows"][-1]))
        i = 0
        while i < len(evs):
            j = i
            while j + 1 < len(evs) and evs[j + 1][0] == evs[i][0]:
                j += 1
            if j > i:
                for k in range(i, j + 1):
                    evs[k][5]["tie"] = True
                ties.append({"case": cid, "at": _iso(evs[i][0]), "activities": [e[1] for e in evs[i:j + 1]],
                             "rows": [e[5]["rows"][-1] for e in evs[i:j + 1]]})
            i = j + 1
        if evs:
            cases.append((cid, evs))
    if not cases:
        raise ProcessError(tx("pm.no_events"))
    # ---- zones
    zone_changes = [{"case": cid, "offsets": sorted(c)} for cid, c in sorted(case_offsets.items())
                    if len([o for o in c if o]) > 1]
    # ---- open / closed
    end_set = set(opts["end_activities"]) if opts["end_activities"] else (
        {opts["expected"][-1]} if opts["expected"] else None)
    status: dict[str, str] = {}
    open_cases = []
    for cid, evs in cases:
        if end_set is not None:
            closed = evs[-1][1] in end_set
        else:
            closed = cid not in in_progress
        status[cid] = "closed" if closed else "open"
        if not closed:
            open_cases.append({"case": cid, "last_activity": evs[-1][1], "last_at": _iso(evs[-1][0]),
                               "in_progress": cid in in_progress, "events": len(evs)})
    last_counts = Counter(evs[-1][1] for _c, evs in cases)
    # ---- missing steps against the expected sequence (closed cases only; open ones are still running)
    missing = []
    if opts["expected"]:
        per_act: dict[str, list] = defaultdict(list)
        for cid, evs in cases:
            if status[cid] != "closed":
                continue
            have = {e[1] for e in evs}
            for a in opts["expected"]:
                if a not in have:
                    per_act[a].append(cid)
        for a in opts["expected"]:
            if per_act.get(a):
                missing.append({"activity": a, "cases": len(per_act[a]), "examples": per_act[a][:5]})
    has_start = all(e[2] is not None for _c, evs in cases for e in evs)
    n_inst = sum(len(evs) for _c, evs in cases)
    quality = {"rows": total, "events_used": n_inst, "cases": len(cases), "dropped": dict(dropped),
               "dropped_total": sum(dropped.values()), "start_after_end": len(start_after_end),
               "has_start": has_start, "has_resource": "resource" in m, "has_cost": "cost" in m,
               "has_lifecycle": mode == "lifecycle", "mode": mode, "mapping": m,
               "checks": {"duplicates_removed": len(duplicates), "ties": len(ties),
                          "complete_without_start": len(no_start), "start_without_complete": len(no_complete),
                          "zone_changes": len(zone_changes), "open_cases": len(open_cases) if end_set is not None
                          else None}}
    diag = {
        "mode": mode, "rows_read": total, "raw_events": len(raw_events), "instances": n_inst,
        "ordering": tx("el.ordering"),
        "excluded": {k: _limited(v) for k, v in excluded.items()},
        "duplicates": _limited(duplicates),
        "ties": _limited(ties),
        "start_after_end": _limited(start_after_end),
        "complete_without_start": _limited(no_start),
        "start_without_complete": _limited(no_complete),
        "resource_mismatch": _limited(res_mismatch),
        "timezone": {"declared": opts["timezone"], "offsets": {k: v for k, v in offsets.items()},
                     "mixed_offsets": len([o for o in offsets if o not in ("naive",)]) > 1 or (
                         "naive" in offsets and len(offsets) > 1),
                     "zone_changes": _limited(zone_changes), "ambiguous_local_times": _limited(ambiguous),
                     "formats": {k: v for k, v in formats.items() if k}, "date_only_values": date_only,
                     "normalised_to": "UTC",
                     "note": tx("el.zone_note_ingest") if tz_cols else tx("el.zone_note", zone=opts["timezone"])},
        "end_activities": sorted(end_set) if end_set is not None else None,
        "end_declared": end_set is not None,
        "proposed_end_activities": None if end_set is not None else [a for a, _n in last_counts.most_common(1)],
        "last_activity_counts": dict(last_counts.most_common()),
        "open_cases": _limited(open_cases),
        "closed_cases": sum(1 for s in status.values() if s == "closed"),
        "missing_steps": missing,
    }
    return {"cases": cases, "status": status, "quality": quality, "diag": diag, "options": opts, "dataset": dataset}


def closed_cases(log: dict) -> list[tuple[str, list]]:
    return [(cid, evs) for cid, evs in log["cases"] if log["status"][cid] == "closed"]


def subset_quality(log: dict, cases: list) -> dict:
    q = dict(log["quality"])
    q["cases"] = len(cases)
    q["events_used"] = sum(len(evs) for _c, evs in cases)
    q["has_start"] = bool(cases) and all(e[2] is not None for _c, evs in cases for e in evs)
    return q


# ------------------------------------------------------------------------------------------------ profile
def _span(evs) -> tuple[float, float]:
    first = min((e[2] if e[2] is not None else e[0]) for e in evs)
    return first, max(e[0] for e in evs)


def _activity_times(cases: list) -> tuple[dict, dict, dict]:
    """Per activity: service, waiting (since the latest completion before the start) and elapsed since the
    previous completion; per pair of activities: overlapping intervals inside a case."""
    acc: dict[str, dict] = defaultdict(lambda: {"instances": 0, "with_start": 0, "service": [], "waiting": [],
                                                "elapsed": [], "parallel_start": 0, "rows": []})
    overlaps: dict[tuple, dict] = {}
    for cid, evs in cases:
        for i, e in enumerate(evs):
            a = acc[e[1]]
            a["instances"] += 1
            if len(a["rows"]) < 5 and len(e) > 5:
                a["rows"].extend(e[5]["rows"][:2])
            if i:
                a["elapsed"].append((e[0] - evs[i - 1][0]) / 3600.0)
            if e[2] is None:
                continue
            a["with_start"] += 1
            a["service"].append((e[0] - e[2]) / 3600.0)
            done = [o[0] for j, o in enumerate(evs) if j != i and o[0] <= e[2]]
            if done:
                a["waiting"].append((e[2] - max(done)) / 3600.0)
            elif any(o[2] is not None and o[2] < e[2] for j, o in enumerate(evs) if j != i):
                a["parallel_start"] += 1
            for j in range(i + 1, len(evs)):
                o = evs[j]
                if o[2] is not None and o[2] < e[0] and e[2] < o[0]:
                    key = tuple(sorted((e[1], o[1])))
                    r = overlaps.setdefault(key, {"pair": list(key), "occurrences": 0, "cases": set(),
                                                  "examples": []})
                    r["occurrences"] += 1
                    if cid not in r["cases"] and len(r["examples"]) < 5:
                        r["examples"].append(cid)
                    r["cases"].add(cid)
    edges: dict[tuple, dict] = defaultdict(lambda: {"overlapping": 0, "tie_dependent": 0})
    for _cid, evs in cases:
        for p, e in zip(evs, evs[1:]):
            d = edges[(p[1], e[1])]
            if e[2] is not None and e[2] < p[0]:
                d["overlapping"] += 1
            if len(e) > 5 and len(p) > 5 and p[0] == e[0]:
                d["tie_dependent"] += 1
    return acc, overlaps, edges


def profile_log(log: dict, threshold: float = 0.5, min_count: int = 1, max_variants: int = 20) -> dict:
    cases = closed_cases(log)
    diag = log["diag"]
    n_open = len(log["cases"]) - len(cases)
    if not cases:
        raise ProcessError(tx("el.no_closed", n=n_open))
    quality = subset_quality(log, cases)
    disc = pm.discover_log(cases, quality, threshold, max(1, int(min_count)), max(1, int(max_variants)))
    acc, overlaps, edge_extra = _activity_times(cases)
    n = len(cases)
    # graph with coverage and the parts the order depends on
    kept = set()
    edges = []
    for e in disc["graph"]["edges"]:
        x = edge_extra.get((e["from"], e["to"]), {"overlapping": 0, "tie_dependent": 0})
        edges.append({**e, "overlapping": x["overlapping"], "tie_dependent": x["tie_dependent"]})
        if e["kept"]:
            kept.add((e["from"], e["to"]))
    transitions = sum(e["count"] for e in edges)
    covered = sum(e["count"] for e in edges if (e["from"], e["to"]) in kept)
    full = sum(1 for _c, evs in cases if all((p[1], q[1]) in kept for p, q in zip(evs, evs[1:])))
    coverage = {"transitions": transitions, "transitions_in_kept_edges": covered,
                "share_of_transitions": covered / transitions if transitions else None,
                "cases_fully_covered": full, "share_of_cases": full / n,
                "note": tx("el.coverage_note")}
    activities = profile_activities(cases, disc, acc)
    proposals = [r["missing_data"] for r in activities if r.get("missing_data")]
    cycle = [(_span(evs)[1] - _span(evs)[0]) / 3600.0 for _c, evs in cases]
    bottlenecks = _bottlenecks(activities, disc)
    explanation = []
    if bottlenecks:
        b = bottlenecks[0]
        explanation.append(tx("el.explain_wait" if b["measure"] == "waiting" else "el.explain_elapsed",
                              act=b["activity"], share=(b["share"] or 0) * 100, median=b["median_hours"] or 0,
                              p90=b["p90_hours"] or 0, n=b["n"] or 0, avg=b["average_cases_waiting"] or 0))
    conc = [{"pair": r["pair"], "occurrences": r["occurrences"], "cases": len(r["cases"]),
             "share_of_cases": len(r["cases"]) / n, "examples": r["examples"]}
            for r in sorted(overlaps.values(), key=lambda r: (-len(r["cases"]), r["pair"]))]
    if conc:
        explanation.append(tx("el.explain_concurrency", n=len(conc), a=conc[0]["pair"][0], b=conc[0]["pair"][1],
                              cases=conc[0]["cases"]))
    if not quality["has_start"]:
        proposals.insert(0, tx("el.need_start_all"))
    if not diag["end_declared"]:
        proposals.append(tx("el.need_end", act=", ".join(diag["proposed_end_activities"] or [])))
    deviations = _deviation_list(log, cases)
    warnings = list(disc["warnings"])
    for key, msg in (("duplicates", "el.warn_duplicates"), ("ties", "el.warn_ties"),
                     ("complete_without_start", "el.warn_no_start_events"),
                     ("start_without_complete", "el.warn_no_complete_events")):
        if diag[key]["total"]:
            warnings.append(tx(msg, n=diag[key]["total"]))
    if diag["timezone"]["zone_changes"]["total"]:
        warnings.append(tx("el.warn_zone_changes", n=diag["timezone"]["zone_changes"]["total"]))
    if n_open:
        warnings.append(tx("el.warn_open", n=n_open))
    return {
        "dataset": log["dataset"], "mapping": quality["mapping"], "options": public_options(log["options"]),
        "time_unit": "hours", "instants": "UTC",
        "cases": {"total": len(log["cases"]), "closed": n, "open": n_open},
        "log": diag,
        "variants": disc["variants"], "variants_count": disc["variants_count"],
        "graph": {**disc["graph"], "edges": edges, "coverage": coverage},
        "start_activities": disc["start_activities"], "end_activities": disc["end_activities"],
        "times": {"cycle_time_hours": pstats(cycle), "activities": activities,
                  "service_separable": quality["has_start"],
                  "note": tx("el.times_sep") if quality["has_start"] else tx("el.times_elapsed")},
        "concurrency": conc, "bottlenecks": bottlenecks, "explanation": explanation,
        "rework": disc["rework"], "resources": disc["resources"],
        "deviations": deviations, "missing_data": proposals,
        "warnings": warnings, "caveat": tx("el.caveat"),
    }


def profile_activities(cases: list, disc: Optional[dict] = None, acc: Optional[dict] = None) -> list[dict]:
    """Observed times per activity with sample sizes; what is separable is said per activity."""
    if acc is None:
        acc = _activity_times(cases)[0]
    case_n: dict[str, set] = defaultdict(set)
    for cid, evs in cases:
        for e in evs:
            case_n[e[1]].add(cid)
    order = [a["activity"] for a in disc["activities"]] if disc else sorted(acc, key=lambda a: -acc[a]["instances"])
    out = []
    for name in order:
        r = acc[name]
        if r["with_start"] == r["instances"] and r["instances"]:
            ident = "service_and_waiting"
        elif r["with_start"]:
            ident = "partial"
        else:
            ident = "elapsed_only"
        row = {"activity": name, "instances": r["instances"], "cases": len(case_n[name]), "identifiable": ident,
               "service_hours": pstats(r["service"]) if r["with_start"] else None,
               "waiting_hours": pstats(r["waiting"]) if r["with_start"] else None,
               "started_in_parallel": r["parallel_start"],
               "elapsed_since_previous_completion_hours": pstats(r["elapsed"]),
               "example_rows": r["rows"][:5]}
        if ident != "service_and_waiting":
            row["missing_data"] = tx("el.need_start", act=name, n=r["instances"] - r["with_start"])
        out.append(row)
    return out


def _bottlenecks(activities: list[dict], disc: dict) -> list[dict]:
    """Where time accumulates before each activity: waiting when it is separable, otherwise elapsed time since the
    previous completion (waiting and work together). average_cases_waiting = total hours / observed span."""
    span_h = disc["span_hours"] or 0
    util = {a["activity"]: a["utilisation"] for a in disc["activities"]}
    ranked = []
    for r in activities:
        if r["identifiable"] == "service_and_waiting":
            st, measure = r["waiting_hours"], "waiting"
        else:
            st, measure = r["elapsed_since_previous_completion_hours"], "elapsed"
        if st and st["n"] and st["total"] > 0:
            ranked.append((st["total"], r["activity"], st, measure))
    grand = sum(x[0] for x in ranked)
    ranked.sort(key=lambda x: (-x[0], x[1]))
    return [{"activity": a, "measure": measure, "total_hours": tot, "share": tot / grand if grand else None,
             "n": st["n"], "mean_hours": st["mean"], "median_hours": st["p50"], "p90_hours": st["p90"],
             "average_cases_waiting": tot / span_h if span_h > 0 else None, "utilisation": util.get(a)}
            for tot, a, st, measure in ranked[:10]]


def _deviation_list(log: dict, cases: list) -> list[dict]:
    """Every deviation with its count, examples and a sentence saying why it is listed."""
    diag = log["diag"]
    out = []

    def add(kind, detail, count, examples, why):
        if count:
            out.append({"type": kind, "detail": detail, "count": count, "examples": examples[:5], "why": why})
    exp = log["options"]["expected"]
    if exp:
        conf = pm.conform_log(cases, {"steps": exp})
        for d in conf["deviations"]:
            add(d["type"], d["detail"], d["cases"], d["examples"], tx("el.why_" + d["type"].replace(" ", "_")))
    ex = diag["excluded"]
    add("invalid timestamp", "", ex["invalid_timestamp"]["total"],
        [f"row {r['row']}: {json.dumps(r['value'], ensure_ascii=False)}" for r in ex["invalid_timestamp"]["items"]], tx("el.why_invalid"))
    add("invalid start", "", ex["invalid_start"]["total"],
        [f"row {r['row']}: {json.dumps(r['value'], ensure_ascii=False)}" for r in ex["invalid_start"]["items"]], tx("el.why_invalid"))
    add("missing case or activity", "", ex["missing_case_or_activity"]["total"],
        [f"row {r['row']}" for r in ex["missing_case_or_activity"]["items"]], tx("el.why_missing_ids"))
    add("unknown lifecycle", "", ex["unknown_lifecycle"]["total"],
        [f"row {r['row']}: {json.dumps(r['value'], ensure_ascii=False)}" for r in ex["unknown_lifecycle"]["items"]], tx("el.why_lifecycle"))
    add("duplicate", "", diag["duplicates"]["total"],
        [f"row {r['row']} = row {r['duplicate_of']}" for r in diag["duplicates"]["items"]], tx("el.why_duplicate"))
    add("tie", "", diag["ties"]["total"],
        [f"{r['case']}: {' = '.join(r['activities'])}" for r in diag["ties"]["items"]], tx("el.why_tie"))
    add("complete without start", "", diag["complete_without_start"]["total"],
        [f"{r['case']}/{r['activity']} row {r['row']}" for r in diag["complete_without_start"]["items"]],
        tx("el.why_no_start"))
    add("start without complete", "", diag["start_without_complete"]["total"],
        [f"{r['case']}/{r['activity']} row {r['row']}" for r in diag["start_without_complete"]["items"]],
        tx("el.why_no_complete"))
    add("start after end", "", diag["start_after_end"]["total"],
        [f"row {r['row']}" for r in diag["start_after_end"]["items"]], tx("el.why_start_after_end"))
    add("zone change", "", diag["timezone"]["zone_changes"]["total"],
        [f"{r['case']}: {', '.join(r['offsets'])}" for r in diag["timezone"]["zone_changes"]["items"]],
        tx("el.why_zone"))
    add("open case", "", diag["open_cases"]["total"],
        [r["case"] for r in diag["open_cases"]["items"]], tx("el.why_open"))
    out.sort(key=lambda d: -d["count"])
    return out


# ----------------------------------------------------------------------------------- model proposal
def _window(cases) -> dict:
    starts = [_span(evs)[0] for _c, evs in cases]
    return {"cases": len(cases), "from": _iso(min(starts)), "to": _iso(max(starts))}


def propose_model(cases: list, quality: dict, replications: int, seed: int) -> tuple[dict, dict, list[dict]]:
    """Scenario-lab model from the given (fit) cases with the provenance of every parameter."""
    model, desc = pm.build_model(cases, quality, replications, seed)
    win = _window(cases)
    acc, _ov, _ed = _activity_times(cases)
    params = [{"parameter": "arrivals", "value": model["arrivals"] if model["arrivals"]["type"] == "poisson"
               else {"type": "empirical", "values_count": len(model["arrivals"]["values"])},
               "identifiable": True, "n": win["cases"] - 1, "estimator": desc["arrivals"],
               "source": {**win, "events": tx("el.src_case_starts")}}]
    for note, st in zip(desc["activities"], model["stations"]):
        a = acc[note["activity"]]
        svc = note["service_basis"] == "service"
        params.append({"parameter": f"service:{st['name']}", "identifiable": svc,
                       "n": note["duration_samples"], "value": {"dist": "empirical",
                                                                "mean_hours": note["duration_mean_hours"]},
                       "estimator": tx("el.est_service") if svc else tx("el.est_elapsed"),
                       "source": {**win, "events": tx("el.src_instances", act=st["name"], n=a["instances"]),
                                  "example_rows": a["rows"][:5]},
                       "missing_data": None if svc else tx("el.need_start", act=st["name"],
                                                          n=a["instances"] - a["with_start"])})
        params.append({"parameter": f"servers:{st['name']}", "identifiable": note["capacity_modelled"],
                       "value": note["servers"], "n": note.get("resources_seen") or a["with_start"] or None,
                       "estimator": note["capacity_basis"],
                       "source": {**win, "resources": note.get("resource_names")},
                       "missing_data": None if note["capacity_modelled"] else tx("el.need_capacity",
                                                                                 act=st["name"])})
    nxt_counts: dict[str, Counter] = defaultdict(Counter)
    for _c, evs in cases:
        for i, e in enumerate(evs):
            nxt_counts[e[1]][evs[i + 1][1] if i + 1 < len(evs) else "END"] += 1
    params.append({"parameter": "routing:start", "value": model["routing"]["start"], "identifiable": True,
                   "n": len(cases), "estimator": tx("el.est_routing"), "source": {**win}})
    for a, shares in model["routing"]["next"].items():
        params.append({"parameter": f"routing:{a}", "value": shares, "identifiable": True,
                       "n": sum(nxt_counts[a].values()), "estimator": tx("el.est_routing"), "source": {**win}})
    return model, desc, params


# ---------------------------------------------------------------------------------- holdout calibration
def _observed(cases: list, bottleneck: Optional[str]) -> dict:
    cyc = [(_span(evs)[1] - _span(evs)[0]) / 3600.0 for _c, evs in cases]
    starts = sorted(_span(evs)[0] for _c, evs in cases)
    span = (starts[-1] - starts[0]) / 3600.0 if len(starts) > 1 else 0
    out = {"cases": len(cases), "cycle": pstats(cyc), "cases_per_hour": (len(starts) - 1) / span if span > 0 else None,
           "bottleneck_wait": None}
    if bottleneck:
        acc, _o, _e = _activity_times(cases)
        r = acc.get(bottleneck)
        if r and r["with_start"] == r["instances"] and r["instances"]:
            out["bottleneck_wait"] = pstats(r["waiting"])
    return out


def split_cases(cases: list, holdout: float = 0.3, split_at: Optional[str] = None) -> tuple[list, list, float]:
    order = sorted(cases, key=lambda c: (_span(c[1])[0], c[0]))
    if split_at:
        t, info = parse_timestamp(split_at, timezone.utc)
        if t is None:
            raise ProcessError(tx("el.bad_split", value=split_at))
        cut = t
    else:
        if not 0.05 <= float(holdout) <= 0.8:
            raise ProcessError(tx("el.holdout_range"))
        k = max(1, int(round(len(order) * (1 - float(holdout)))))
        k = min(k, len(order) - 1)
        cut = _span(order[k][1])[0]
    train = [c for c in order if _span(c[1])[0] < cut]
    test = [c for c in order if _span(c[1])[0] >= cut]
    return train, test, cut


def _row(metric: str, train_obs, test_obs, sim: dict, tol: float, n_test: int) -> dict:
    m = sim["mean"]
    if test_obs in (None, 0) or m is None:
        return {"metric": metric, "fit_observed": train_obs, "holdout_observed": test_obs, "simulated": m,
                "simulated_ci": [sim.get("ci_low"), sim.get("ci_high")], "relative_difference": None,
                "tolerance": tol, "ok": None, "n_holdout": n_test, "note": tx("el.not_evaluable")}
    rel = abs(m - test_obs) / abs(test_obs)
    return {"metric": metric, "fit_observed": train_obs, "holdout_observed": test_obs, "simulated": m,
            "simulated_ci": [sim.get("ci_low"), sim.get("ci_high")], "relative_difference": rel, "tolerance": tol,
            "ok": rel <= tol, "n_holdout": n_test, "note": None}


def calibrate_log(log: dict, holdout: float = 0.3, split_at: Optional[str] = None, replications: int = 20,
                  seed: int = 1, tolerances: Optional[dict] = None, variants: Optional[list] = None) -> dict:
    if not 1 <= int(replications) <= 100:
        raise ProcessError(tx("pm.replications_range"))
    tol = dict(DEFAULT_TOLERANCES)
    for k, v in (tolerances or {}).items():
        if k not in tol:
            raise ProcessError(tx("el.bad_tolerance", key=k, keys=", ".join(tol)))
        try:
            v = float(v)
        except (TypeError, ValueError):
            raise ProcessError(tx("el.bad_tolerance_value", key=k)) from None
        if not 0 < v < 5:
            raise ProcessError(tx("el.bad_tolerance_value", key=k))
        tol[k] = v
    cases = closed_cases(log)
    train, test, cut = split_cases(cases, holdout, split_at)
    if len(train) < pm.MIN_CASES:
        raise ProcessError(tx("el.few_fit", n=len(train), min=pm.MIN_CASES))
    if len(test) < MIN_TEST_CASES:
        raise ProcessError(tx("el.few_holdout", n=len(test), min=MIN_TEST_CASES))
    q_train = subset_quality(log, train)
    model, desc, params = propose_model(train, q_train, int(replications), int(seed))
    disc = pm.discover_log(train, q_train)
    b_rows = _bottlenecks(profile_activities(train, disc), disc)
    bott = b_rows[0]["activity"] if b_rows else None
    obs_train, obs_test = _observed(train, bott), _observed(test, bott)
    vlist = _variants(model, variants)
    try:
        if vlist:
            cmp_ = sc.compare_models(model, [{"name": v["name"], "overrides": v["overrides"]} for v in vlist],
                                     int(replications), int(seed))
            run = cmp_["base"]
        else:
            cmp_ = None
            run = sc._public(sc.run_model(model, int(replications), int(seed)))
    except sc.ScenarioError as exc:
        raise ProcessError(tx("pm.sim_failed", exc=exc)) from None
    met = run["metrics"]
    nt = len(test)
    rows = [_row("cycle_time_mean_hours", obs_train["cycle"]["mean"], obs_test["cycle"]["mean"],
                 met["lead_time_mean"], tol["cycle_time_mean_hours"], nt),
            _row("cycle_time_p50_hours", obs_train["cycle"]["p50"], obs_test["cycle"]["p50"], met["lead_time_p50"],
                 tol["cycle_time_p50_hours"], nt),
            _row("cycle_time_p90_hours", obs_train["cycle"]["p90"], obs_test["cycle"]["p90"], met["lead_time_p90"],
                 tol["cycle_time_p90_hours"], nt),
            _row("cases_per_hour", obs_train["cases_per_hour"], obs_test["cases_per_hour"], met["throughput"],
                 tol["cases_per_hour"], nt)]
    if bott:
        wkey = f"wait:{bott}"
        tw, hw = obs_train["bottleneck_wait"], obs_test["bottleneck_wait"]
        r = _row("bottleneck_wait_mean_hours", tw and tw["mean"], hw and hw["mean"],
                 met.get(wkey, {"mean": None}), tol["bottleneck_wait_mean_hours"], nt)
        r["activity"] = bott
        if hw is None:
            r["note"] = tx("el.wait_not_identifiable", act=bott)
        rows.append(r)
    evaluated = [r for r in rows if r["ok"] is not None]
    passed = bool(evaluated) and all(r["ok"] for r in evaluated)
    failed = [r["metric"] for r in evaluated if not r["ok"]]
    verdict = tx("el.calib_pass", n=len(evaluated), test=nt) if passed else tx(
        "el.calib_fail", metrics=", ".join(failed) or "-", test=nt)
    warnings = list(desc["warnings"]) + list(run["warnings"])
    if nt < pm.SMALL_LOG:
        warnings.append(tx("el.warn_small_holdout", n=nt))
    outs = pm.variant_results(vlist, cmp_) if cmp_ else []
    if outs and not passed:
        for o in outs:
            o["warnings"] = [tx("el.alt_uncalibrated")] + list(o["warnings"])
    return {
        "split": {"at": _iso(cut), "holdout": None if split_at else float(holdout), "fit_cases": len(train),
                  "holdout_cases": nt, "fit_window": _window(train), "holdout_window": _window(test),
                  "rule": tx("el.split_rule")},
        "model": model, "parameters": params,
        "not_identifiable": [p["parameter"] for p in params if not p["identifiable"]],
        "bottleneck": bott,
        "validation": {"passed": passed, "verdict": verdict, "rows": rows, "tolerances": tol,
                       "replications": int(replications), "seed": int(seed)},
        "baseline": {"metrics": {k: met[k] for k in pm.HEADLINE if k in met}, "stations": run["stations"]},
        "alternatives": outs,
        "warnings": warnings, "method": tx("el.calib_method"), "caveat": tx("el.caveat_sim"),
    }


def _variants(model: dict, variants: Optional[list]) -> list[dict]:
    out, names = [], set()
    for i, v in enumerate(variants or []):
        if not isinstance(v, dict):
            raise ProcessError(tx("pm.variants_shape"))
        name = str(v.get("name") or tx("pm.variant_default_name", i=i + 1))
        if name in names:
            raise ProcessError(tx("pm.variant_unique", name=name))
        names.add(name)
        ch = v.get("changes")
        if not isinstance(ch, list) or not ch:
            raise ProcessError(tx("pm.variant_give", name=name))
        out.append({"name": name, "overrides": pm.variant_overrides(model, ch),
                    "changes": [pm.describe_change(c) for c in ch], "raw": ch})
    return out


# ------------------------------------------------------------------------------------- dataset reference
def dataset_reference(services, dataset: str) -> dict:
    row = services._dataset_row(dataset)
    v = services._version_row(row)
    table = v["table_name"]
    with services.engine.lock() as conn:
        cols = [r[0] for r in conn.execute(f"DESCRIBE {pm._q(table)}").fetchall()]
        n, h = conn.execute(f"SELECT count(*), sum(hash({', '.join(pm._q(c) for c in cols)})::HUGEINT) "
                            f"FROM {pm._q(table)}").fetchone()
    return {"name": row["name"], "id": row["id"], "version": v["version"], "row_count": n,
            "columns": cols, "fingerprint": f"{n}:{h if h is not None else 0}",
            "fingerprint_method": "row count : sum of DuckDB hash() of every row (order-independent)"}


def check_reference(services, ref: dict) -> dict:
    now = dataset_reference(services, ref["name"])
    same = now["fingerprint"] == ref.get("fingerprint")
    return {"same_content": same, "recipe_version": ref.get("version"), "current_version": now["version"],
            "note": None if same else tx("el.dataset_changed", name=ref["name"])}


def recipe_for(services, dataset: str, mapping: dict, options: dict, calibration: Optional[dict] = None,
               variants: Optional[list] = None) -> dict:
    return {"kind": RECIPE_KIND, "schema": RECIPE_SCHEMA,
            "created_at": datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
            "dataset": dataset_reference(services, dataset), "mapping": mapping, "log_options": options,
            "calibration": calibration, "variants": variants or [],
            "note": tx("el.recipe_note")}


def read_recipe(recipe: Any) -> dict:
    if isinstance(recipe, str):
        try:
            recipe = json.loads(recipe)
        except json.JSONDecodeError as exc:
            raise ProcessError(tx("el.recipe_json", exc=exc)) from None
    if not isinstance(recipe, dict) or recipe.get("kind") != RECIPE_KIND:
        raise ProcessError(tx("el.recipe_kind"))
    if not isinstance(recipe.get("dataset"), dict) or not recipe["dataset"].get("name"):
        raise ProcessError(tx("el.recipe_kind"))
    return recipe


# ------------------------------------------------------------------------------------------ services layer
def _inputs(dataset, mapping, log_options, recipe):
    rec = read_recipe(recipe) if recipe else None
    if rec:
        dataset = dataset or rec["dataset"]["name"]
        mapping = mapping or rec["mapping"]
        log_options = log_options if log_options is not None else rec.get("log_options")
    if not dataset:
        raise ProcessError(tx("el.need_dataset"))
    if not mapping:
        raise ProcessError(tx("pm.map_required", key="case"))
    return dataset, mapping, log_options or {}, rec


def profile_process(services, dataset: Optional[str], mapping: Optional[dict], log_options: Optional[dict] = None,
                    threshold: float = 0.5, min_count: int = 1, max_variants: int = 20,
                    recipe: Optional[Any] = None, source: str = "ui") -> dict:
    dataset, mapping, log_options, rec = _inputs(dataset, mapping, log_options, recipe)

    def do():
        log = load(services, dataset, mapping, log_options)
        res = profile_log(log, threshold, min_count, max_variants)
        res["dataset_reference"] = dataset_reference(services, dataset)
        if rec:
            res["recipe_check"] = check_reference(services, rec["dataset"])
            if res["recipe_check"]["note"]:
                res["warnings"].insert(0, res["recipe_check"]["note"])
        res["recipe"] = recipe_for(services, dataset, log["quality"]["mapping"], public_options(log["options"]))
        res["_log_summary"] = (f"event-log profile of {dataset}: {res['cases']['closed']} closed / "
                               f"{res['cases']['open']} open cases, {res['variants_count']} variants, "
                               f"{res['log']['duplicates']['total']} duplicates, {res['log']['ties']['total']} ties")
        return res
    return services._log("process_profile", source, dataset, {"mapping": mapping, "log_options": log_options}, do)


def calibrate_process(services, dataset: Optional[str], mapping: Optional[dict], log_options: Optional[dict] = None,
                      holdout: float = 0.3, split_at: Optional[str] = None, replications: int = 20, seed: int = 1,
                      tolerances: Optional[dict] = None, variants: Optional[list] = None,
                      model_name: Optional[str] = None, recipe: Optional[Any] = None, source: str = "ui") -> dict:
    dataset, mapping, log_options, rec = _inputs(dataset, mapping, log_options, recipe)
    if rec and rec.get("calibration"):
        c = rec["calibration"]
        holdout, split_at = c.get("holdout", holdout) or holdout, split_at or c.get("split_at")
        replications, seed = c.get("replications", replications), c.get("seed", seed)
        tolerances = tolerances or c.get("tolerances")
        variants = variants or rec.get("variants")

    def do():
        log = load(services, dataset, mapping, log_options)
        res = calibrate_log(log, holdout, split_at, replications, seed, tolerances, variants)
        res["dataset"] = dataset
        res["cases"] = {"total": len(log["cases"]), "closed": log["diag"]["closed_cases"],
                        "open": len(log["cases"]) - log["diag"]["closed_cases"]}
        if res["cases"]["open"]:
            res["warnings"].append(tx("el.warn_open", n=res["cases"]["open"]))
        if rec:
            res["recipe_check"] = check_reference(services, rec["dataset"])
            if res["recipe_check"]["note"]:
                res["warnings"].insert(0, res["recipe_check"]["note"])
        if model_name:
            saved = dict(res["model"])
            saved["provenance"] = {"from_dataset": dataset, "fit_window": res["split"]["fit_window"],
                                   "parameters": [{k: p[k] for k in ("parameter", "identifiable", "n", "estimator")}
                                                  for p in res["parameters"]],
                                   "validation_passed": res["validation"]["passed"]}
            res["scenario_model"] = sc.save_model(services, model_name, saved, source=source)["name"]
        res["recipe"] = recipe_for(services, dataset, log["quality"]["mapping"], public_options(log["options"]),
                                   {"holdout": None if split_at else holdout, "split_at": split_at,
                                    "replications": int(replications), "seed": int(seed),
                                    "tolerances": res["validation"]["tolerances"]}, variants)
        res["_log_summary"] = (f"holdout calibration on {dataset}: "
                               f"{'passed' if res['validation']['passed'] else 'FAILED'} "
                               f"({res['split']['fit_cases']} fit / {res['split']['holdout_cases']} held-out cases), "
                               f"{len(res['alternatives'])} alternatives")
        return res
    return services._log("process_calibrate", source, dataset,
                         {"mapping": mapping, "log_options": log_options, "holdout": holdout, "split_at": split_at,
                          "variants": [v.get("name") for v in variants or [] if isinstance(v, dict)]}, do)


def export_process(services, dataset: Optional[str], mapping: Optional[dict], log_options: Optional[dict] = None,
                   with_calibration: bool = False, holdout: float = 0.3, split_at: Optional[str] = None,
                   replications: int = 20, seed: int = 1, tolerances: Optional[dict] = None,
                   variants: Optional[list] = None, source: str = "ui") -> dict:
    """The profile and the recipe as one JSON document: references to the dataset, never a copy of its rows."""
    dataset, mapping, log_options, _rec = _inputs(dataset, mapping, log_options, None)

    def do():
        log = load(services, dataset, mapping, log_options)
        prof = profile_log(log)
        cal = None
        calib_settings = None
        if with_calibration:
            cal = calibrate_log(log, holdout, split_at, replications, seed, tolerances, variants)
            calib_settings = {"holdout": None if split_at else holdout, "split_at": split_at,
                              "replications": int(replications), "seed": int(seed),
                              "tolerances": cal["validation"]["tolerances"]}
        recipe = recipe_for(services, dataset, log["quality"]["mapping"], public_options(log["options"]),
                            calib_settings, variants)
        diag = prof["log"]
        recipe["profile"] = {
            "cases": prof["cases"], "variants_count": prof["variants_count"],
            "variants": [{k: v[k] for k in ("id", "activities", "count", "share")} for v in prof["variants"]],
            "graph": [{k: e[k] for k in ("from", "to", "count", "cases", "share_of_cases", "kept", "overlapping",
                                         "tie_dependent")} for e in prof["graph"]["edges"]],
            "coverage": prof["graph"]["coverage"], "times": prof["times"], "bottlenecks": prof["bottlenecks"],
            "concurrency": prof["concurrency"],
            "checks": {"duplicates": diag["duplicates"]["total"], "ties": diag["ties"]["total"],
                       "excluded": {k: v["total"] for k, v in diag["excluded"].items()},
                       "complete_without_start": diag["complete_without_start"]["total"],
                       "start_without_complete": diag["start_without_complete"]["total"],
                       "zone_changes": diag["timezone"]["zone_changes"]["total"],
                       "offsets": diag["timezone"]["offsets"], "open_cases": diag["open_cases"]["total"]},
            "deviations": [{k: d[k] for k in ("type", "detail", "count")} for d in prof["deviations"]],
            "missing_data": prof["missing_data"]}
        if cal:
            recipe["model_proposal"] = {"model": cal["model"], "parameters": cal["parameters"],
                                        "split": cal["split"]}
            recipe["validation"] = cal["validation"]
            recipe["alternatives"] = [{k: a[k] for k in ("name", "changes", "headline")} for a in cal["alternatives"]]
        else:
            cases = closed_cases(log)
            if len(cases) >= pm.MIN_CASES:
                model, _d, params = propose_model(cases, subset_quality(log, cases), int(replications), int(seed))
                recipe["model_proposal"] = {"model": model, "parameters": params, "split": None}
        out = {"recipe": recipe, "filename": f"{re.sub(r'[^A-Za-z0-9_.-]+', '_', dataset)}.process-recipe.json",
               "_log_summary": f"exported process recipe for {dataset}"}
        return out
    return services._log("process_export", source, dataset, {"mapping": mapping, "log_options": log_options,
                                                               "with_calibration": with_calibration}, do)

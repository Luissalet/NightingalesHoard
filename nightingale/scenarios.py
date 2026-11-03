"""Scenario lab: explicit operations models simulated with a discrete-event engine.

A model states demand (arrivals), capacity (stations with servers and shifts),
times (service distributions), stock (an optional consumed item with a reorder
rule) and cost. The engine is a seeded heapq event loop; several replications
give each metric a mean and a 95% interval. The output is a set of scenarios
under stated assumptions, not a prediction: the model, its seed and, when data
were used, the calibration sample size are always reported.

Optionally a model carries `routing` (a first-order Markov chain over the stations: start shares and
next-step shares, with "END" for leaving), which turns the line into a network: each job gets its own route,
loops (rework) included, and every visit draws its own service time. Without `routing` the stations are the
plain tandem line described above, unchanged.

All times are hours. Random numbers come from per-purpose streams keyed by
(seed, replication) and indexed by job, so two variants of a model run with the
same seed see the same arrivals and the same underlying service draws (common
random numbers), which makes their differences much less noisy.
"""
from __future__ import annotations

import copy
import csv
import heapq
import io
import json
import math
import random
import re
from collections import deque
from pathlib import Path
from statistics import NormalDist
from typing import Any, Callable, Optional

_NORM = NormalDist()
UNITS = {"hours": 1.0, "hour": 1.0, "h": 1.0, "minutes": 1 / 60, "minute": 1 / 60, "min": 1 / 60,
         "seconds": 1 / 3600, "second": 1 / 3600, "s": 1 / 3600, "days": 24.0, "day": 24.0}
DISTS = ("fixed", "exponential", "uniform", "triangular", "lognormal", "empirical")
MAX_REPLICATIONS = 200
MAX_JOB_DRAWS = 6_000_000
SMALL_SAMPLE = 30
VERY_SMALL_SAMPLE = 10
CAVEAT = ("Scenarios under the stated assumptions, not predictions: results depend on the model, the seed and "
          "any calibration sample.")
# higher_better True / False; None = descriptive, no judgement
POLARITY = {"throughput": True, "lead_time_mean": False, "lead_time_p50": False, "lead_time_p90": False,
            "wip": False, "lost_jobs": False, "stockouts": False, "total_cost": False, "cost_per_hour": False,
            "orders": None, "avg_stock": None, "wait": False, "queue": False, "lost": False, "util": None}


class ScenarioError(ValueError):
    pass


# ------------------------------------------------------------------ statistics
def t_crit(df: int) -> float:
    """Two-sided 95% Student t critical value."""
    table = {1: 12.706, 2: 4.303, 3: 3.182, 4: 2.776, 5: 2.571, 6: 2.447, 7: 2.365, 8: 2.306, 9: 2.262, 10: 2.228}
    if df in table:
        return table[df]
    z = 1.959964
    return z + (z ** 3 + z) / (4 * df) + (5 * z ** 5 + 16 * z ** 3 + 3 * z) / (96 * df ** 2)


def mean_ci(xs: list[float]) -> dict:
    n = len(xs)
    if n == 0:
        return {"mean": None, "sd": None, "ci_low": None, "ci_high": None, "n": 0}
    m = sum(xs) / n
    if n < 2:
        return {"mean": m, "sd": None, "ci_low": None, "ci_high": None, "n": n}
    sd = math.sqrt(sum((x - m) ** 2 for x in xs) / (n - 1))
    h = t_crit(n - 1) * sd / math.sqrt(n)
    return {"mean": m, "sd": sd, "ci_low": m - h, "ci_high": m + h, "n": n}


def percentile(sorted_xs: list[float], p: float) -> float:
    if not sorted_xs:
        return float("nan")
    k = (len(sorted_xs) - 1) * p
    lo = int(math.floor(k))
    hi = min(lo + 1, len(sorted_xs) - 1)
    return sorted_xs[lo] + (sorted_xs[hi] - sorted_xs[lo]) * (k - lo)


# ----------------------------------------------------------------- distributions
def _num(spec: dict, key: str, where: str, positive: bool = False, nonneg: bool = True) -> float:
    if key not in spec:
        raise ScenarioError(f"{where}: missing {key!r}")
    try:
        v = float(spec[key])
    except (TypeError, ValueError):
        raise ScenarioError(f"{where}: {key!r} must be a number") from None
    if not math.isfinite(v) or (positive and v <= 0) or (nonneg and v < 0):
        raise ScenarioError(f"{where}: {key!r} must be {'greater than 0' if positive else 'not negative'}")
    return v


def make_sampler(spec: Any, where: str) -> Callable[[float], float]:
    """Quantile function u -> time (hours) for a distribution spec; one uniform per draw keeps runs comparable."""
    if isinstance(spec, (int, float)):
        spec = {"dist": "fixed", "value": spec}
    if not isinstance(spec, dict) or "dist" not in spec:
        raise ScenarioError(f"{where}: expected {{\"dist\": one of {', '.join(DISTS)}, ...}}")
    d = spec["dist"]
    if d == "fixed":
        v = _num(spec, "value", where)
        return lambda u: v
    if d == "exponential":
        mean = _num(spec, "mean", where, positive=True)
        return lambda u: -mean * math.log(1.0 - u)
    if d == "uniform":
        lo, hi = _num(spec, "min", where), _num(spec, "max", where)
        if hi < lo:
            raise ScenarioError(f"{where}: max is below min")
        return lambda u: lo + (hi - lo) * u
    if d == "triangular":
        lo, mode, hi = _num(spec, "min", where), _num(spec, "mode", where), _num(spec, "max", where)
        if not lo <= mode <= hi or hi == lo:
            raise ScenarioError(f"{where}: need min <= mode <= max and max > min")
        fc = (mode - lo) / (hi - lo)

        def tri(u):
            if u < fc:
                return lo + math.sqrt(u * (hi - lo) * (mode - lo))
            return hi - math.sqrt((1 - u) * (hi - lo) * (hi - mode))
        return tri
    if d == "lognormal":
        mean = _num(spec, "mean", where, positive=True)
        sigma = _num(spec, "sigma", where, positive=True)
        mu = math.log(mean) - sigma * sigma / 2
        return lambda u: math.exp(mu + sigma * _NORM.inv_cdf(min(max(u, 1e-12), 1 - 1e-12)))
    if d == "empirical":
        vals = spec.get("values")
        if not isinstance(vals, list) or not vals:
            raise ScenarioError(f"{where}: empirical needs 'values' (a list) or 'dataset' and 'column'")
        try:
            xs = sorted(float(v) for v in vals)
        except (TypeError, ValueError):
            raise ScenarioError(f"{where}: empirical values must be numbers") from None
        if xs[0] < 0 or not all(math.isfinite(x) for x in xs):
            raise ScenarioError(f"{where}: empirical values must be finite and not negative")
        n = len(xs)
        return lambda u: xs[min(int(u * n), n - 1)]
    raise ScenarioError(f"{where}: unknown distribution {d!r}; use one of {', '.join(DISTS)}")


def dist_mean(spec: Any) -> Optional[float]:
    if isinstance(spec, (int, float)):
        return float(spec)
    d = spec.get("dist")
    try:
        if d == "fixed":
            return float(spec["value"])
        if d in ("exponential", "lognormal"):
            return float(spec["mean"])
        if d == "uniform":
            return (float(spec["min"]) + float(spec["max"])) / 2
        if d == "triangular":
            return (float(spec["min"]) + float(spec["mode"]) + float(spec["max"])) / 3
        if d == "empirical":
            return sum(float(v) for v in spec["values"]) / len(spec["values"])
    except (KeyError, TypeError, ValueError, ZeroDivisionError):
        return None
    return None


# ---------------------------------------------------------------- model handling
def default_model() -> dict:
    return {
        "horizon_hours": 200, "warmup_hours": 20, "replications": 20, "seed": 1,
        "arrivals": {"type": "poisson", "rate": 4},
        "stations": [{"name": "Service", "servers": 1, "service": {"dist": "exponential", "mean": 0.125}}],
        "costs": {"server_hour": 20, "waiting_hour": 10, "lost_job": 50},
    }


def _set_path(model: dict, path: str, value: Any) -> None:
    """Set a dotted path; list items are addressed by index or by their 'name' (stations.Packing.servers)."""
    parts = str(path).split(".")
    node: Any = model
    for i, part in enumerate(parts):
        last = i == len(parts) - 1
        if isinstance(node, list):
            idx = None
            if re.fullmatch(r"\d+", part):
                idx = int(part)
            else:
                for j, item in enumerate(node):
                    if isinstance(item, dict) and item.get("name") == part:
                        idx = j
                        break
            if idx is None or idx >= len(node):
                raise ScenarioError(f"override path {path!r}: no item {part!r}")
            if last:
                node[idx] = value
            else:
                node = node[idx]
        elif isinstance(node, dict):
            if last:
                node[part] = value
            else:
                if part not in node or not isinstance(node[part], (dict, list)):
                    if part in node:
                        raise ScenarioError(f"override path {path!r}: {part!r} is not a container")
                    node[part] = {}
                node = node[part]
        else:
            raise ScenarioError(f"override path {path!r} does not exist")


def apply_overrides(model: dict, overrides: dict) -> dict:
    m = copy.deepcopy(model)
    for path, value in (overrides or {}).items():
        _set_path(m, path, copy.deepcopy(value))
    return m


class _Station:
    __slots__ = ("name", "servers", "sampler", "shifts", "buffer", "on_full", "cost", "queue", "busy", "blocked",
                 "idx")


def _prepare(model: dict) -> dict:
    """Validate and compile a model into the structures the event loop uses."""
    if not isinstance(model, dict):
        raise ScenarioError("model must be a JSON object")
    horizon = float(model.get("horizon_hours", 168))
    if not math.isfinite(horizon) or horizon <= 0:
        raise ScenarioError("horizon_hours must be greater than 0")
    warm = float(model.get("warmup_hours", horizon * 0.1))
    if not 0 <= warm < horizon:
        raise ScenarioError("warmup_hours must be at least 0 and below horizon_hours")
    # arrivals
    arr = model.get("arrivals")
    if not isinstance(arr, dict) or "type" not in arr:
        raise ScenarioError("arrivals: expected {\"type\": \"poisson\" | \"schedule\" | \"empirical\", ...}")
    kind = arr["type"]
    if kind == "poisson":
        rate = _num(arr, "rate", "arrivals", positive=True)
        arrival = {"type": "poisson", "rate": rate, "rmax": rate}
    elif kind == "schedule":
        rates = arr.get("rates")
        if not isinstance(rates, list) or not rates:
            raise ScenarioError("arrivals: schedule needs 'rates', a list of rates per hour (24 = by hour of day)")
        try:
            rates = [float(r) for r in rates]
        except (TypeError, ValueError):
            raise ScenarioError("arrivals: schedule rates must be numbers") from None
        if min(rates) < 0 or max(rates) <= 0:
            raise ScenarioError("arrivals: schedule rates must be not negative and at least one above 0")
        arrival = {"type": "schedule", "rates": rates, "rmax": max(rates)}
    elif kind == "empirical":
        sampler = make_sampler({"dist": "empirical", "values": arr.get("values")}, "arrivals")
        mean = dist_mean({"dist": "empirical", "values": arr["values"]})
        if not mean or mean <= 0:
            raise ScenarioError("arrivals: empirical inter-arrival times must have a mean above 0")
        arrival = {"type": "empirical", "sampler": sampler, "rmax": 1 / mean}
    else:
        raise ScenarioError(f"arrivals: unknown type {kind!r}")
    # job types
    types = model.get("job_types") or []
    if types:
        if not isinstance(types, list):
            raise ScenarioError("job_types must be a list")
        mix = [float(t.get("mix", 1)) for t in types]
        if min(mix) < 0 or sum(mix) <= 0:
            raise ScenarioError("job_types: mix values must be not negative and sum above 0")
        total = sum(mix)
        cum, acc = [], 0.0
        for mx in mix:
            acc += mx / total
            cum.append(acc)
        scale = [float(t.get("service_scale", 1)) for t in types]
        if min(scale) <= 0:
            raise ScenarioError("job_types: service_scale must be above 0")
        names = [str(t.get("name", f"type{i + 1}")) for i, t in enumerate(types)]
    else:
        cum, scale, names = [1.0], [1.0], ["all"]
    # stations
    raw = model.get("stations")
    if not isinstance(raw, list) or not raw:
        raise ScenarioError("stations: at least one station is required")
    costs = model.get("costs") or {}
    stations, seen = [], set()
    for i, s in enumerate(raw):
        if not isinstance(s, dict):
            raise ScenarioError(f"stations[{i}] must be an object")
        st = _Station()
        st.idx = i
        st.name = str(s.get("name", f"Station {i + 1}"))
        if st.name in seen:
            raise ScenarioError(f"station names must be unique ({st.name!r})")
        seen.add(st.name)
        where = f"station {st.name!r}"
        servers = s.get("servers", 1)
        if not isinstance(servers, int) or isinstance(servers, bool) or servers < 1:
            raise ScenarioError(f"{where}: servers must be a whole number of at least 1")
        st.servers = servers
        st.sampler = make_sampler(s.get("service"), where + " service")
        st.shifts = None
        if s.get("shifts"):
            sh = []
            for w in s["shifts"]:
                a, b = float(w.get("from", 0)), float(w.get("to", 24))
                n = int(w.get("servers", servers))
                if not (0 <= a < 24 and 0 < b <= 24) or a == b or n < 0:
                    raise ScenarioError(f"{where}: shifts need from/to hours in 0-24 and servers >= 0")
                sh.append((a, b, n))
            st.shifts = sh
        buf = s.get("buffer")
        if buf is not None:
            if not isinstance(buf, int) or isinstance(buf, bool) or buf < 0:
                raise ScenarioError(f"{where}: buffer must be a whole number >= 0 or null")
        st.buffer = buf
        st.on_full = s.get("on_full", "lose")
        if st.on_full not in ("lose", "block"):
            raise ScenarioError(f"{where}: on_full must be 'lose' or 'block'")
        st.cost = float(s.get("server_cost_hour", costs.get("server_hour", 0)))
        stations.append(st)
    routing = _prepare_routing(model.get("routing"), stations)
    # inventory
    inv = model.get("inventory")
    inventory = None
    if inv:
        if not isinstance(inv, dict):
            raise ScenarioError("inventory must be an object")
        which = inv.get("station", stations[0].name)
        match = [st for st in stations if st.name == which]
        if not match:
            raise ScenarioError(f"inventory: unknown station {which!r}")
        q = _num(inv, "order_quantity", "inventory", positive=True)
        inventory = {
            "station": match[0].idx, "initial": _num(inv, "initial", "inventory"),
            "reorder_point": _num(inv, "reorder_point", "inventory"), "order_quantity": q,
            "per_job": float(inv.get("per_job", 1)), "item": str(inv.get("item", "stock")),
            "lead": make_sampler(inv.get("lead_time", {"dist": "fixed", "value": 0}), "inventory lead_time"),
        }
        if inventory["per_job"] <= 0:
            raise ScenarioError("inventory: per_job must be above 0")
        if inventory["order_quantity"] < inventory["per_job"]:
            raise ScenarioError("inventory: order_quantity is smaller than per_job; jobs could never start")
    reps = int(model.get("replications", 20))
    if not 1 <= reps <= MAX_REPLICATIONS:
        raise ScenarioError(f"replications must be between 1 and {MAX_REPLICATIONS}")
    visits = routing["K"] if routing else 1
    expected = arrival["rmax"] * horizon * reps * (len(stations) * visits + 2)
    if expected > MAX_JOB_DRAWS:
        raise ScenarioError("model too large for one run (about "
                            f"{int(expected):,} random draws); shorten the horizon or the replications")
    return {
        "horizon": horizon, "warm": warm, "reps": reps, "seed": int(model.get("seed", 1)), "arrival": arrival,
        "cum": cum, "scale": scale, "type_names": names, "stations": stations, "inventory": inventory,
        "routing": routing,
        "c_wait": float(costs.get("waiting_hour", 0)), "c_lost": float(costs.get("lost_job", 0)),
        "c_hold": float(costs.get("holding_unit_hour", 0)), "c_order": float(costs.get("order", 0)),
        "c_stockout": float(costs.get("stockout_event", 0)),
    }


MAX_ROUTE_STEPS = 200


def _cumulative(shares: Any, index: dict, where: str, allow_end: bool) -> tuple[list[int], list[float]]:
    if not isinstance(shares, dict) or not shares:
        raise ScenarioError(f"{where}: expected an object {{station: share}}")
    targets, weights = [], []
    for name, w in shares.items():
        try:
            w = float(w)
        except (TypeError, ValueError):
            raise ScenarioError(f"{where}: share of {name!r} must be a number") from None
        if w < 0 or not math.isfinite(w):
            raise ScenarioError(f"{where}: share of {name!r} must not be negative")
        if name == "END" and allow_end:
            targets.append(-1)
        elif name in index:
            targets.append(index[name])
        else:
            raise ScenarioError(f"{where}: unknown station {name!r}")
        weights.append(w)
    total = sum(weights)
    if total <= 0:
        raise ScenarioError(f"{where}: shares must sum to more than 0")
    cum, acc = [], 0.0
    for w in weights:
        acc += w / total
        cum.append(acc)
    return targets, cum


def _prepare_routing(raw: Any, stations: list) -> Optional[dict]:
    if not raw:
        return None
    if not isinstance(raw, dict) or "start" not in raw:
        raise ScenarioError("routing: expected {\"start\": {station: share}, \"next\": {station: {station|END: share}}}")
    index = {st.name: st.idx for st in stations}
    visits = raw.get("max_visits", 4)
    if not isinstance(visits, int) or isinstance(visits, bool) or not 1 <= visits <= 20:
        raise ScenarioError("routing: max_visits must be a whole number between 1 and 20")
    nxt = {}
    for name, shares in (raw.get("next") or {}).items():
        if name not in index:
            raise ScenarioError(f"routing.next: unknown station {name!r}")
        nxt[index[name]] = _cumulative(shares, index, f"routing.next.{name}", True)
    return {"start": _cumulative(raw["start"], index, "routing.start", False), "next": nxt, "K": visits}


def _pick(spec: tuple[list[int], list[float]], u: float) -> int:
    targets, cum = spec
    for t, c in zip(targets, cum):
        if u <= c:
            return t
    return targets[-1]


def _streams(seed: int, rep: int) -> Callable[[int], random.Random]:
    base = (int(seed) * 1_000_003 + rep) * 977
    return lambda stream: random.Random(base + stream * 7919 + 13)


def _capacity(st: _Station, t: float) -> int:
    if not st.shifts:
        return st.servers
    h = t % 24.0
    for a, b, n in st.shifts:
        if (a <= h < b) if a < b else (h >= a or h < b):
            return n
    return 0


# Event kinds, ordered so that at equal times departures precede arrivals.
_END, _CAP, _ORD, _ARR = 0, 1, 2, 3


def simulate(p: dict, rep: int) -> dict:
    """One replication. Returns scalar metrics for the window [warmup, horizon]."""
    horizon, warm = p["horizon"], p["warm"]
    S: list[_Station] = p["stations"]
    ns = len(S)
    inv = p["inventory"]
    rng = _streams(p["seed"], rep)

    # --- arrivals (independent of the system, so generated up front)
    arr = p["arrival"]
    ra, ru = rng(1), rng(2)
    times: list[float] = []
    t = 0.0
    if arr["type"] == "poisson":
        rate = arr["rate"]
        while True:
            t += -math.log(1.0 - ra.random()) / rate
            if t >= horizon:
                break
            times.append(t)
    elif arr["type"] == "schedule":
        rates, rmax = arr["rates"], arr["rmax"]
        n = len(rates)
        while True:
            t += -math.log(1.0 - ra.random()) / rmax
            if t >= horizon:
                break
            if ru.random() * rmax < rates[int(t) % n]:
                times.append(t)
    else:
        sampler = arr["sampler"]
        while True:
            t += sampler(ra.random())
            if t >= horizon:
                break
            times.append(t)
    nj = len(times)
    rt = rng(3)
    cum = p["cum"]
    jtype = []
    for _ in range(nj):
        u = rt.random()
        k = 0
        while k < len(cum) - 1 and u > cum[k]:
            k += 1
        jtype.append(k)
    rtg = p.get("routing")
    ku = rtg["K"] if rtg else 1
    routes: list[list[int]] = []
    truncated = 0
    if rtg:
        jr_base = (int(p["seed"]) * 1_000_003 + rep) * 977 + 5 * 7919 + 13
        for j in range(nj):
            rr = random.Random(jr_base + j * 104729)
            cur = _pick(rtg["start"], rr.random())
            route, seen = [cur], {cur: 1}
            while len(route) < MAX_ROUTE_STEPS and cur in rtg["next"]:
                tgt = _pick(rtg["next"][cur], rr.random())
                if tgt < 0:
                    break
                if seen.get(tgt, 0) >= ku:
                    truncated += 1
                    break
                route.append(tgt)
                seen[tgt] = seen.get(tgt, 0) + 1
                cur = tgt
            routes.append(route)
    pos = [0] * nj
    uniforms = [[r.random() for _ in range(nj * ku)] for r in (rng(10 + i) for i in range(ns))]

    def visit(job, i):
        return routes[job][:pos[job] + 1].count(i) - 1 if rtg else 0

    def next_of(job, i):
        if rtg:
            k = pos[job] + 1
            return routes[job][k] if k < len(routes[job]) else None
        return i + 1 if i < ns - 1 else None

    def advance(job):
        if rtg:
            pos[job] += 1
    scale = p["scale"]
    lead_rng = rng(4)

    # --- state
    heap: list = []
    seq = 0

    def push(time, kind, a=0, b=0):
        nonlocal seq
        seq += 1
        heapq.heappush(heap, (time, kind, seq, a, b))

    for i, st in enumerate(S):
        st.queue, st.busy, st.blocked = deque(), 0, deque()
        if st.shifts:
            day = 0
            while day * 24.0 < horizon:
                for a, b, _n in st.shifts:
                    for edge in (a, b % 24.0):
                        te = day * 24.0 + edge
                        if 0 < te < horizon:
                            push(te, _CAP, i)
                day += 1
    if nj:
        push(times[0], _ARR, 0)
    arrive_t = times
    enter_t = [[0.0] * nj for _ in range(ns)]
    stock = inv["initial"] if inv else 0.0
    on_order = 0.0
    n_orders = 0
    flagged = bytearray(nj)
    order_idx = 0

    area_q = [0.0] * ns
    area_busy = [0.0] * ns
    area_cap = [0.0] * ns
    area_wip = 0.0
    area_stock = 0.0
    wait_sum = [0.0] * ns
    wait_n = [0] * ns
    lost_by = [0] * ns
    lead_times: list[float] = []
    departures = 0
    stockouts = 0
    in_system = 0
    last = 0.0

    def accumulate(now):
        nonlocal last, area_wip, area_stock
        lo, hi = max(last, warm), min(now, horizon)
        if hi > lo:
            dt = hi - lo
            for k, s in enumerate(S):
                area_q[k] += len(s.queue) * dt
                area_busy[k] += s.busy * dt
                area_cap[k] += _capacity(s, last) * dt
            area_wip += in_system * dt
            area_stock += stock * dt
        last = now

    def can_accept(s, now):
        if s.buffer is None or len(s.queue) < s.buffer:
            return True
        return not s.queue and s.busy < _capacity(s, now)

    def reorder(now):
        nonlocal on_order, n_orders, order_idx
        while stock + on_order <= inv["reorder_point"]:
            lt = inv["lead"](lead_rng.random())
            order_idx += 1
            n_orders += 1 if now >= warm else 0
            on_order += inv["order_quantity"]
            push(now + lt, _ORD, 0)
            if lt == 0:
                break  # arrives at once; the ORD event adds it before the next check

    def start(i, now):
        nonlocal stock, stockouts
        s = S[i]
        while s.queue and s.busy < _capacity(s, now):
            if inv and inv["station"] == i:
                if stock < inv["per_job"]:
                    head = s.queue[0]
                    if not flagged[head]:
                        flagged[head] = 1
                        if now >= warm:
                            stockouts += 1
                    return
                stock -= inv["per_job"]
                reorder(now)
            job = s.queue.popleft()
            if now >= warm:
                wait_sum[i] += now - enter_t[i][job]
                wait_n[i] += 1
            s.busy += 1
            push(now + s.sampler(uniforms[i][job * ku + visit(job, i)]) * scale[jtype[job]], _END, i, job)
            admit(i, now)

    def enter(job, i, now):
        enter_t[i][job] = now
        S[i].queue.append(job)
        start(i, now)

    def admit(i, now):
        s = S[i]
        while s.blocked and can_accept(s, now):
            job, frm = s.blocked.popleft()
            S[frm].busy -= 1
            advance(job)
            enter(job, i, now)
            start(frm, now)

    def finish(job, now):
        nonlocal in_system, departures
        in_system -= 1
        if now >= warm:
            departures += 1
            lead_times.append(now - arrive_t[job])

    def lose(i, now):
        nonlocal in_system
        in_system -= 1
        if now >= warm:
            lost_by[i] += 1

    while heap:
        now, kind, _s, a, b = heapq.heappop(heap)
        if now > horizon:
            break
        accumulate(now)
        if kind == _ARR:
            job = a
            if job + 1 < nj:
                push(times[job + 1], _ARR, job + 1)
            first = routes[job][0] if rtg else 0
            s0 = S[first]
            in_system += 1
            if s0.on_full == "lose" and not can_accept(s0, now):
                lose(first, now)
            else:
                enter(job, first, now)
        elif kind == _END:
            i, job = a, b
            s = S[i]
            ni = next_of(job, i)
            if ni is None:
                s.busy -= 1
                finish(job, now)
                start(i, now)
            else:
                nxt = S[ni]
                if can_accept(nxt, now):
                    s.busy -= 1
                    advance(job)
                    enter(job, ni, now)
                    start(i, now)
                elif nxt.on_full == "lose":
                    s.busy -= 1
                    lose(ni, now)
                    start(i, now)
                else:
                    nxt.blocked.append((job, i))
        elif kind == _CAP:
            start(a, now)
        else:  # order arrives
            stock += inv["order_quantity"]
            on_order -= inv["order_quantity"]
            start(inv["station"], now)
    accumulate(horizon)

    span = horizon - warm
    lead_times.sort()
    lost = sum(lost_by)
    waiting_hours = sum(area_q)
    cost = (sum(area_cap[i] * S[i].cost for i in range(ns)) + waiting_hours * p["c_wait"] + lost * p["c_lost"])
    if inv:
        cost += area_stock * p["c_hold"] + n_orders * p["c_order"] + stockouts * p["c_stockout"]
    out = {
        "throughput": departures / span,
        "lead_time_mean": sum(lead_times) / len(lead_times) if lead_times else float("nan"),
        "lead_time_p50": percentile(lead_times, 0.5),
        "lead_time_p90": percentile(lead_times, 0.9),
        "wip": area_wip / span,
        "lost_jobs": float(lost),
        "stockouts": float(stockouts),
        "total_cost": cost,
        "cost_per_hour": cost / span,
    }
    if rtg:
        out["truncated_routes"] = float(truncated)
    if inv:
        out["orders"] = float(n_orders)
        out["avg_stock"] = area_stock / span
    for i, s in enumerate(S):
        out[f"wait:{s.name}"] = wait_sum[i] / wait_n[i] if wait_n[i] else float("nan")
        out[f"util:{s.name}"] = area_busy[i] / area_cap[i] if area_cap[i] > 0 else float("nan")
        out[f"queue:{s.name}"] = area_q[i] / span
        out[f"lost:{s.name}"] = float(lost_by[i])
    return out


# ------------------------------------------------------------------ analytical
def erlang_c(c: int, lam: float, mu: float) -> Optional[dict]:
    a = lam / mu
    rho = a / c
    if rho >= 1:
        return None
    s = sum(a ** k / math.factorial(k) for k in range(c))
    top = a ** c / (math.factorial(c) * (1 - rho))
    pw = top / (s + top)
    wq = pw / (c * mu - lam)
    return {"servers": c, "rho": rho, "p_wait": pw, "wq_hours": wq, "w_hours": wq + 1 / mu, "lq": lam * wq}


def _analytic(model: dict, p: dict) -> Optional[dict]:
    if len(p["stations"]) != 1 or p["inventory"] or p["routing"] or p["arrival"]["type"] != "poisson":
        return None
    s = p["stations"][0]
    raw = model["stations"][0].get("service")
    if s.shifts or s.buffer is not None or not isinstance(raw, dict) or raw.get("dist") != "exponential":
        return None
    if len(p["scale"]) > 1 and any(abs(x - 1) > 1e-12 for x in p["scale"]):
        return None
    res = erlang_c(s.servers, p["arrival"]["rate"], 1 / float(raw["mean"]))
    return res and {"model": f"M/M/{s.servers}", **res}


# ----------------------------------------------------------------------- runner
def _metric_order(keys: list[str]) -> list[str]:
    head = [k for k in POLARITY if k in keys]
    return head + [k for k in keys if k not in POLARITY]


def run_model(model: dict, replications: Optional[int] = None, seed: Optional[int] = None,
              horizon: Optional[float] = None) -> dict:
    m = copy.deepcopy(model)
    if replications is not None:
        m["replications"] = replications
    if seed is not None:
        m["seed"] = seed
    if horizon is not None:
        m["horizon_hours"] = horizon
    p = _prepare(m)
    reps = [simulate(p, r) for r in range(p["reps"])]
    keys = _metric_order(list(reps[0].keys()))
    metrics = {k: mean_ci([r[k] for r in reps if not math.isnan(r[k])]) for k in keys}
    warnings = []
    # Little's law: WIP should equal throughput x mean lead time
    x, w, l_ = metrics["throughput"]["mean"], metrics["lead_time_mean"]["mean"], metrics["wip"]["mean"]
    littles = None
    if x is not None and w is not None and l_:
        rel = abs(l_ - x * w) / l_ if l_ > 0 else 0.0
        littles = {"wip": l_, "throughput_x_lead_time": x * w, "relative_difference": rel, "ok": rel <= 0.10,
                   "note": ("WIP matches throughput x lead time within 10%." if rel <= 0.10 else
                            "WIP and throughput x lead time differ by more than 10%: the horizon may be too short "
                            "or the warm-up too small for a steady state.")}
        if not littles["ok"]:
            warnings.append(littles["note"])
    analytic = _analytic(m, p)
    if analytic:
        first = p["stations"][0].name
        sim = metrics[f"wait:{first}"]
        analytic["simulated_wq_hours"] = sim["mean"]
        analytic["simulated_ci"] = [sim["ci_low"], sim["ci_high"]]
        if sim["mean"] is not None and analytic["wq_hours"] > 0:
            analytic["relative_difference"] = abs(sim["mean"] - analytic["wq_hours"]) / analytic["wq_hours"]
        if sim["ci_low"] is not None:
            analytic["within_ci"] = sim["ci_low"] <= analytic["wq_hours"] <= sim["ci_high"]
    for s in p["stations"]:
        u = metrics[f"util:{s.name}"]["mean"]
        if u is not None and u > 0.95:
            warnings.append(f"Station {s.name!r} is above 95% utilised: queues grow without bound and results "
                            "depend on the horizon.")
    cut = metrics.get("truncated_routes")
    if cut and cut["mean"]:
        warnings.append(f"About {cut['mean']:.1f} job routes per replication were cut at max_visits "
                        f"({p['routing']['K']}); raise routing.max_visits if rework can repeat more often.")
    if p["reps"] < 5:
        warnings.append("Fewer than 5 replications: the intervals are very wide or missing.")
    stations = [{"name": s.name, "servers": s.servers, "wait_hours": metrics[f"wait:{s.name}"],
                 "utilisation": metrics[f"util:{s.name}"], "avg_queue": metrics[f"queue:{s.name}"],
                 "lost": metrics[f"lost:{s.name}"]} for s in p["stations"]]
    return {
        "replications": p["reps"], "seed": p["seed"], "horizon_hours": p["horizon"], "warmup_hours": p["warm"],
        "metrics": metrics, "stations": stations,
        "checks": {"littles_law": littles, "analytic": analytic},
        "warnings": warnings, "caveat": CAVEAT,
        "_per_rep": reps,
    }


# ------------------------------------------------------------ compare and sweep
def compare_models(base: dict, variants: list[dict], replications: Optional[int] = None,
                   seed: Optional[int] = None) -> dict:
    if not variants:
        raise ScenarioError("variants: give at least one {name, overrides}")
    base_run = run_model(base, replications, seed)
    out, names = [], set()
    for v in variants:
        name = str(v.get("name") or f"variant {len(out) + 1}")
        if name in names:
            raise ScenarioError(f"variant names must be unique ({name!r})")
        names.add(name)
        model = apply_overrides(base, v.get("overrides") or {})
        run = run_model(model, replications, seed)
        rows = []
        for k in run["metrics"]:
            if k not in base_run["metrics"]:
                continue
            diffs = [a[k] - b[k] for a, b in zip(run["_per_rep"], base_run["_per_rep"])
                     if not (math.isnan(a[k]) or math.isnan(b[k]))]
            d = mean_ci(diffs)
            bm, vm = base_run["metrics"][k]["mean"], run["metrics"][k]["mean"]
            dist = d["ci_low"] is not None and (d["ci_low"] > 0 or d["ci_high"] < 0)
            pol = POLARITY.get(k.split(":")[0], None) if ":" in k else POLARITY.get(k)
            better = None
            if dist and pol is not None:
                better = "variant" if (d["mean"] > 0) == pol else "base"
            rows.append({"metric": k, "base": bm, "variant": vm, "delta": d["mean"], "delta_ci_low": d["ci_low"],
                         "delta_ci_high": d["ci_high"],
                         "delta_pct": (d["mean"] / bm * 100) if bm not in (None, 0) and d["mean"] is not None else None,
                         "distinguishable": bool(dist), "better": better})
        out.append({"name": name, "overrides": v.get("overrides") or {}, "rows": rows,
                    "warnings": run["warnings"], "run": _public(run)})
    return {"base": _public(base_run), "variants": out, "replications": base_run["replications"],
            "seed": base_run["seed"],
            "method": "Common random numbers: every variant uses the same seed, so arrivals and the underlying "
                      "service draws are shared; deltas are paired by replication. 'Distinguishable' means the 95% "
                      "interval of the paired difference excludes 0.",
            "caveat": CAVEAT}


def sweep_model(model: dict, param: str, values: list, replications: Optional[int] = None,
                seed: Optional[int] = None) -> dict:
    if not isinstance(values, list) or not 1 <= len(values) <= 40:
        raise ScenarioError("values: give between 1 and 40 values")
    points = []
    for v in values:
        run = run_model(apply_overrides(model, {param: v}), replications, seed)
        points.append({"value": v, "metrics": run["metrics"], "warnings": run["warnings"]})
    return {"param": param, "points": points, "replications": run["replications"], "seed": run["seed"],
            "caveat": CAVEAT}


def _public(run: dict) -> dict:
    return {k: v for k, v in run.items() if k != "_per_rep"}


# ------------------------------------------------------------------ calibration
def sample_warnings(n: int, what: str) -> list[str]:
    if n < VERY_SMALL_SAMPLE:
        return [f"{what}: only {n} observations. This is too little to describe the real variability; treat any "
                "scenario built on it as illustrative."]
    if n < SMALL_SAMPLE:
        return [f"{what}: {n} observations is a small sample; the fitted shape and the tails are uncertain."]
    return []


def describe(values: list[float]) -> dict:
    xs = sorted(values)
    n = len(xs)
    m = sum(xs) / n
    sd = math.sqrt(sum((x - m) ** 2 for x in xs) / (n - 1)) if n > 1 else 0.0
    return {"n": n, "mean": m, "sd": sd, "cv": sd / m if m else None, "min": xs[0], "p50": percentile(xs, 0.5),
            "p90": percentile(xs, 0.9), "max": xs[-1]}


def thin(values: list[float], cap: int = 2000) -> list[float]:
    xs = sorted(values)
    if len(xs) <= cap:
        return xs
    return [xs[int(i * (len(xs) - 1) / (cap - 1))] for i in range(cap)]


def fit_durations(values: list[float], what: str) -> dict:
    """Parameter estimates and an empirical spec from durations in hours."""
    if not values:
        raise ScenarioError(f"{what}: no usable values")
    d = describe(values)
    out: dict[str, Any] = {"sample_size": d["n"], "summary": d, "warnings": sample_warnings(d["n"], what)}
    specs: dict[str, Any] = {"empirical": {"dist": "empirical", "values": thin(values)}}
    if d["mean"] > 0:
        specs["exponential"] = {"dist": "exponential", "mean": d["mean"]}
        pos = [math.log(x) for x in values if x > 0]
        if len(pos) >= 2:
            mu = sum(pos) / len(pos)
            sigma = math.sqrt(sum((x - mu) ** 2 for x in pos) / (len(pos) - 1))
            if sigma > 0:
                specs["lognormal"] = {"dist": "lognormal", "mean": d["mean"], "sigma": sigma}
        if d["max"] > d["min"]:
            mode = min(max(3 * d["mean"] - d["min"] - d["max"], d["min"]), d["max"])
            specs["triangular"] = {"dist": "triangular", "min": d["min"], "mode": mode, "max": d["max"]}
    out["specs"] = specs
    cv = d["cv"]
    if d["n"] >= SMALL_SAMPLE:
        out["suggestion"] = {"dist": "empirical", "reason": "enough observations to resample them directly"}
    elif cv is not None and 0.8 <= cv <= 1.25:
        out["suggestion"] = {"dist": "exponential", "reason": f"coefficient of variation {cv:.2f} is close to 1"}
    else:
        out["suggestion"] = {"dist": "lognormal" if "lognormal" in specs else "empirical",
                             "reason": "small sample; a smooth shape is steadier than resampling a few points"}
    return out


def calibrate_values(values: list[float], kind: str, what: str, timestamps: Optional[list[float]] = None) -> dict:
    """values are durations in hours (service / interarrival); timestamps are epoch seconds (arrivals only)."""
    out = fit_durations(values, what)
    out["kind"] = kind
    if kind == "interarrival":
        mean = out["summary"]["mean"]
        out["arrivals"] = {"poisson": {"type": "poisson", "rate": 1 / mean} if mean > 0 else None,
                           "empirical": {"type": "empirical", "values": out["specs"]["empirical"]["values"]}}
        if timestamps and len(timestamps) > 1:
            ts = sorted(timestamps)
            span_h = (ts[-1] - ts[0]) / 3600
            days = len({int(x // 86400) for x in ts})
            counts = [0] * 24
            for x in ts:
                counts[int((x % 86400) // 3600)] += 1
            out["arrivals"]["schedule"] = {"type": "schedule", "rates": [c / days for c in counts]}
            out["span_hours"] = span_h
            out["arrivals"]["poisson"] = {"type": "poisson", "rate": (len(ts) - 1) / span_h} if span_h > 0 else None
            out["warnings"].append("Hourly schedule assumes every hour of the day has the same pattern on every "
                                   "day; weekdays and seasons are not separated.")
            if days < 7:
                out["warnings"].append(f"Only {days} calendar days of data: the hourly pattern rests on very few "
                                       "repetitions of each hour.")
    out["caveat"] = CAVEAT
    return out


# ----------------------------------------------------------------- service layer
_NAME = re.compile(r"^[A-Za-z0-9][A-Za-z0-9 _.\-]{0,59}$")


def _dir(services) -> Path:
    d = Path(services.config.data_dir) / "scenarios"
    d.mkdir(parents=True, exist_ok=True)
    return d


def _file(services, name: str) -> Path:
    if not isinstance(name, str) or not _NAME.match(name):
        raise ScenarioError("model name: 1-60 letters, digits, spaces, '_', '.' or '-'")
    return _dir(services) / (name + ".json")


def _load_column(services, dataset: str, column: str, as_time: bool) -> list[float]:
    table = services._resolve_table_by_name(dataset)
    q = lambda s: '"' + str(s).replace('"', '""') + '"'  # noqa: E731
    with services.engine.lock() as conn:
        cols = [r[0] for r in conn.execute(f"DESCRIBE {q(table)}").fetchall()]
        if column not in cols:
            raise ScenarioError(f"dataset {dataset!r} has no column {column!r}; columns: {cols}")
        expr = f"epoch(TRY_CAST({q(column)} AS TIMESTAMP))" if as_time else f"TRY_CAST({q(column)} AS DOUBLE)"
        rows = conn.execute(f"SELECT {expr} FROM {q(table)}").fetchall()
    return [float(r[0]) for r in rows if r[0] is not None and math.isfinite(float(r[0]))]


def _durations(services, dataset: str, column: str, unit: str) -> list[float]:
    if unit not in UNITS:
        raise ScenarioError(f"unit must be one of {sorted(set(UNITS))}")
    xs = [v * UNITS[unit] for v in _load_column(services, dataset, column, False)]
    return [x for x in xs if x >= 0]


def _interarrivals(services, dataset: str, column: str, unit: str, kind: str):
    if kind == "timestamps":
        ts = sorted(_load_column(services, dataset, column, True))
        return [(b - a) / 3600 for a, b in zip(ts, ts[1:])], ts
    return _durations(services, dataset, column, unit), None


def resolve_refs(services, model: dict) -> tuple[dict, list[dict]]:
    """Replace {dataset, column} empirical references with their values; list the calibration samples used."""
    m = copy.deepcopy(model)
    notes: list[dict] = []

    def visit(node, path):
        if isinstance(node, dict):
            if node.get("dataset") and node.get("column") and (node.get("dist") == "empirical"
                                                               or node.get("type") == "empirical"):
                is_arr = node.get("type") == "empirical"
                unit = node.get("unit", "hours")
                if is_arr:
                    xs, _ = _interarrivals(services, node["dataset"], node["column"], unit,
                                           node.get("kind", "interarrival"))
                else:
                    xs = _durations(services, node["dataset"], node["column"], unit)
                if not xs:
                    raise ScenarioError(f"{path}: no usable values in {node['dataset']}.{node['column']}")
                node["values"] = thin(xs)
                notes.append({"path": path, "dataset": node["dataset"], "column": node["column"],
                              "sample_size": len(xs), "warnings": sample_warnings(len(xs), path)})
                return
            for k, v in list(node.items()):
                visit(v, f"{path}.{k}" if path else k)
        elif isinstance(node, list):
            for i, v in enumerate(node):
                visit(v, f"{path}[{i}]")

    visit(m, "")
    return m, notes


def _model_for(services, name: Optional[str], model: Optional[dict]) -> dict:
    if model is not None:
        if isinstance(model, str):
            try:
                model = json.loads(model)
            except json.JSONDecodeError as exc:
                raise ScenarioError(f"model is not valid JSON: {exc}") from None
        return model
    if name:
        return get_model(services, name)["model"]
    raise ScenarioError("give 'name' (a saved model) or 'model' (inline JSON)")


def save_model(services, name: str, model: dict, source: str = "ui") -> dict:
    def do():
        if isinstance(model, str):
            try:
                mm = json.loads(model)
            except json.JSONDecodeError as exc:
                raise ScenarioError(f"model is not valid JSON: {exc}") from None
        else:
            mm = model
        resolved, _ = resolve_refs(services, mm) if _has_refs(mm) else (mm, [])
        _prepare(resolved)  # validate before storing
        path = _file(services, name)
        existed = path.exists()
        path.write_text(json.dumps(mm, indent=2, ensure_ascii=False), encoding="utf-8")
        return {"name": name, "saved": True, "replaced": existed,
                "_log_summary": f"saved scenario model {name!r}"}
    return services._log("scenario_save", source, None, {"name": name}, do)


def _has_refs(node) -> bool:
    if isinstance(node, dict):
        return bool(node.get("dataset") and node.get("column")) or any(_has_refs(v) for v in node.values())
    if isinstance(node, list):
        return any(_has_refs(v) for v in node)
    return False


def list_models(services) -> dict:
    out = []
    for f in sorted(_dir(services).glob("*.json")):
        try:
            m = json.loads(f.read_text(encoding="utf-8"))
        except json.JSONDecodeError:
            continue
        out.append({"name": f.stem, "stations": [s.get("name") for s in m.get("stations", [])],
                    "arrivals": (m.get("arrivals") or {}).get("type"),
                    "updated": f.stat().st_mtime})
    return {"models": out, "count": len(out)}


def get_model(services, name: str) -> dict:
    f = _file(services, name)
    if not f.exists():
        raise LookupError(f"unknown scenario model {name!r}; saved: {[m['name'] for m in list_models(services)['models']]}")
    return {"name": name, "model": json.loads(f.read_text(encoding="utf-8"))}


def delete_model(services, name: str, source: str = "ui") -> dict:
    def do():
        f = _file(services, name)
        if not f.exists():
            raise LookupError(f"unknown scenario model {name!r}")
        f.unlink()
        return {"name": name, "deleted": True, "_log_summary": f"deleted scenario model {name!r}"}
    return services._log("scenario_delete", source, None, {"name": name}, do)


def _fmt(v) -> Any:
    return "" if v is None or (isinstance(v, float) and math.isnan(v)) else v


def _save_rows(services, save_as: str, header: list[str], rows: list[list], source: str) -> str:
    buf = io.StringIO()
    w = csv.writer(buf, lineterminator="\n")
    w.writerow(header)
    for r in rows:
        w.writerow([_fmt(x) for x in r])
    saved = services.ingest_text(buf.getvalue(), save_as, "csv", None, source=source)
    return saved.get("name", save_as)


def run_scenario(services, name: Optional[str] = None, model: Optional[dict] = None,
                 replications: Optional[int] = None, seed: Optional[int] = None, horizon: Optional[float] = None,
                 save_as: Optional[str] = None, source: str = "ui") -> dict:
    def do():
        resolved, notes = resolve_refs(services, _model_for(services, name, model))
        res = run_model(resolved, replications, seed, horizon)
        per = res.pop("_per_rep")
        res["model_name"] = name
        res["calibration"] = notes
        for n in notes:
            res["warnings"].extend(n["warnings"])
        if save_as:
            keys = list(per[0].keys())
            res["saved_dataset"] = _save_rows(services, save_as, ["replication"] + keys,
                                              [[i + 1] + [r[k] for k in keys] for i, r in enumerate(per)], source)
        m = res["metrics"]
        res["_log_summary"] = (f"ran scenario {name or '(inline)'}: {res['replications']} replications, throughput "
                               f"{m['throughput']['mean']:.3f}/h, lead time {m['lead_time_mean']['mean']:.3f} h")
        return res
    return services._log("scenario_run", source, None, {"name": name, "replications": replications, "seed": seed}, do)


def compare_scenarios(services, name: Optional[str], model: Optional[dict], variants: list[dict],
                      replications: Optional[int] = None, seed: Optional[int] = None,
                      save_as: Optional[str] = None, source: str = "ui") -> dict:
    def do():
        resolved, notes = resolve_refs(services, _model_for(services, name, model))
        res = compare_models(resolved, variants or [], replications, seed)
        res["calibration"] = notes
        if save_as:
            rows = [[v["name"], r["metric"], r["base"], r["variant"], r["delta"], r["delta_ci_low"],
                     r["delta_ci_high"], r["distinguishable"], r["better"]] for v in res["variants"] for r in v["rows"]]
            res["saved_dataset"] = _save_rows(services, save_as, ["variant", "metric", "base", "variant_value", "delta",
                                                                  "delta_ci_low", "delta_ci_high", "distinguishable",
                                                                  "better"], rows, source)
        res["_log_summary"] = f"compared {len(res['variants'])} variants against the base model"
        return res
    return services._log("scenario_compare", source, None, {"name": name, "variants": [v.get("name") for v in variants or []]}, do)


def sweep_scenario(services, name: Optional[str], model: Optional[dict], param: str, values: list,
                   replications: Optional[int] = None, seed: Optional[int] = None,
                   save_as: Optional[str] = None, source: str = "ui") -> dict:
    def do():
        resolved, notes = resolve_refs(services, _model_for(services, name, model))
        res = sweep_model(resolved, param, values, replications, seed)
        res["calibration"] = notes
        if save_as:
            rows = [[pt["value"], k, m["mean"], m["ci_low"], m["ci_high"]] for pt in res["points"]
                    for k, m in pt["metrics"].items()]
            res["saved_dataset"] = _save_rows(services, save_as, ["value", "metric", "mean", "ci_low", "ci_high"],
                                              rows, source)
        res["_log_summary"] = f"swept {param} over {len(values)} values"
        return res
    return services._log("scenario_sweep", source, None, {"name": name, "param": param, "values": values}, do)


def calibrate_scenario(services, dataset: str, column: str, kind: str = "service", unit: str = "hours",
                       source: str = "ui") -> dict:
    def do():
        if kind == "service":
            xs = _durations(services, dataset, column, unit)
            res = calibrate_values(xs, "service", f"{dataset}.{column}")
        elif kind in ("interarrival", "timestamps"):
            xs, ts = _interarrivals(services, dataset, column, unit, kind)
            res = calibrate_values(xs, "interarrival", f"{dataset}.{column}", ts)
        else:
            raise ScenarioError("kind must be 'service', 'interarrival' or 'timestamps'")
        res.update({"dataset": dataset, "column": column, "unit": unit if kind != "timestamps" else "timestamps"})
        res["_log_summary"] = f"calibrated {kind} from {dataset}.{column}: n={res['sample_size']}"
        return res
    return services._log("scenario_calibrate", source, dataset, {"column": column, "kind": kind, "unit": unit}, do)

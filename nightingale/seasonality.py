"""Hour-of-day and day-of-week seasonality of case arrivals, tested against a constant rate.

The arrivals of an event log (the first event of every case) are counted per local hour of the day and per weekday
in the declared time zone. Each pattern is compared with a constant arrival rate by a Pearson chi-square test whose
expected counts are proportional to the hours of each slot actually observed (a window that covers some hours more
often than others does not create a pattern by itself). A pattern is significant when its p-value is below ``alpha``
and every expected count is at least 5; otherwise the test is reported but not trusted.

When the pattern is used, the arrival process becomes the scenario engine's ``schedule``: rates per hour for the
slots of one cycle (24 hours, or 168 when the weekday matters), multiplicative in hour and weekday and rescaled so
that the expected number of arrivals in the observed window equals the observed number. The engine reads the
schedule from model time 0, so time 0 is local midnight (24 h cycle) or Monday 00:00 (168 h cycle) of the declared
zone. Daylight-saving changes are not modelled inside the cycle.
"""
from __future__ import annotations

import math
from datetime import datetime
from typing import Any, Optional

from scipy import stats

from .texts import t as tx

ALPHA = 0.01
MIN_ARRIVALS = 30
MIN_EXPECTED = 5.0
MIN_SPAN_HOURS = 48.0
MAX_WINDOW_HOURS = 200_000
PEAK_RATIO = 1.5
DAYS = ("Mon", "Tue", "Wed", "Thu", "Fri", "Sat", "Sun")


def _exposure(first: float, last: float, tz) -> list[float]:
    """Hours observed inside each of the 168 (weekday, hour) slots of local time between two instants."""
    expo = [0.0] * 168
    cur = first
    while cur < last:
        dt = datetime.fromtimestamp(cur, tz)
        off = dt.utcoffset().total_seconds() if dt.utcoffset() is not None else 0.0
        into_hour = (cur + off) % 3600.0
        nxt = min(cur + (3600.0 - into_hour), last)
        expo[dt.weekday() * 24 + dt.hour] += (nxt - cur) / 3600.0
        cur = nxt
    return expo


def _test(counts: list[int], expo: list[float], alpha: float) -> dict:
    total_o, total_e = float(sum(counts)), float(sum(expo))
    idx = [i for i, e in enumerate(expo) if e > 1e-9]
    expected = [total_o * expo[i] / total_e for i in idx]
    stat = sum((counts[i] - e) ** 2 / e for i, e in zip(idx, expected))
    df = len(idx) - 1
    p = float(stats.chi2.sf(stat, df)) if df > 0 else None
    min_e = min(expected) if expected else 0.0
    reliable = min_e >= MIN_EXPECTED
    return {"statistic": stat, "df": df, "p_value": p, "alpha": alpha, "min_expected": min_e, "reliable": reliable,
            "significant": bool(p is not None and p < alpha and reliable)}


def _peaks(rates: list[float], base: float, circular: bool, label) -> list[dict]:
    """Local maxima (largest within two slots) whose rate is at least PEAK_RATIO times the mean rate."""
    n = len(rates)
    out = []
    for i, r in enumerate(rates):
        if base <= 0 or r < PEAK_RATIO * base:
            continue
        def nb(k):
            j = i + k
            return rates[j % n] if circular else (rates[j] if 0 <= j < n else None)
        if (all(nb(k) is None or r > nb(k) for k in (-1, 1))
                and all(nb(k) is None or r >= nb(k) for k in (-2, 2))):
            out.append({"slot": label(i), "rate_per_hour": r, "ratio_to_mean": r / base})
    return sorted(out, key=lambda x: -x["rate_per_hour"])


def analyse(starts: list[float], tz, alpha: float = ALPHA, mode: str = "auto") -> dict:
    """Seasonality of the given arrival instants (epoch seconds). Never raises: a log too short says why."""
    xs = sorted(starts)
    n = len(xs)
    base = {"evaluable": False, "significant": False, "used": None, "schedule": None, "alpha": alpha, "mode": mode,
            "arrivals": max(n - 1, 0), "method": tx("sn.method"), "caveat": tx("sn.caveat")}
    if n < MIN_ARRIVALS:
        return {**base, "reason": tx("sn.too_few", n=n, min=MIN_ARRIVALS)}
    span_h = (xs[-1] - xs[0]) / 3600.0
    if span_h < MIN_SPAN_HOURS:
        return {**base, "reason": tx("sn.too_short", hours=span_h, min=MIN_SPAN_HOURS)}
    if span_h > MAX_WINDOW_HOURS:
        return {**base, "reason": tx("sn.too_long", hours=span_h)}
    expo = _exposure(xs[0], xs[-1], tz)
    counts = [0] * 168
    for x in xs[1:]:                      # the first arrival opens the window; the rest are counted in it
        dt = datetime.fromtimestamp(x, tz)
        counts[dt.weekday() * 24 + dt.hour] += 1
    m = n - 1
    total_h = sum(expo)
    mean_rate = m / total_h
    ch = [sum(counts[d * 24 + h] for d in range(7)) for h in range(24)]
    eh = [sum(expo[d * 24 + h] for d in range(7)) for h in range(24)]
    cd = [sum(counts[d * 24:(d + 1) * 24]) for d in range(7)]
    ed = [sum(expo[d * 24:(d + 1) * 24]) for d in range(7)]
    rate_h = [c / e if e > 1e-9 else None for c, e in zip(ch, eh)]
    rate_d = [c / e if e > 1e-9 else None for c, e in zip(cd, ed)]
    t_h, t_d = _test(ch, eh, alpha), _test(cd, ed, alpha)
    hour = {"counts": ch, "exposure_hours": eh, "rate_per_hour": rate_h, "test": t_h,
            "peaks": _peaks([r or 0.0 for r in rate_h], mean_rate, True, lambda i: i)}
    day = {"counts": cd, "exposure_hours": ed, "rate_per_hour": rate_d, "test": t_d, "labels": list(DAYS),
           "peaks": _peaks([r or 0.0 for r in rate_d], mean_rate, False, lambda i: DAYS[i])}
    significant = t_h["significant"] or t_d["significant"]
    force = mode == "on"
    use_hour = t_h["significant"] or force
    use_day = t_d["significant"]
    out = {**base, "evaluable": True, "reason": None, "window": {"from_epoch": xs[0], "to_epoch": xs[-1], "hours": total_h},
           "mean_rate_per_hour": mean_rate, "hour_of_day": hour, "day_of_week": day, "significant": significant}
    if not (use_hour or use_day):
        out["verdict"] = tx("sn.constant", ph=_fmt_p(t_h["p_value"]), pd=_fmt_p(t_d["p_value"]))
        return out
    f = [(r / mean_rate if (use_hour and r is not None) else 1.0) for r in rate_h]
    g = [(r / mean_rate if (use_day and r is not None) else 1.0) for r in rate_d]
    rates = [mean_rate * f[h] * g[d] for d in range(7) for h in range(24)]
    expected = sum(r * e for r, e in zip(rates, expo))
    scale = m / expected if expected > 0 else 1.0
    rates = [r * scale for r in rates]
    if use_day:
        sched = {"type": "schedule", "rates": rates, "cycle_hours": 168}
        used = "hour_day" if use_hour else "day"
    else:
        sched = {"type": "schedule", "rates": [sum(rates[d * 24 + h] * expo[d * 24 + h] for d in range(7)) / eh[h]
                                                if eh[h] > 1e-9 else 0.0 for h in range(24)], "cycle_hours": 24}
        used = "hour"
    sched["origin"] = "monday_00:00" if sched["cycle_hours"] == 168 else "midnight"
    sched["note"] = tx("sn.origin_note")
    out.update(used=used, schedule=sched)
    peaks = ", ".join(f"{p['slot']:02d}h" for p in hour["peaks"]) if use_hour and hour["peaks"] else "-"
    out["verdict"] = tx("sn.pattern", used=tx("sn.used_" + used), ph=_fmt_p(t_h["p_value"]), pd=_fmt_p(t_d["p_value"]),
                        peaks=peaks, forced=tx("sn.forced") if force and not significant else "")
    return out


def _fmt_p(p: Optional[float]) -> str:
    if p is None:
        return "n/a"
    return "<0.0001" if p < 1e-4 else f"{p:.4f}"


def summary(a: dict) -> dict:
    """A compact version for recipes and logs: no per-slot arrays."""
    keep = {k: a.get(k) for k in ("evaluable", "significant", "used", "arrivals", "mean_rate_per_hour", "reason", "verdict")}
    if a.get("evaluable"):
        keep["hour_test"] = a["hour_of_day"]["test"]
        keep["day_test"] = a["day_of_week"]["test"]
        keep["peak_hours"] = [p["slot"] for p in a["hour_of_day"]["peaks"]]
    return keep

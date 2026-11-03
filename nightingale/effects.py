"""Effect of a change estimated from observational or before-after data.

Three designs, each with the assumptions it needs and the checks that can be run on them:

* ``did``    - difference-in-differences with a control group (parallel pre-trends test, placebo date,
               cluster-robust errors when units are given).
* ``its``    - interrupted time series: segmented regression with a pre-trend, a level change and a slope change,
               AR(1) errors when the residuals are autocorrelated, a linearity check on the pre-period and placebo dates.
* ``adjust`` - regression adjustment for listed confounders, with balance, overlap, stability of the estimate as
               confounders are added, a robustness value against an unmeasured confounder and an optional placebo
               outcome.

An estimate is never presented as established causation: the assumptions are listed with their status, the
untestable ones are named, and a failed testable one turns the verdict into a warning.
"""
from __future__ import annotations

import datetime as _dt
import math
from typing import Optional

import numpy as np

from .linfit import FitError, acf, coef_table, contrast, durbin_watson, ljung_box, load_frame, num, ols, ols_ar1, thin, tq, to_float


class EffectError(FitError):
    pass


CAUSAL_TEXT = ("This is an estimate of the effect of the change under the assumptions listed below. The data alone cannot establish "
               "causation: the untestable assumptions have to be argued from knowledge of how the data arose.")


# ---------------------------------------------------------------- helpers
def parse_time_point(value, time_kind: Optional[str]) -> float:
    if isinstance(value, (int, float)) and not isinstance(value, bool):
        return float(value)
    s = str(value).strip()
    try:
        return float(s)
    except ValueError:
        pass
    try:
        d = _dt.datetime.fromisoformat(s.replace("Z", "+00:00"))
    except ValueError:
        raise EffectError(f"cannot read {value!r} as a number or a date (use e.g. 2026-03-01)") from None
    if d.tzinfo is None:
        d = d.replace(tzinfo=_dt.timezone.utc)
    return d.timestamp()


def _is_treated(values: list, treated_value) -> np.ndarray:
    distinct = []
    for v in values:
        if v is not None and v not in distinct:
            distinct.append(v)
    if treated_value is None:
        norm = {str(d).lower() for d in distinct}
        if norm <= {"0", "1", "true", "false", "0.0", "1.0"}:
            treated_value = next(d for d in distinct if str(d).lower() in ("1", "true", "1.0")) if any(str(d).lower() in ("1", "true", "1.0") for d in distinct) else None
        else:
            raise EffectError(f"say which value of the group column marks the treated group (treated_value); values found: {distinct[:10]}")
    tv = str(treated_value).lower()
    out = np.array([str(v).lower() == tv for v in values], bool)
    if out.all() or not out.any():
        raise EffectError(f"treated_value {treated_value!r} selects {'every' if out.all() else 'no'} row; values found: {distinct[:10]}")
    return out


def _step(t: np.ndarray) -> float:
    u = np.unique(t)
    d = np.diff(u)
    return float(np.median(d)) if len(d) else 1.0


def _verdict(effect_row: dict, assumptions: list[dict], extra: str = "") -> dict:
    failed = [a for a in assumptions if a["status"] == "failed"]
    untestable = [a["name"] for a in assumptions if a["status"] == "not_testable"]
    text = CAUSAL_TEXT
    if failed:
        text = ("One or more testable assumptions failed (" + ", ".join(a["name"] for a in failed) + "): do not read the estimate as "
                "the effect of the change. " + CAUSAL_TEXT)
    return {"established": False, "assumptions_failed": [a["name"] for a in failed], "untestable_assumptions": untestable, "text": text + extra}


# ---------------------------------------------------------------- difference-in-differences
def _did_fit(y, treat, post, cov=None, groups=None, level=0.95):
    cols = [np.ones(len(y)), treat.astype(float), post.astype(float), (treat & post).astype(float)]
    names = ["intercept", "treated", "post", "treated x post"]
    if cov is not None and cov.size:
        for j in range(cov.shape[1]):
            cols.append(cov[:, j])
        names += [f"cov{j}" for j in range(cov.shape[1])]
    X = np.column_stack(cols)
    use_cluster = groups is not None and len(np.unique(groups)) >= 2
    fit = ols(X, y, "cluster" if use_cluster else "HC3", groups=groups if use_cluster else None)
    return fit, names, use_cluster


def did_effect(y, treat, t, t0: float, unit=None, covariates: Optional[np.ndarray] = None, cov_names: Optional[list] = None,
               placebo_time: Optional[float] = None, level: float = 0.95, time_label: str = "time") -> dict:
    y = np.asarray(y, float); t = np.asarray(t, float)
    keep = np.isfinite(y) & np.isfinite(t)
    if covariates is not None:
        keep &= np.isfinite(covariates).all(1)
    dropped = int((~keep).sum())
    y, t, treat = y[keep], t[keep], np.asarray(treat)[keep]
    unit = None if unit is None else np.asarray(unit)[keep]
    cov = None if covariates is None else covariates[keep]
    post = t >= t0
    cells = {"treated_pre": int((treat & ~post).sum()), "treated_post": int((treat & post).sum()),
             "control_pre": int((~treat & ~post).sum()), "control_post": int((~treat & post).sum())}
    if min(cells.values()) == 0:
        empty = [k for k, v in cells.items() if v == 0][0]
        raise EffectError(f"the cell {empty.replace('_', ' ')} is empty: difference-in-differences needs treated and control rows both before and after {t0:g}")
    warnings: list[str] = []
    if dropped:
        warnings.append(f"{dropped} rows with missing values were ignored.")
    if min(cells.values()) < 5:
        warnings.append(f"A cell has fewer than 5 rows ({cells}): the estimate and its interval are fragile.")
    fit, names, clustered = _did_fit(y, treat, post, cov, unit, level)
    row = coef_table(fit, names, level)[3]
    n_clusters = int(len(np.unique(unit))) if unit is not None else None
    if clustered and n_clusters < 10:
        warnings.append(f"Only {n_clusters} clusters: cluster-robust intervals are approximate with so few units.")
    if unit is None:
        warnings.append("No unit column: rows are treated as independent (robust to unequal variance only). With repeated measures of "
                        "the same units this understates the uncertainty; give a unit column for cluster-robust errors.")
    pre_mean_treated = float(y[treat & ~post].mean())
    eff = {"estimate": row["estimate"], "se": row["se"], "ci_low": row["ci_low"], "ci_high": row["ci_high"], "p_value": row["p_value"],
           "level": level, "relative_to_treated_pre_mean": row["estimate"] / pre_mean_treated if pre_mean_treated else None}
    means = {"treated_pre": float(y[treat & ~post].mean()), "treated_post": float(y[treat & post].mean()),
             "control_pre": float(y[~treat & ~post].mean()), "control_post": float(y[~treat & post].mean())}
    means["difference_in_differences"] = (means["treated_post"] - means["treated_pre"]) - (means["control_post"] - means["control_pre"])
    assumptions: list[dict] = []
    # --- parallel pre-trends
    pre = ~post
    ut = np.unique(t[pre])
    pre_trend = None
    if len(ut) >= 3 and (treat & pre).sum() >= 2 and (~treat & pre).sum() >= 2:
        dt = _step(t)
        tc = (t[pre] - t[pre].mean()) / dt
        X = np.column_stack([np.ones(pre.sum()), treat[pre].astype(float), tc, treat[pre].astype(float) * tc])
        g = unit[pre] if (unit is not None and len(np.unique(unit[pre])) >= 2) else None
        pf = ols(X, y[pre], "cluster" if g is not None else "HC3", groups=g)
        r = coef_table(pf, ["intercept", "treated", "slope", "differential slope"], level)[3]
        mean_post_t = t[post].mean(); mean_pre_t = t[pre].mean()
        bias = float(r["estimate"] * (mean_post_t - mean_pre_t) / dt)
        pre_trend = {"differential_slope_per_period": r["estimate"], "ci_low": r["ci_low"], "ci_high": r["ci_high"], "p_value": r["p_value"],
                     "pre_periods": int(len(ut)), "implied_bias_if_trend_continued": bias,
                     "implied_bias_vs_estimate": (bias / eff["estimate"]) if eff["estimate"] else None}
        violated = r["p_value"] is not None and r["p_value"] < 0.10
        assumptions.append({"name": "parallel pre-trends", "status": "failed" if violated else "passed",
                            "statement": "Without the change, treated and control groups would have moved in parallel.",
                            "detail": f"differential pre-period slope {r['estimate']:.4g} per period (95% CI {r['ci_low']:.4g} to {r['ci_high']:.4g}, p={r['p_value']:.3g}); "
                                      f"if that drift continued it would add about {bias:.4g} to the estimate."
                                      + ("" if violated else " Not rejecting parallel trends does not prove them, especially with few pre-periods.")})
        if violated:
            warnings.append(f"Parallel pre-trends rejected (p={r['p_value']:.3g}): the groups were already diverging before the change, "
                            f"so the difference-in-differences estimate mixes the effect with that drift (about {bias:.4g} if it continued).")
    else:
        assumptions.append({"name": "parallel pre-trends", "status": "not_testable",
                            "statement": "Without the change, treated and control groups would have moved in parallel.",
                            "detail": f"needs at least 3 pre-change time points with both groups; found {len(ut)}."})
        warnings.append("Parallel trends cannot be tested with fewer than 3 pre-change time points; the estimate relies on it unchecked.")
    # --- placebo date
    placebo = None
    if len(ut) >= 4:
        pt = placebo_time if placebo_time is not None else float(ut[len(ut) // 2])
        if not (ut[1] <= pt <= ut[-2] or placebo_time is not None):
            pt = float(ut[len(ut) // 2])
        pp = t[pre] >= pt
        tr_pre = treat[pre]
        if min((tr_pre & pp).sum(), (tr_pre & ~pp).sum(), (~tr_pre & pp).sum(), (~tr_pre & ~pp).sum()) > 0:
            g = unit[pre] if (unit is not None and len(np.unique(unit[pre])) >= 2) else None
            cv = None if cov is None else cov[pre]
            f2, nm2, _ = _did_fit(y[pre], tr_pre, pp, cv, g, level)
            r2 = coef_table(f2, nm2, level)[3]
            placebo = {"placebo_time": pt, "estimate": r2["estimate"], "ci_low": r2["ci_low"], "ci_high": r2["ci_high"], "p_value": r2["p_value"],
                       "excludes_zero": bool(r2["p_value"] is not None and r2["p_value"] < 0.05)}
            assumptions.append({"name": "no effect at a placebo date", "status": "failed" if placebo["excludes_zero"] else "passed",
                                "statement": "Using only pre-change data and a fake change date, the estimated 'effect' should be zero.",
                                "detail": f"placebo estimate {r2['estimate']:.4g} (95% CI {r2['ci_low']:.4g} to {r2['ci_high']:.4g}, p={r2['p_value']:.3g})."})
            if placebo["excludes_zero"]:
                warnings.append(f"A placebo change at {pt:g} shows an 'effect' of {r2['estimate']:.4g} (p={r2['p_value']:.3g}): the design finds effects where none was applied.")
    if placebo is None:
        assumptions.append({"name": "no effect at a placebo date", "status": "not_testable", "statement": "A fake change date inside the pre-period should show no effect.",
                            "detail": "the pre-period has fewer than 4 distinct time points on each side."})
    # --- serial correlation of residuals within clusters
    order_key = unit if unit is not None else treat.astype(int)
    r_ac = None
    res = fit["resid"]
    num_, den_ = 0.0, 0.0
    for key in np.unique(order_key):
        m = np.where(order_key == key)[0]
        m = m[np.argsort(t[m], kind="stable")]
        if len(m) >= 3:
            e = res[m] - res[m].mean()
            num_ += float(e[1:] @ e[:-1]); den_ += float(e @ e)
    if den_ > 0:
        r_ac = num_ / den_
        if r_ac > 0.3 and unit is None:
            warnings.append(f"Residuals are positively autocorrelated within groups (lag-1 {r_ac:.2f}) and no unit column was given: the interval is too narrow.")
    assumptions += [
        {"name": "no other change at the same time", "status": "not_testable", "statement": "Nothing else that affects the outcome changed for one group at the change date.", "detail": "cannot be tested from these data."},
        {"name": "no spillover between groups", "status": "not_testable", "statement": "The change did not affect the control group.", "detail": "cannot be tested from these data."},
        {"name": "stable group composition", "status": "not_testable", "statement": "The units in each group are comparable before and after.", "detail": "cannot be tested from these data."}]
    # --- chart: mean per group and time
    chart = []
    for gname, mask in (("treated", treat), ("control", ~treat)):
        for tv in np.unique(t[mask]):
            m = mask & (t == tv)
            chart.append({"group": gname, "time": float(tv), "mean": float(y[m].mean()), "n": int(m.sum())})
    return {"method": "did", "effect": eff, "means": means, "cells": cells, "pre_trend": pre_trend, "placebo": placebo,
            "assumptions": assumptions, "residual_autocorrelation": r_ac, "clustered_by_unit": clustered, "n_clusters": n_clusters,
            "intervention_time": t0, "chart": thin(chart, 600), "warnings": warnings,
            "causal_claim": _verdict(eff, assumptions), "n": int(len(y))}


# ---------------------------------------------------------------- interrupted time series
def _its_design(k: np.ndarray, post: np.ndarray, k0: float, period: Optional[int]) -> tuple[np.ndarray, list[str]]:
    cols = [np.ones(len(k)), k, post.astype(float), (k - k0) * post]
    names = ["intercept", "pre-trend (per step)", "level change", "slope change (per step)"]
    if period and period > 1:
        ph = (np.round(k).astype(int)) % period
        for r in range(1, period):
            cols.append((ph == r).astype(float))
            names.append(f"season {r}/{period}")
    return np.column_stack(cols), names


def _its_fit(X, y):
    """Ordinary least squares, switching to AR(1) errors (Prais-Winsten) when the residuals are autocorrelated."""
    fit = ols(X, y, "classical")
    lb = ljung_box(fit["resid"], min(10, max(2, len(y) // 5)), 0)
    if lb["p_value"] < 0.10 and fit["dof"] > 3:
        ar = ols_ar1(X, y)
        ar["model"] = f"AR(1) errors by Prais-Winsten (rho {ar['rho']:.2f}); residuals were autocorrelated (Ljung-Box p={lb['p_value']:.3g})"
        return ar
    fit["model"] = "ordinary least squares (no autocorrelation detected in the residuals)"
    return fit


def its_effect(y, t, t0: float, season_period: Optional[int] = None, level: float = 0.95) -> dict:
    y = np.asarray(y, float); t = np.asarray(t, float)
    keep = np.isfinite(y) & np.isfinite(t)
    y, t = y[keep], t[keep]
    warnings: list[str] = []
    ut, inv = np.unique(t, return_inverse=True)
    if len(ut) < len(t):
        y = np.bincount(inv, weights=y) / np.bincount(inv)
        t = ut
        warnings.append("Several rows share a timestamp: they were averaged into one value per time.")
    order = np.argsort(t)
    y, t = y[order], t[order]
    dt = _step(t)
    k = (t - t[0]) / dt
    k0 = (t0 - t[0]) / dt
    post = t >= t0
    npre, npost = int((~post).sum()), int(post.sum())
    if npre < 5 or npost < 2:
        raise EffectError(f"an interrupted time series needs at least 5 points before and 2 after the change (found {npre} and {npost})")
    if npre < 12:
        warnings.append(f"Only {npre} points before the change: the pre-trend, and so the counterfactual, is poorly determined.")
    if npost < 4:
        warnings.append(f"Only {npost} points after the change: the slope change cannot be judged.")
    if season_period and npre < 2 * season_period:
        raise EffectError(f"season_period {season_period} needs at least {2 * season_period} points before the change; found {npre}")
    X, names = _its_design(k, post, k0, season_period)
    n = len(y)
    fit = _its_fit(X, y)
    tab = coef_table(fit, names, level)
    lvl, slope = tab[2], tab[3]
    # effect j steps after the change: level + j * slope
    def at(j):
        c = np.zeros(X.shape[1]); c[2] = 1; c[3] = j
        return contrast(fit, c, level)
    last_j = float(k[-1] - k0)
    horizon = {"immediate": at(0.0), "at_end": {"steps_after": last_j, **at(last_j)}}
    horizon["immediate"]["steps_after"] = 0.0
    # counterfactual: pre-trend continued
    Xcf = X.copy()
    Xcf[:, 2] = 0; Xcf[:, 3] = 0
    cf = Xcf @ fit["beta"]
    cf_se = np.sqrt(np.maximum(np.einsum("ij,jk,ik->i", Xcf, fit["cov"], Xcf), 0))
    tcrit = tq(fit["vdof"], level)
    resid = fit["resid"]
    # diagnostics
    dw = durbin_watson(resid)
    r1 = float(acf(resid, 1)[0])
    lb = ljung_box(resid, min(10, max(2, n // 5)), 0)
    if "AR(1)" in fit["model"]:
        warnings.append(f"The series is autocorrelated (lag-1 {r1:.2f}); intervals use {fit['model'].split(';')[0]}. With a short series they remain approximate "
                        "and tend to be a little too narrow.")
    # pre-period linearity
    lin = None
    if npre >= 8:
        kp = k[~post]
        Xq = np.column_stack([np.ones(npre), kp, (kp - kp.mean()) ** 2] + ([(( np.round(kp).astype(int)) % season_period == r).astype(float) for r in range(1, season_period)] if season_period else []))
        fq = _its_fit(Xq, y[~post])
        rq = coef_table(fq, ["c", "l", "curvature"] + [f"s{r}" for r in range(1, (season_period or 1))], level)[2]
        lin = {"curvature_p_value": rq["p_value"], "curvature": rq["estimate"]}
    assumptions = []
    if lin is not None:
        bad = lin["curvature_p_value"] is not None and lin["curvature_p_value"] < 0.05
        assumptions.append({"name": "linear pre-trend continues", "status": "failed" if bad else "passed",
                            "statement": "Without the change the series would have kept its straight-line pre-trend.",
                            "detail": f"curvature in the pre-period p={lin['curvature_p_value']:.3g}." + (" The straight-line counterfactual is questionable." if bad else " (This cannot show the trend would have continued after the change.)")})
        if bad:
            warnings.append("The pre-period is curved: projecting its straight line forward misstates the counterfactual.")
    else:
        assumptions.append({"name": "linear pre-trend continues", "status": "not_testable", "statement": "Without the change the series would have kept its straight-line pre-trend.", "detail": "fewer than 8 pre-change points."})
    # placebo dates on pre-data only
    placebos = []
    if npre >= 10:
        for frac in (0.4, 0.6, 0.8):
            c = int(npre * frac)
            if c < 4 or npre - c < 3:
                continue
            tp = float(t[c])
            kk, pp = k[~post], (k[~post] >= k[c])
            Xp, nm = _its_design(kk, pp, k[c], season_period)
            if len(kk) <= Xp.shape[1] + 2:
                continue
            fp = _its_fit(Xp, y[~post])
            rp = coef_table(fp, nm, level)[2]
            placebos.append({"placebo_time": tp, "level_change": rp["estimate"], "ci_low": rp["ci_low"], "ci_high": rp["ci_high"],
                             "p_value": rp["p_value"], "excludes_zero": bool(rp["p_value"] is not None and rp["p_value"] < 0.05 / 3)})
    if placebos:
        rej = sum(p["excludes_zero"] for p in placebos)
        assumptions.append({"name": "no level change at placebo dates", "status": "failed" if rej >= 1 else "passed",
                            "statement": "Using only pre-change data and fake change dates, the estimated level change should be zero.",
                            "detail": f"{rej} of {len(placebos)} placebo dates show a significant level change (Bonferroni-adjusted for {len(placebos)} dates)."})
        if rej:
            warnings.append(f"{rej} of {len(placebos)} placebo dates show a 'level change': the series jumps without any change being applied, so the real estimate may be noise or a structural break.")
    else:
        assumptions.append({"name": "no level change at placebo dates", "status": "not_testable", "statement": "Fake change dates inside the pre-period should show no effect.", "detail": "fewer than 10 pre-change points."})
    assumptions += [
        {"name": "no other change at the same time", "status": "not_testable", "statement": "Nothing else that affects the series changed at the change date.", "detail": "cannot be tested from these data."},
        {"name": "stable measurement", "status": "not_testable", "statement": "How the series is measured did not change at the change date.", "detail": "cannot be tested from these data."}]
    eff = {"estimate": lvl["estimate"], "se": lvl["se"], "ci_low": lvl["ci_low"], "ci_high": lvl["ci_high"], "p_value": lvl["p_value"], "level": level,
           "definition": "immediate level change at the intervention, relative to the projected pre-trend"}
    chart = {"time": [float(v) for v in t], "observed": [float(v) for v in y], "fitted": [float(v) for v in X @ fit["beta"]],
             "counterfactual": [float(v) for v in cf], "counterfactual_lo": [float(a - tcrit * b) for a, b in zip(cf, cf_se)],
             "counterfactual_hi": [float(a + tcrit * b) for a, b in zip(cf, cf_se)], "post": [bool(v) for v in post]}
    for key in list(chart):
        if isinstance(chart[key], list) and len(chart[key]) > 400:
            idx = np.linspace(0, n - 1, 400).round().astype(int)
            chart[key] = [chart[key][i] for i in idx]
    return {"method": "its", "effect": eff, "slope_change": {"estimate": slope["estimate"], "ci_low": slope["ci_low"], "ci_high": slope["ci_high"], "p_value": slope["p_value"]},
            "pre_trend": {"per_step": tab[1]["estimate"], "ci_low": tab[1]["ci_low"], "ci_high": tab[1]["ci_high"]},
            "effect_over_time": horizon, "step": dt, "coefficients": tab, "diagnostics": {"durbin_watson": dw, "lag1_autocorrelation": r1, "ljung_box_p": lb["p_value"], "error_model": fit["model"], "r2": num(fit["r2"]), "linearity": lin},
            "placebos": placebos, "assumptions": assumptions, "chart": chart, "intervention_time": t0, "n_pre": npre, "n_post": npost,
            "warnings": warnings, "causal_claim": _verdict(eff, assumptions), "n": int(n)}


# ---------------------------------------------------------------- regression adjustment
def _design_from_columns(raw: dict[str, list]) -> tuple[np.ndarray, list[str], list[str]]:
    """Numeric columns as they are; text columns become 0/1 dummies (first level dropped, at most 12 levels)."""
    cols, names, notes = [], [], []
    for nm, vals in raw.items():
        f = to_float(vals)
        usable = [v for v in vals if v is not None]
        numeric = len(usable) > 0 and np.isfinite(f).sum() >= 0.9 * len(usable) and not any(isinstance(v, str) and not _numeric_str(v) for v in usable)
        if numeric:
            cols.append(f); names.append(nm)
        else:
            levels = sorted({str(v) for v in usable})
            if len(levels) > 12:
                raise EffectError(f"confounder {nm!r} has {len(levels)} distinct text values; group them first (at most 12 levels)")
            for lv in levels[1:]:
                cols.append(np.array([1.0 if (v is not None and str(v) == lv) else (np.nan if v is None else 0.0) for v in vals]))
                names.append(f"{nm}={lv}")
            notes.append(f"{nm}: text column coded as {len(levels) - 1} dummy variables (reference {levels[0]!r}).")
    return np.column_stack(cols) if cols else np.empty((len(next(iter(raw.values()))) if raw else 0, 0)), names, notes


def _numeric_str(s: str) -> bool:
    try:
        float(s)
        return True
    except ValueError:
        return False


def adjust_effect(y, treat, X, x_names: list[str], placebo_y=None, treat_is_binary: bool = True, level: float = 0.95) -> dict:
    y = np.asarray(y, float); treat = np.asarray(treat, float)
    keep = np.isfinite(y) & np.isfinite(treat) & (np.isfinite(X).all(1) if X.size else True)
    if placebo_y is not None:
        placebo_y = np.asarray(placebo_y, float)
    n0 = len(y)
    y, treat, X = y[keep], treat[keep], X[keep] if X.size else X
    if placebo_y is not None:
        placebo_y = placebo_y[keep]
    warnings: list[str] = []
    if n0 - len(y):
        warnings.append(f"{n0 - len(y)} rows with missing values were ignored.")
    n = len(y)
    p = X.shape[1] if X.size else 0
    if n < p + 6:
        raise EffectError(f"{n} rows are too few for {p} confounders and a treatment")
    if treat_is_binary:
        nt, nc = int((treat == 1).sum()), int((treat == 0).sum())
        if min(nt, nc) < 5:
            warnings.append(f"Only {min(nt, nc)} rows in the smaller group: estimates are fragile.")
    if np.ptp(treat) == 0:
        raise EffectError("the treatment does not vary")
    # naive estimate
    Xn = np.column_stack([np.ones(n), treat])
    fn = ols(Xn, y, "HC3")
    naive = coef_table(fn, ["intercept", "treatment"], level)[1]
    cols = [np.ones(n), treat] + ([X] if p else [])
    XA = np.column_stack([c if c.ndim == 2 else c[:, None] for c in cols])
    fa = ols(XA, y, "HC3")
    if fa["rank"] < XA.shape[1]:
        warnings.append("Some confounders are perfectly collinear with others or with the treatment; their coefficients are not identified.")
    tab = coef_table(fa, ["intercept", "treatment"] + list(x_names), level)
    adj = tab[1]
    eff = {"estimate": adj["estimate"], "se": adj["se"], "ci_low": adj["ci_low"], "ci_high": adj["ci_high"], "p_value": adj["p_value"], "level": level,
           "definition": "difference in the mean outcome between treated and untreated at the same confounder values (linear adjustment)" if treat_is_binary else "change in outcome per unit of the treatment variable, holding the confounders fixed",
           "naive_estimate": naive["estimate"], "naive_ci": [naive["ci_low"], naive["ci_high"]],
           "shift_from_adjustment": adj["estimate"] - naive["estimate"]}
    # balance and overlap
    balance, overlap = [], []
    if p and treat_is_binary:
        a, b = treat == 1, treat == 0
        for j, nm in enumerate(x_names):
            xt, xc = X[a, j], X[b, j]
            sd = math.sqrt((xt.var(ddof=1) + xc.var(ddof=1)) / 2) if len(xt) > 1 and len(xc) > 1 else 0.0
            smd = (xt.mean() - xc.mean()) / sd if sd > 0 else 0.0
            outside = float(np.mean((xt < xc.min()) | (xt > xc.max())))
            balance.append({"confounder": nm, "mean_treated": float(xt.mean()), "mean_control": float(xc.mean()), "standardized_difference": float(smd),
                            "imbalanced": bool(abs(smd) > 0.1), "treated_outside_control_range": outside})
            if outside > 0.05:
                warnings.append(f"{outside:.0%} of treated rows lie outside the control range of {nm}: the adjustment extrapolates there.")
        big = [bl["confounder"] for bl in balance if abs(bl["standardized_difference"]) > 0.25]
        if big:
            warnings.append(f"Large imbalance (|standardized difference| > 0.25) in {', '.join(big)}: linear adjustment relies heavily on the model form.")
        try:
            from sklearn.linear_model import LogisticRegression
            Z = (X - X.mean(0)) / np.where(X.std(0) > 0, X.std(0), 1)
            ps = LogisticRegression(C=1e3, max_iter=1000).fit(Z, treat).predict_proba(Z)[:, 1]
            lo = max(ps[a].min(), ps[b].min()); hi = min(ps[a].max(), ps[b].max())
            share = float(np.mean((ps < lo) | (ps > hi)))
            overlap = {"propensity_common_support": [float(lo), float(hi)], "share_outside_support": share,
                       "min": float(ps.min()), "max": float(ps.max())}
            if share > 0.05 or ps.max() > 0.97 or ps.min() < 0.03:
                warnings.append(f"Weak overlap: {share:.0%} of rows lie outside the propensity range shared by both groups (range {ps.min():.2f} to {ps.max():.2f}). "
                                "Some treated units have no comparable untreated unit.")
        except Exception:   # noqa: BLE001 - optional diagnostic
            overlap = {}
    # stability as confounders are added
    stability = []
    if p:
        for j in range(p + 1):
            cols_j = [np.ones(n), treat] + [X[:, i] for i in range(j)]
            fj = ols(np.column_stack(cols_j), y, "HC3")
            r = coef_table(fj, ["i", "t"] + x_names[:j], level)[1]
            stability.append({"adjusted_for": list(x_names[:j]), "estimate": r["estimate"], "ci_low": r["ci_low"], "ci_high": r["ci_high"]})
    # robustness to an unmeasured confounder (Cinelli-Hazlett robustness value)
    sens = None
    if adj["t"] is not None and fa["dof"] > 0:
        f = abs(adj["t"]) / math.sqrt(fa["dof"])
        rv = 0.5 * (math.sqrt(f ** 4 + 4 * f ** 2) - f ** 2)
        tcrit = tq(fa["dof"], level)
        fcrit = tcrit / math.sqrt(fa["dof"])
        rv_ci = 0.5 * (math.sqrt(max(f - fcrit, 0) ** 4 + 4 * max(f - fcrit, 0) ** 2) - max(f - fcrit, 0) ** 2) if f > fcrit else 0.0
        bench = None
        if p:
            r2s = [(row["t"] ** 2 / (row["t"] ** 2 + fa["dof"]), row["term"]) for row in tab[2:] if row["t"] is not None]
            if r2s:
                m = max(r2s)
                bench = {"confounder": m[1], "partial_r2_with_outcome": float(m[0])}
        sens = {"robustness_value": float(rv), "robustness_value_ci_bound": float(rv_ci), "strongest_observed_confounder": bench,
                "reading": f"An unmeasured confounder would need to explain at least {rv:.1%} of the remaining variance of both the treatment and the outcome to reduce the estimate to zero"
                           + (f" ({rv_ci:.1%} to make the interval include zero)." if rv_ci > 0 else ".")}
        if rv < 0.05:
            warnings.append(f"The estimate is fragile: a weak unmeasured confounder (robustness value {rv:.1%}) could remove it.")
    # placebo outcome
    placebo = None
    assumptions: list[dict] = []
    if placebo_y is not None:
        fp = ols(XA, placebo_y, "HC3")
        rp = coef_table(fp, ["intercept", "treatment"] + list(x_names), level)[1]
        bad = rp["p_value"] is not None and rp["p_value"] < 0.05
        placebo = {"estimate": rp["estimate"], "ci_low": rp["ci_low"], "ci_high": rp["ci_high"], "p_value": rp["p_value"], "excludes_zero": bool(bad)}
        assumptions.append({"name": "no effect on a placebo outcome", "status": "failed" if bad else "passed",
                            "statement": "An outcome measured before the change, or one the change cannot affect, should show no 'effect'.",
                            "detail": f"placebo estimate {rp['estimate']:.4g} (95% CI {rp['ci_low']:.4g} to {rp['ci_high']:.4g})."})
        if bad:
            warnings.append("The placebo outcome shows an 'effect': the adjusted comparison is picking up differences the listed confounders do not remove.")
    else:
        assumptions.append({"name": "no effect on a placebo outcome", "status": "not_testable", "statement": "An outcome the change cannot affect should show no 'effect'.", "detail": "no placebo outcome was given."})
    imb = [b["confounder"] for b in balance if b["imbalanced"]]
    if balance:
        assumptions.append({"name": "overlap (comparable units in both groups)", "status": "failed" if (overlap and overlap.get("share_outside_support", 0) > 0.05) else "passed",
                            "statement": "For every kind of unit there are both treated and untreated cases.",
                            "detail": (f"{overlap.get('share_outside_support', 0):.0%} of rows outside the shared propensity range." if overlap else "propensity overlap not computed.") +
                                      (f" Imbalanced before adjustment: {', '.join(imb)}." if imb else "")})
    assumptions += [
        {"name": "no unmeasured confounding", "status": "not_testable", "statement": "The listed confounders are all the common causes of treatment and outcome.",
         "detail": "cannot be tested; see the robustness value for how strong a missing confounder would have to be."},
        {"name": "correct functional form", "status": "not_testable", "statement": "The outcome is linear in the confounders.", "detail": "check the stability table; large shifts signal sensitivity."}]
    if not p:
        warnings.append("No confounders were listed: the 'adjusted' estimate is just the raw difference and is very likely confounded.")
    return {"method": "adjust", "effect": eff, "coefficients": tab, "balance": balance, "overlap": overlap, "stability": stability,
            "sensitivity": sens, "placebo_outcome": placebo, "assumptions": assumptions, "warnings": warnings,
            "causal_claim": _verdict(eff, assumptions), "n": int(n), "treatment_type": "binary" if treat_is_binary else "continuous"}


# ---------------------------------------------------------------- service wrappers
def estimate(services, method: str, dataset: str, outcome: str, time: Optional[str] = None, intervention=None, group: Optional[str] = None,
             treated_value=None, unit: Optional[str] = None, confounders: Optional[list] = None, treatment: Optional[str] = None,
             placebo_time=None, placebo_outcome: Optional[str] = None, season_period: Optional[int] = None, level: float = 0.95,
             source: str = "ui") -> dict:
    def do():
        conf = list(confounders or [])
        if method == "did":
            for need, v in (("time", time), ("intervention", intervention), ("group", group)):
                if v is None or v == "":
                    raise EffectError(f"difference-in-differences needs {need}")
            spec = {outcome: "num", time: "time", group: "raw"}
            if unit:
                spec[unit] = "raw"
            for c in conf:
                spec.setdefault(c, "raw")
            cols, meta = load_frame(services, dataset, spec)
            t0 = parse_time_point(intervention, meta["time_kind"])
            tr = _is_treated(cols[group], treated_value)
            covs, cn, notes = (None, [], [])
            if conf:
                covs, cn, notes = _design_from_columns({c: cols[c] for c in conf})
            res = did_effect(to_float(cols[outcome]), tr, to_float(cols[time]), t0, None if not unit else np.array([str(v) for v in cols[unit]]),
                             covs, cn, None if placebo_time in (None, "") else parse_time_point(placebo_time, meta["time_kind"]), level)
            res["warnings"].extend(notes)
            res["time_kind"] = meta["time_kind"]
        elif method == "its":
            for need, v in (("time", time), ("intervention", intervention)):
                if v is None or v == "":
                    raise EffectError(f"an interrupted time series needs {need}")
            cols, meta = load_frame(services, dataset, {outcome: "num", time: "time"})
            t0 = parse_time_point(intervention, meta["time_kind"])
            res = its_effect(to_float(cols[outcome]), to_float(cols[time]), t0, season_period, level)
            res["time_kind"] = meta["time_kind"]
        elif method == "adjust":
            if not treatment:
                raise EffectError("regression adjustment needs the treatment column")
            spec = {outcome: "num", treatment: "raw"}
            for c in conf:
                spec.setdefault(c, "raw")
            if placebo_outcome:
                spec[placebo_outcome] = "num"
            cols, _ = load_frame(services, dataset, spec)
            vals = cols[treatment]
            distinct = {str(v).lower() for v in vals if v is not None}
            binary = treated_value is not None or distinct <= {"0", "1", "true", "false", "0.0", "1.0"} or len(distinct) == 2
            if binary:
                tvec = _is_treated(vals, treated_value).astype(float)
            else:
                tvec = to_float(vals)
            Xc, cn, notes = _design_from_columns({c: cols[c] for c in conf}) if conf else (np.empty((len(tvec), 0)), [], [])
            res = adjust_effect(to_float(cols[outcome]), tvec, Xc, cn, to_float(cols[placebo_outcome]) if placebo_outcome else None, binary, level)
            res["warnings"].extend(notes)
        else:
            raise EffectError("method must be 'did', 'its' or 'adjust'")
        res["dataset"] = dataset
        res["outcome"] = outcome
        e = res["effect"]
        res["_log_summary"] = (f"{method} effect on {outcome} in {dataset}: {e['estimate']:.4g} "
                               f"(CI {e['ci_low']:.4g} to {e['ci_high']:.4g})" if e.get("ci_low") is not None else f"{method} effect on {outcome}: {e['estimate']:.4g}")
        if res["causal_claim"]["assumptions_failed"]:
            res["_log_summary"] += "; failed: " + ", ".join(res["causal_claim"]["assumptions_failed"])
        return res
    return services._log(f"effect_{method}", source, dataset, {"outcome": outcome, "method": method, "time": time, "group": group, "treatment": treatment}, do)

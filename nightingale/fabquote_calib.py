"""Calibrate the rates and yields of a fabrication quote from several real jobs, by least squares.

Each job gives its own measurements (volume, surface, toolpath...) and what it really cost or took (`actuals`: a line
item or a derived quantity such as `print_time_h` or `machine_time`). The parameters to calibrate (`fit`: print
speed, machine rate, material price...) are the unknowns; the quote's own formulas are the model, so nothing is
re-implemented here. The residual of every observation is relative, ``(model - real) / real``, which lets times in
hours and costs in currency be fitted together.

What comes back is honest about its limits: the fitted value of each parameter with a standard error and a confidence
interval from the Jacobian (only when there are more observations than parameters), the error before and after the
fit, a leave-one-job-out check of the error on jobs the fit did not see (three jobs or more), and the parameters that
the data cannot pin down (no observation depends on them, or they are collinear) instead of a made-up number.
"""
from __future__ import annotations

import copy
import math
from typing import Any, Optional

import numpy as np
from scipy import stats
from scipy.optimize import least_squares

from . import fabquote as fq
from .texts import t as tx

#: parameters worth calibrating by default: the rates and yields, not the geometry of a particular job
DEFAULT_FIT = {
    "fdm": ["print_speed_mm_s", "machine_rate_eur_h", "material_price_eur_kg", "labour_rate_eur_h"],
    "cnc_laser": ["feed_mm_min", "machine_rate_eur_h", "material_price_eur_m2", "labour_rate_eur_h"],
    "concrete": ["print_speed_mm_s", "machine_rate_eur_h", "mix_price_eur_m3", "labour_rate_eur_h"],
}
MAX_JOBS = 200
MAX_PARAMS = 12
COND_LIMIT = 1e10


def _num(v: Any) -> Optional[float]:
    return float(v) if isinstance(v, (int, float)) and not isinstance(v, bool) and math.isfinite(v) else None


def _clean_jobs(spec: dict, jobs: Any, fit: list[str]) -> list[dict]:
    if not isinstance(jobs, list) or not jobs:
        raise fq.FabQuoteError(tx("fq.calib_no_jobs"))
    if len(jobs) > MAX_JOBS:
        raise fq.FabQuoteError(tx("fq.calib_too_many_jobs", max=MAX_JOBS))
    nodes = {n["id"] for k in ("derived", "line_items") for n in spec.get(k) or []}
    out = []
    for i, j in enumerate(jobs):
        label = (j.get("label") if isinstance(j, dict) else None) or f"job {i + 1}"
        if not isinstance(j, dict) or not isinstance(j.get("actuals"), dict) or not j["actuals"]:
            raise fq.FabQuoteError(tx("fq.calib_job_no_actuals", job=label))
        meas = {}
        for k, v in (j.get("parameters") or {}).items():
            if k not in spec["parameters"]:
                raise fq.FabQuoteError(tx("fq.unknown_param", name=k, known=sorted(spec["parameters"])))
            if k in fit:
                raise fq.FabQuoteError(tx("fq.calib_job_sets_fit", job=label, name=k))
            d = dict(v) if isinstance(v, dict) else {"value": v}
            d.setdefault("source", "measured")
            meas[k] = d
        acts = {}
        for k, v in j["actuals"].items():
            if k not in nodes:
                raise fq.FabQuoteError(tx("fq.actual_unknown_item", item=k, known=sorted(nodes)))
            x = _num(v)
            if x is None or x <= 0:
                raise fq.FabQuoteError(tx("fq.calib_actual_value", job=label, item=k))
            acts[k] = x
        out.append({"label": str(label), "parameters": meas, "actuals": acts})
    return out


def _predict(spec: dict, job: dict, fit: list[str], x: np.ndarray) -> dict[str, float]:
    ov = dict(job["parameters"])
    for n, v in zip(fit, x):
        ov[n] = {"value": float(v), "source": "calibrated"}
    res = fq.evaluate(fq._apply_overrides(spec, ov))
    by = {r["id"]: r for r in res["items"] + res["derived"]}
    out = {}
    for k in job["actuals"]:
        r = by[k]
        if r["status"] != "ok" or r.get("value") is None:
            raise fq.FabQuoteError(tx("fq.calib_job_blocked", job=job["label"], item=k, why=r.get("reason") or r["status"]))
        out[k] = float(r["value"])
    return out


def _observations(jobs: list[dict]) -> list[tuple[int, str, float]]:
    return [(i, k, v) for i, j in enumerate(jobs) for k, v in j["actuals"].items()]


def _residuals(spec: dict, jobs: list[dict], fit: list[str], x: np.ndarray, only: Optional[set[int]] = None) -> np.ndarray:
    r = []
    for i, j in enumerate(jobs):
        if only is not None and i not in only:
            continue
        pred = _predict(spec, j, fit, x)
        for k, real in j["actuals"].items():
            r.append((pred[k] - real) / real)
    return np.array(r, dtype=float)


def _jacobian(spec, jobs, fit, x, only=None) -> np.ndarray:
    r0 = _residuals(spec, jobs, fit, x, only)
    J = np.zeros((len(r0), len(x)))
    for c in range(len(x)):
        h = max(abs(x[c]) * 1e-6, 1e-9)
        xp = x.copy()
        xp[c] += h
        J[:, c] = (_residuals(spec, jobs, fit, xp, only) - r0) / h
    return J


def _bounds(spec: dict, fit: list[str], x0: np.ndarray):
    lo, hi = [], []
    for n, v in zip(fit, x0):
        if spec["parameters"][n]["unit"].strip() == "%":
            lo.append(0.0)
        else:
            lo.append(abs(v) * 1e-6 if v else 1e-9)
        hi.append(np.inf)
    return np.array(lo), np.array(hi)


def _solve(spec, jobs, fit, x0, only=None):
    lo, hi = _bounds(spec, fit, x0)
    x_start = np.clip(x0, lo, None)
    # least_squares needs a start strictly inside the bounds
    x_start = np.where(x_start <= lo, lo + np.maximum(abs(x0) * 1e-3, 1e-9), x_start)
    sol = least_squares(lambda x: _residuals(spec, jobs, fit, x, only), x_start, bounds=(lo, hi),
                        x_scale=np.maximum(np.abs(x0), 1e-9), method="trf", xtol=1e-12, ftol=1e-12, gtol=1e-12,
                        max_nfev=200)
    return sol


def _errors(r: np.ndarray) -> dict:
    return {"mape_pct": float(np.mean(np.abs(r)) * 100.0), "rmse_pct": float(math.sqrt(np.mean(r ** 2)) * 100.0),
            "max_abs_pct": float(np.max(np.abs(r)) * 100.0), "n": int(len(r))}


def _per_target(obs, r) -> dict[str, float]:
    acc: dict[str, list[float]] = {}
    for (_, k, _), v in zip(obs, r):
        acc.setdefault(k, []).append(abs(v))
    return {k: float(np.mean(v) * 100.0) for k, v in acc.items()}


def calibrate(spec: dict, jobs: list[dict], fit: Optional[list[str]] = None, level: float = 0.95) -> dict:
    """Fit `fit` (default: the usual rates of the process) to the jobs. Pure: it does not change the spec."""
    spec = fq.normalize_spec(spec)
    if not (0.5 <= level < 1):
        raise fq.FabQuoteError(tx("fq.calib_level"))
    fit = list(fit) if fit else [n for n in DEFAULT_FIT[spec["process"]] if n in spec["parameters"]]
    if not fit or len(fit) > MAX_PARAMS or len(set(fit)) != len(fit):
        raise fq.FabQuoteError(tx("fq.calib_fit_names", max=MAX_PARAMS))
    for n in fit:
        if n not in spec["parameters"]:
            raise fq.FabQuoteError(tx("fq.unknown_param", name=n, known=sorted(spec["parameters"])))
        if spec["parameters"][n].get("value") is None:
            raise fq.FabQuoteError(tx("fq.calib_fit_missing", name=n))
    jobs = _clean_jobs(spec, jobs, fit)
    obs = _observations(jobs)
    warnings: list[str] = []
    x0 = np.array([float(spec["parameters"][n]["value"]) for n in fit])
    r_before = _residuals(spec, jobs, fit, x0)
    J0 = _jacobian(spec, jobs, fit, x0)
    norms = np.linalg.norm(J0, axis=0)
    live = [c for c in range(len(fit)) if norms[c] > 1e-9 * max(1.0, norms.max())]
    dead = [fit[c] for c in range(len(fit)) if c not in live]
    if not live:
        raise fq.FabQuoteError(tx("fq.calib_nothing_depends", names=", ".join(fit)))
    if dead:
        warnings.append(tx("fq.calib_not_identifiable", names=", ".join(dead)))
    fit_live = [fit[c] for c in live]
    x_live0 = x0[live]
    n_obs, p = len(obs), len(fit_live)
    if n_obs < p:
        raise fq.FabQuoteError(tx("fq.calib_underdetermined", n=n_obs, p=p))
    sol = _solve(spec, jobs, fit_live, x_live0)
    x = sol.x
    r_after = _residuals(spec, jobs, fit_live, x)
    J = _jacobian(spec, jobs, fit_live, x)
    dof = n_obs - p
    sv = np.linalg.svd(J, compute_uv=False)
    rank_ok = bool(sv.size and sv[-1] > 0 and sv[0] / sv[-1] < COND_LIMIT)
    cov = None
    if dof > 0 and rank_ok:
        s2 = float(np.sum(r_after ** 2) / dof)
        cov = s2 * np.linalg.inv(J.T @ J)
    elif dof <= 0:
        warnings.append(tx("fq.calib_no_dof", n=n_obs, p=p))
    else:
        warnings.append(tx("fq.calib_collinear"))
    tcrit = float(stats.t.ppf(0.5 + level / 2.0, dof)) if dof > 0 else None
    at_bound = []
    rows = []
    for c, n in enumerate(fit_live):
        spec_p = spec["parameters"][n]
        se = float(math.sqrt(max(cov[c, c], 0.0))) if cov is not None else None
        lo_b, _ = _bounds(spec, [n], np.array([x_live0[c]]))
        if x[c] <= lo_b[0] * (1 + 1e-6) + 1e-12 and spec_p["unit"].strip() != "%":
            at_bound.append(n)
        rows.append({"parameter": n, "unit": spec_p["unit"], "before": float(x_live0[c]), "after": float(x[c]),
                     "change_pct": (float(x[c]) / x_live0[c] - 1) * 100.0 if x_live0[c] else None,
                     "std_error": se, "ci_low": float(x[c] - tcrit * se) if se is not None else None,
                     "ci_high": float(x[c] + tcrit * se) if se is not None else None,
                     "rel_uncertainty_pct": (tcrit * se / abs(x[c]) * 100.0) if se is not None and x[c] else None,
                     "identifiable": True})
    for n in dead:
        rows.append({"parameter": n, "unit": spec["parameters"][n]["unit"], "before": float(spec["parameters"][n]["value"]),
                     "after": float(spec["parameters"][n]["value"]), "change_pct": 0.0, "std_error": None, "ci_low": None,
                     "ci_high": None, "rel_uncertainty_pct": None, "identifiable": False})
    if at_bound:
        warnings.append(tx("fq.calib_at_bound", names=", ".join(at_bound)))
    eb, ea = _errors(r_before), _errors(r_after)
    improvement = ((eb["mape_pct"] - ea["mape_pct"]) / eb["mape_pct"] * 100.0) if eb["mape_pct"] > 1e-12 else 0.0
    cv = None
    if len(jobs) >= 3:
        errs, ok = [], True
        for i in range(len(jobs)):
            keep = {k for k in range(len(jobs)) if k != i}
            if sum(len(jobs[k]["actuals"]) for k in keep) < p:
                ok = False
                break
            sub = _solve(spec, jobs, fit_live, x, keep)
            pred = _predict(spec, jobs[i], fit_live, sub.x)
            errs.extend((pred[k] - real) / real for k, real in jobs[i]["actuals"].items())
        if ok:
            ecv = np.array(errs)
            # the "before" figure on the same observations is the plain error of the old rates
            cv = {"mape_pct": float(np.mean(np.abs(ecv)) * 100.0), "rmse_pct": float(math.sqrt(np.mean(ecv ** 2)) * 100.0),
                  "before_mape_pct": eb["mape_pct"], "method": tx("fq.calib_cv_method"), "n": int(len(ecv))}
    else:
        warnings.append(tx("fq.calib_cv_needs_jobs"))
    per_job = []
    k = 0
    for j in jobs:
        m = len(j["actuals"])
        per_job.append({"job": j["label"], "before_pct": float(np.mean(np.abs(r_before[k:k + m])) * 100.0),
                        "after_pct": float(np.mean(np.abs(r_after[k:k + m])) * 100.0)})
        k += m
    return {"process": spec["process"], "fit": fit, "jobs": len(jobs), "observations": n_obs, "dof": dof,
            "level": level, "parameters": rows,
            "error": {"before": eb, "after": ea, "improvement_pct": improvement,
                      "per_target_before_pct": _per_target(obs, r_before), "per_target_after_pct": _per_target(obs, r_after),
                      "per_job": per_job},
            "cross_validation": cv, "converged": bool(sol.status > 0), "warnings": warnings,
            "method": tx("fq.calib_method", level=int(round(level * 100))), "caveat": tx("fq.calib_caveat")}


def calibrated_overrides(result: dict, level_text: str = "") -> dict:
    """The parameter overrides that apply a calibration to a quote (only the identifiable ones)."""
    ov = {}
    for r in result["parameters"]:
        if not r["identifiable"]:
            continue
        unc = f" ± {r['std_error']:.4g}" if r["std_error"] is not None else ""
        ov[r["parameter"]] = {"value": r["after"], "source": "calibrated",
                              "note": tx("fq.calib_note", n=result["jobs"], value=f"{r['after']:.4g}{unc}", unit=r["unit"])}
    return ov


def simulate_job(spec: dict, params: dict, measurements: dict, targets: list[str]) -> dict:
    """Helper for tests and docs: what the quote predicts for a job under the given rates (no noise)."""
    s = fq._apply_overrides(copy.deepcopy(spec), {**{k: {"value": v, "source": "measured"} for k, v in measurements.items()},
                                                   **{k: {"value": v, "source": "assumed"} for k, v in params.items()}})
    res = fq.evaluate(s)
    by = {r["id"]: r for r in res["items"] + res["derived"]}
    return {k: by[k]["value"] for k in targets}


def calibrate_quote(services, name: str, jobs: list[dict], fit: Optional[list[str]] = None, apply: bool = False,
                    level: float = 0.95, source: str = "ui") -> dict:
    """Calibrate a stored quote against real jobs. With `apply` the fitted values replace the quote's rates (source
    'calibrated', with their uncertainty in the note) and the change in the total is reported; otherwise nothing
    is written."""
    def do():
        doc = fq._load(services, name)
        spec = doc["spec"]
        out = calibrate(spec, jobs, fit, level)
        out["name"] = name
        out["applied"] = False
        if apply:
            if not out["converged"]:
                raise fq.FabQuoteError(tx("fq.calib_not_converged"))
            before = fq.evaluate(spec)
            new_spec, after = fq.compute(spec, calibrated_overrides(out), before)
            new_spec = fq.normalize_spec(new_spec)
            doc["spec"] = new_spec
            doc.setdefault("calibrations", []).append({
                "at": fq._now(services), "jobs": out["jobs"], "observations": out["observations"], "fit": out["fit"],
                "parameters": [{k: r[k] for k in ("parameter", "before", "after", "std_error", "ci_low", "ci_high")} for r in out["parameters"]],
                "error": {"before_mape_pct": out["error"]["before"]["mape_pct"], "after_mape_pct": out["error"]["after"]["mape_pct"]}})
            fq._save(services, name, doc)
            out["applied"] = True
            out["diff"] = fq.diff_results(spec, before, new_spec, after)
            out["result"] = fq.public(after)
        out["_log_summary"] = (f"{'applied' if apply else 'computed'} calibration of {name!r} from {out['jobs']} jobs "
                               f"(error {out['error']['before']['mape_pct']:.1f}% -> {out['error']['after']['mape_pct']:.1f}%)")
        return out
    return services._log("fabquote_calibrate", source, None, {"name": name, "jobs": len(jobs or []), "apply": apply}, do)

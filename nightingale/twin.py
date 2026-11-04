"""Dynamic twins learned from measurements.

A twin is an interpretable linear dynamic model fitted by least squares to a regularly sampled time series:

* ``arx``   - a multi-output ARX model (each output depends on lagged outputs and lagged inputs). It is a linear
  state-space model in companion form, so its poles and steady-state gains are reported. The lag order is chosen on
  a selection block that is kept apart from the final held-out block, so the reported validation is not tuned on.
* ``fopdt`` - first order plus dead time for one input and one output (a step-response style model).

The last part of the record is held out. Validation reports NRMSE (one-step and free-run), the residual
autocorrelation check (Ljung-Box and the 95% band), and the empirical coverage of the prediction interval. Parameter
uncertainty comes from a residual bootstrap; what-if simulations carry intervals that combine coefficient
uncertainty and residual noise, and refuse to extrapolate far outside the input range seen in training.
An interval is not a guarantee: it does not contain model-structure error or changes after the record ends.
"""
from __future__ import annotations

import datetime as _dt
import json
import math
import re
import time as _time
from pathlib import Path
from typing import Any, Optional

import numpy as np
from scipy import optimize, signal

from .linfit import FitError, ljung_box, load_frame, thin, to_float
from .texts import register, t as tx

register({
    "tw.warn_uncalibrated": {"en": "The prediction interval could not be calibrated on the held-out block (too few rows); its coverage is not guaranteed.",
                             "es": "El intervalo de predicción no se pudo calibrar en el bloque reservado (muy pocas filas); su cobertura no está garantizada."},
    "tw.calib_method": {"en": "split-conformal width per horizon on free-run errors from every origin of the held-out block",
                        "es": "anchura conformal por horizonte con los errores en simulación libre desde cada origen del bloque reservado"},
})


class TwinError(FitError):
    pass


_NAME = re.compile(r"^[A-Za-z0-9_][A-Za-z0-9 _.\-]{0,59}$")
MIN_TRAIN = 30
MIN_VALID = 10


# ---------------------------------------------------------------- data preparation
def prepare_series(t, Y, U) -> dict:
    """Sort by time, drop unusable rows and keep the longest run of regularly spaced finite rows."""
    t = np.asarray(t, float)
    Y = np.asarray(Y, float).reshape(len(t), -1)
    U = np.asarray(U, float).reshape(len(t), -1)
    order = np.argsort(t, kind="stable")
    t, Y, U = t[order], Y[order], U[order]
    valid = np.isfinite(t) & np.isfinite(Y).all(1) & np.isfinite(U).all(1)
    if valid.sum() < MIN_TRAIN + MIN_VALID:
        raise TwinError(tx("tw.few_rows", n=int(valid.sum()), need=MIN_TRAIN + MIN_VALID))
    tv = t[valid]
    d = np.diff(tv)
    pos = d[d > 0]
    if len(pos) == 0:
        raise TwinError(tx("tw.time_increasing"))
    dt = float(np.median(pos))
    n = len(t)
    link = np.zeros(n - 1, bool)
    dd = np.diff(t)
    link[:] = valid[:-1] & valid[1:] & (dd > 0.5 * dt) & (dd < 1.5 * dt)
    best, cur, bs, cs = 0, 0, 0, 0
    for i in range(n - 1):
        if link[i]:
            if cur == 0:
                cs = i
            cur += 1
            if cur > best:
                best, bs = cur, cs
        else:
            cur = 0
    start, end = (bs, bs + best + 1) if best else (0, 0)
    seg = end - start
    if seg < MIN_TRAIN + MIN_VALID:
        raise TwinError(tx("tw.short_stretch", seg=seg, need=MIN_TRAIN + MIN_VALID))
    info = {"rows_total": int(n), "rows_used": int(seg), "rows_left_out": int(n - seg), "dt": dt,
            "irregular_steps": int(((dd <= 0.5 * dt) | (dd >= 1.5 * dt)).sum()),
            "start_index": int(start)}
    warnings = []
    if n - seg > 0:
        warnings.append(tx("tw.warn_left_out", left=n - seg, n=n, seg=seg))
    return {"t": t[start:end], "Y": Y[start:end], "U": U[start:end], "dt": dt, "info": info, "warnings": warnings}


# ---------------------------------------------------------------- ARX machinery
def _hist_len(p: int, s: int) -> int:
    return max(p, s + p - 1)


def _build(Y: np.ndarray, U: np.ndarray, p: int, s: int):
    n = len(Y)
    H = _hist_len(p, s)
    cols = [np.ones(n - H)]
    for k in range(1, p + 1):
        cols.append(Y[H - k: n - k])
    for m in range(p):
        cols.append(U[H - s - m: n - s - m])
    X = np.column_stack([c if c.ndim == 2 else c[:, None] for c in cols])
    return X, Y[H:], H


def _regressor_names(outputs, inputs, p, s):
    names = ["const"]
    names += [f"{o}[t-{k}]" for k in range(1, p + 1) for o in outputs]
    names += [f"{i}[t-{s + m}]" for m in range(p) for i in inputs]
    return names


def _fit_arx(Y, U, p, s):
    X, T, H = _build(Y, U, p, s)
    nreg = X.shape[1]
    if len(X) < 4 * nreg:
        raise TwinError(tx("tw.lag_few", p=p, rows=len(X), nreg=nreg))
    beta, *_ = np.linalg.lstsq(X, T, rcond=None)
    theta = beta.T  # (no, nreg)
    res = T - X @ beta
    return theta, res, H


def _simulate(theta, p, s, histY, histU, Ufut, noise=None):
    """Free-run simulation for D parameter sets at once. theta (D,no,nreg) -> (D,T,no)."""
    theta = np.asarray(theta, float)
    if theta.ndim == 2:
        theta = theta[None]
    D, no, _ = theta.shape
    H = _hist_len(p, s)
    T = len(Ufut)
    Yb = np.empty((D, H + T, no))
    Yb[:, :H] = histY[-H:][None]
    Ub = np.vstack([histU[-H:], Ufut])
    for t in range(H, H + T):
        parts = [np.ones((D, 1))]
        parts += [Yb[:, t - k, :] for k in range(1, p + 1)]
        parts += [np.broadcast_to(Ub[t - s - m], (D, Ub.shape[1])) for m in range(p)]
        x = np.concatenate(parts, axis=1)
        y = np.einsum("dor,dr->do", theta, x)
        if noise is not None:
            y = y + noise[:, t - H]
        Yb[:, t] = y
    return Yb[:, H:]


def _companion_poles(theta, p, no):
    A = [theta[:, 1 + k * no: 1 + (k + 1) * no] for k in range(p)]
    top = np.hstack(A)
    comp = np.zeros((no * p, no * p))
    comp[:no] = top
    if p > 1:
        comp[no:, :-no] = np.eye(no * (p - 1))
    return np.linalg.eigvals(comp)


def _gains(theta, p, no, ni):
    """Steady-state gain of each input on each output: (I - sum A)^-1 sum B."""
    sumA = sum(theta[:, 1 + k * no: 1 + (k + 1) * no] for k in range(p))
    base = 1 + p * no
    sumB = sum(theta[:, base + m * ni: base + (m + 1) * ni] for m in range(p))
    M = np.eye(no) - sumA
    if abs(np.linalg.det(M)) < 1e-6:
        return None
    return np.linalg.solve(M, sumB)


def _is_stable(theta, p, no) -> bool:
    return bool(np.max(np.abs(_companion_poles(theta, p, no))) < 1.0 - 1e-9)


def _bootstrap_arx(theta, p, s, Y, U, res, B, rng, scale):
    H = _hist_len(p, s)
    no = Y.shape[1]
    idx = rng.integers(0, len(res), size=(B, len(Y) - H))
    noise = res[idx]
    Ys = _simulate(np.repeat(theta[None], B, 0), p, s, Y[:H], U[:H], U[H:], noise)
    Ys = np.concatenate([np.repeat(Y[None, :H], B, 0), Ys], axis=1)
    draws, failed = [], 0
    for b in range(B):
        yb = Ys[b]
        if not np.isfinite(yb).all() or np.abs(yb).max() > 1e3 * scale:
            failed += 1
            continue
        try:
            th, _, _ = _fit_arx(yb, U, p, s)
        except TwinError:
            failed += 1
            continue
        if not _is_stable(th, p, no):
            failed += 1
            continue
        draws.append(th)
    return (np.array(draws) if draws else np.empty((0,) + theta.shape)), failed


CAL_LEVELS = [0.5, 0.8, 0.9, 0.95, 0.99]


def _calibrate_arx(th_draws, res_tr, p, s, H, Y, U, ntr, n, rng, max_h: int = 30, min_origins: int = 25):
    """Split-conformal calibration of the simulated interval per horizon on the held-out block.

    From each origin in the held-out block the model is run forward on the measured inputs; the observed future is
    scored against the simulated mean in units of the simulated interval half-width (lower or upper side). The
    (finite-sample corrected) quantile of those scores, per horizon and level, is the factor that widens (or narrows)
    the interval so that its coverage on data like the held-out block matches the nominal level."""
    no = Y.shape[1]
    hmax = min(max_h, (n - ntr) // 3)
    origins = list(range(ntr, n - 2))
    if hmax < 3 or len(origins) < min_origins or len(th_draws) < 20:
        return None
    scores = [[[] for _ in range(no)] for _ in range(hmax)]
    for o in origins:
        h = min(hmax, n - o)
        idx = rng.integers(0, len(res_tr), size=(len(th_draws), h))
        paths = _simulate(th_draws, p, s, Y[o - H:o], U[o - H:o], U[o:o + h], res_tr[idx])
        mid = paths.mean(0)
        lo = np.percentile(paths, 5, axis=0)       # a wide-enough reference band; scores are ratios to the 90 % half-widths
        hi = np.percentile(paths, 95, axis=0)
        for k in range(h):
            for j in range(no):
                y = Y[o + k, j]
                d = y - mid[k, j]
                w = (hi[k, j] - mid[k, j]) if d >= 0 else (mid[k, j] - lo[k, j])
                if w > 1e-12:
                    scores[k][j].append(abs(d) / w)
    scale = np.ones((len(CAL_LEVELS), hmax, no))
    for k in range(hmax):
        for j in range(no):
            pool = [v for kk in range(max(0, k - 2), min(hmax, k + 3)) for v in scores[kk][j]]   # neighbouring horizons share the noise
            if len(pool) < min_origins:
                return None
            arr = np.sort(np.array(pool))
            for li, lv in enumerate(CAL_LEVELS):
                q = min(1.0, math.ceil((len(arr) + 1) * lv) / len(arr))
                scale[li, k, j] = float(np.quantile(arr, q))
    # the reference band is the 90 % one: scores are relative to its half-width, so a level-L interval is c_L * halfwidth(90 %)
    return {"levels": CAL_LEVELS, "horizon": hmax, "origins": len(origins), "scale": scale.tolist(), "reference_level": 0.9}


def _apply_calibration(calib, level, mean, lo, hi, nominal_level):
    """Rebuild lo/hi around the mean from the calibrated half-width quantile at `level` (reference band = 90 %)."""
    sc = np.array(calib["scale"], float)                  # (levels, horizon, outputs)
    T, no = mean.shape
    out_lo, out_hi = lo.copy(), hi.copy()
    # half-widths of the 90 % reference band from the paths: recover them from lo/hi at `nominal_level` is not exact,
    # so predict() passes the 90 % band in lo/hi (see predict)
    for k in range(T):
        kk = min(k, sc.shape[1] - 1)
        for j in range(no):
            c = float(np.interp(level, calib["levels"], sc[:, kk, j]))
            out_lo[k, j] = mean[k, j] - c * (mean[k, j] - lo[k, j])
            out_hi[k, j] = mean[k, j] + c * (hi[k, j] - mean[k, j])
    return out_lo, out_hi


def metrics(obs: np.ndarray, pred: np.ndarray) -> dict:
    obs, pred = np.asarray(obs, float), np.asarray(pred, float)
    e = obs - pred
    rmse = float(np.sqrt(np.mean(e ** 2)))
    rng_ = float(obs.max() - obs.min())
    sst = float(((obs - obs.mean()) ** 2).sum())
    return {"rmse": rmse, "mae": float(np.mean(np.abs(e))), "bias": float(np.mean(-e)),
            "nrmse": rmse / rng_ if rng_ > 0 else None,
            "r2": 1 - float(e @ e) / sst if sst > 0 else None,
            "fit_percent": 100 * (1 - float(np.linalg.norm(e)) / math.sqrt(sst)) if sst > 0 else None}




def _free_run_score(theta, p, s, Y, U, a, b):
    """Free-run NRMSE on rows [a, b) started from measured history before a; inf when unstable."""
    H = _hist_len(p, s)
    if a < H:
        return float("inf")
    sim = _simulate(theta, p, s, Y[a - H:a], U[a - H:a], U[a:b])[0]
    if not np.isfinite(sim).all() or np.abs(sim).max() > 1e6 * (np.abs(Y).max() + 1):
        return float("inf")
    sc = []
    for j in range(Y.shape[1]):
        m = metrics(Y[a:b, j], sim[:, j])
        sc.append(m["nrmse"] if m["nrmse"] is not None else m["rmse"])
    return float(np.mean(sc))


def select_lag(Y, U, s, max_lag, n_first):
    """Choose the lag order on a selection block at the end of the first part (never the held-out block)."""
    cut = int(n_first * 0.75)
    cands = []
    for p in range(1, max_lag + 1):
        row = {"lag": p, "selection_nrmse": None, "stable": None, "note": None}
        try:
            theta, _, _ = _fit_arx(Y[:cut], U[:cut], p, s)
            score = _free_run_score(theta, p, s, Y, U, cut, n_first)
            row["stable"] = bool(_is_stable(theta, p, Y.shape[1]))
            row["selection_nrmse"] = None if not math.isfinite(score) else score
            row["_score"] = score
        except TwinError as exc:
            row["note"] = str(exc)
            row["_score"] = float("inf")
        cands.append(row)
    scores = [c["_score"] for c in cands]
    best = min(scores)
    if not math.isfinite(best):
        raise TwinError(tx("tw.no_stable_lag"))
    chosen = next(c["lag"] for c in cands if c["_score"] <= best * 1.02 + 1e-12)
    for c in cands:
        c.pop("_score")
    return chosen, cands


# ---------------------------------------------------------------- first order plus dead time
def _fopdt_series(par, u, dt, u0):
    y0, K, tau, theta = par
    a = math.exp(-dt / max(tau, 1e-9))
    m = int(theta // dt)
    frac = theta / dt - m
    pad = np.concatenate([np.full(m + 2, u[0]), u])
    k = np.arange(len(u)) + m + 2
    ud = (1 - frac) * pad[k - m] + frac * pad[k - m - 1]
    x = signal.lfilter([0.0, K * (1 - a)], [1.0, -a], ud - u0)
    return y0 + x, ud, a


def _fopdt_fit(y, u, dt, start=None):
    span = dt * (len(y) - 1)
    u0 = float(u[0])
    du = float(u[-1] - u0)
    if np.ptp(u) <= 0:
        raise TwinError(tx("tw.input_flat"))

    def resid(par):
        return _fopdt_series(par, u, dt, u0)[0] - y

    lo = [-np.inf, -np.inf, dt * 0.2, 0.0]
    hi = [np.inf, np.inf, span * 2, span * 0.5]
    k0 = (y[-max(3, len(y) // 20):].mean() - y[0]) / du if abs(du) > 0 else 0.0
    starts = [start] if start is not None else [
        [y[0], k0, tau, th] for tau in (span / 20, span / 5, span / 2) for th in (0.0, 2 * dt, span / 10)]
    best = None
    for st in starts:
        st = np.clip(st, [-1e18, -1e18, lo[2] * 1.01, 0.0], [1e18, 1e18, hi[2] * 0.99, hi[3] * 0.99])
        try:
            r = optimize.least_squares(resid, st, bounds=(lo, hi), x_scale=[max(np.ptp(y), 1e-9), max(abs(k0), 1e-3),
                                                                           span / 5, span / 10])
        except (ValueError, FloatingPointError):
            continue
        if best is None or r.cost < best.cost:
            best = r
    if best is None:
        raise TwinError(tx("tw.fopdt_noconv"))
    return best.x, u0


def _fopdt_predict(par, hist_u, y_last, Ufut, dt, u0):
    """Predicted outputs for the steps after the record; the state is anchored on the last measured output."""
    y0, K, tau, theta = par
    a = math.exp(-dt / max(tau, 1e-9))
    m = int(theta // dt)
    frac = theta / dt - m
    full = np.concatenate([np.full(m + 2, hist_u[0]), hist_u, Ufut])
    n_hist = len(hist_u)
    k = np.arange(n_hist - 1, n_hist - 1 + len(Ufut)) + m + 2
    ud = (1 - frac) * full[k - m] + frac * full[k - m - 1]
    x0 = y_last - y0
    out = signal.lfilter([K * (1 - a)], [1.0, -a], ud - u0, zi=[a * x0])[0]
    return y0 + out


def _fit_fopdt_twin(prep, ntr, nval, outputs, inputs, B, rng, level):
    Y, U, dt = prep["Y"][:, 0], prep["U"][:, 0], prep["dt"]
    n = len(Y)
    par, u0 = _fopdt_fit(Y[:ntr], U[:ntr], dt)
    tr_fit = _fopdt_series(par, U[:ntr], dt, u0)[0]
    sim_val = _fopdt_predict(par, U[:ntr], Y[ntr - 1], U[ntr:], dt, u0)
    # final model on every row
    par_all, u0_all = _fopdt_fit(Y, U, dt, start=par)
    fit_all = _fopdt_series(par_all, U, dt, u0_all)[0]
    res_all = Y - fit_all
    draws = []
    block = max(2, int(math.sqrt(n)))
    for _ in range(B):
        starts = rng.integers(0, max(1, n - block), size=int(math.ceil(n / block)))
        e = np.concatenate([res_all[s0:s0 + block] for s0 in starts])[:n]
        try:
            pb, _ = _fopdt_fit(fit_all + e, U, dt, start=par_all)
        except TwinError:
            continue
        draws.append(pb)
    return {"par": par, "par_all": par_all, "u0": u0, "u0_all": u0_all, "draws": np.array(draws),
            "tr_fit": tr_fit, "sim_val": sim_val, "res_all": res_all, "dt": dt}


# ---------------------------------------------------------------- fit entry point
def fit_series(t, Y, U, outputs: list[str], inputs: list[str], model: str = "arx", max_lag: Optional[int] = None,
               input_lag_start: int = 1, validation_fraction: float = 0.25, n_boot: int = 200, seed: int = 0,
               level: float = 0.95, time_kind: Optional[str] = None) -> dict:
    """Fit a twin to arrays. Returns {"twin": stored object, "report": what the caller sees}."""
    if model not in ("arx", "fopdt", "auto"):
        raise TwinError(tx("tw.model_kind"))
    if not inputs:
        raise TwinError(tx("tw.need_input"))
    if not outputs:
        raise TwinError(tx("tw.need_output"))
    if not 0.1 <= validation_fraction <= 0.5:
        raise TwinError(tx("tw.valfrac"))
    if input_lag_start not in (0, 1, 2, 3):
        raise TwinError(tx("tw.lagstart"))
    single = len(outputs) == 1 and len(inputs) == 1
    if model in ("fopdt", "auto") and not single:
        if model == "fopdt":
            raise TwinError(tx("tw.fopdt_siso"))
        model = "arx"
    prep = prepare_series(t, Y, U)
    t, Y, U, dt = prep["t"], prep["Y"], prep["U"], prep["dt"]
    n = len(Y)
    warnings = list(prep["warnings"])
    rng = np.random.default_rng(seed)
    nval = max(MIN_VALID, int(round(n * validation_fraction)))
    ntr = n - nval
    if ntr < MIN_TRAIN:
        raise TwinError(tx("tw.holdout", nval=nval, ntr=ntr, need=MIN_TRAIN))
    for j, nm in enumerate(inputs):
        if np.ptp(U[:ntr, j]) <= 1e-12:
            raise TwinError(tx("tw.input_const", name=nm))
    for j, nm in enumerate(outputs):
        if np.ptp(Y[:ntr, j]) <= 1e-12:
            raise TwinError(tx("tw.output_const", name=nm))
    scale = float(np.abs(Y).max() + 1.0)
    out_rng = {o: [float(Y[:, j].min()), float(Y[:, j].max())] for j, o in enumerate(outputs)}
    in_rng = {o: [float(U[:, j].min()), float(U[:, j].max())] for j, o in enumerate(inputs)}
    no, ni = len(outputs), len(inputs)

    results: dict[str, Any] = {}
    # ---- ARX
    if model in ("arx", "auto"):
        s = input_lag_start
        ml = max_lag or 8
        ml = max(1, min(ml, (ntr // 6) // max(1, no + ni) , 12))
        p, cands = select_lag(Y, U, s, ml, ntr)
        theta_tr, res_tr, H = _fit_arx(Y[:ntr], U[:ntr], p, s)
        poles = _companion_poles(theta_tr, p, no)
        if np.max(np.abs(poles)) >= 1:
            warnings.append(tx("tw.warn_pole", mag=float(np.max(np.abs(poles)))))
        Xv, Tv, Hv = _build(Y, U, p, s)
        rows = np.arange(Hv, n)
        sel = rows >= ntr
        one_step = (Xv @ theta_tr.T)[sel]
        obs_val = Tv[sel]
        sim = _simulate(theta_tr, p, s, Y[ntr - H:ntr], U[ntr - H:ntr], U[ntr:])[0]
        val = {"one_step": {}, "free_run": {}}
        for j, o in enumerate(outputs):
            val["one_step"][o] = metrics(obs_val[:, j], one_step[:, j])
            val["free_run"][o] = metrics(Y[ntr:, j], sim[:, j])
            # persistence baseline: predict y[t] = y[t-1]
            val["one_step"][o]["persistence_nrmse"] = metrics(obs_val[:, j], Y[ntr - 1: n - 1, j])["nrmse"]
        res_val = obs_val - one_step
        # bootstrap on the fitting block to calibrate the interval on the held-out block
        Btr = min(n_boot, 120)
        th_draws, failed_tr = _bootstrap_arx(theta_tr, p, s, Y[:ntr], U[:ntr], res_tr, Btr, rng, scale)
        cover = None
        sim_lo = sim_hi = None
        calib = _calibrate_arx(th_draws, res_tr, p, s, H, Y, U, ntr, n, np.random.default_rng(seed + 17)) if len(th_draws) >= 20 else None
        if calib is None:
            warnings.append(tx("tw.warn_uncalibrated"))
        if len(th_draws) >= 20:
            idx = rng.integers(0, len(res_tr), size=(len(th_draws), nval))
            paths = _simulate(th_draws, p, s, Y[ntr - H:ntr], U[ntr - H:ntr], U[ntr:], res_tr[idx])
            lo_q, hi_q = (1 - level) / 2 * 100, (1 + level) / 2 * 100
            sim_lo = np.percentile(paths, lo_q, axis=0)
            sim_hi = np.percentile(paths, hi_q, axis=0)
            cover = {o: float(np.mean((Y[ntr:, j] >= sim_lo[:, j]) & (Y[ntr:, j] <= sim_hi[:, j]))) for j, o in enumerate(outputs)}
        # final model on all rows
        theta, res_all, H = _fit_arx(Y, U, p, s)
        draws, failed = _bootstrap_arx(theta, p, s, Y, U, res_all, n_boot, rng, scale)
        if len(draws) < max(10, n_boot // 5):
            warnings.append(tx("tw.warn_boot_stable", n=len(draws), total=n_boot))
        names = _regressor_names(outputs, inputs, p, s)
        pdraw = draws if len(draws) else theta[None]
        lo_q, hi_q = (1 - level) / 2 * 100, (1 + level) / 2 * 100
        params = []
        for j, o in enumerate(outputs):
            for r, nm in enumerate(names):
                col = pdraw[:, j, r]
                params.append({"output": o, "term": nm, "estimate": float(theta[j, r]), "std": float(col.std(ddof=1)) if len(col) > 1 else None,
                               "ci_low": float(np.percentile(col, lo_q)) if len(col) > 1 else None,
                               "ci_high": float(np.percentile(col, hi_q)) if len(col) > 1 else None})
        G = _gains(theta, p, no, ni)
        gains = []
        if G is None:
            warnings.append(tx("tw.warn_gain_undef"))
        else:
            gd = [g for g in (_gains(d, p, no, ni) for d in pdraw) if g is not None]
            for j, o in enumerate(outputs):
                for i, nm in enumerate(inputs):
                    col = np.array([g[j, i] for g in gd]) if gd else np.array([])
                    gains.append({"output": o, "input": nm, "gain": float(G[j, i]),
                                  "ci_low": float(np.percentile(col, lo_q)) if len(col) > 1 else None,
                                  "ci_high": float(np.percentile(col, hi_q)) if len(col) > 1 else None})
        poles_all = _companion_poles(theta, p, no)
        lb_tr = {o: ljung_box(res_tr[:, j], 10, p) for j, o in enumerate(outputs)}
        lb_val = {o: ljung_box(res_val[:, j], min(10, max(2, len(res_val) // 4)), 0) if len(res_val) >= 12 else None
                  for j, o in enumerate(outputs)}
        results["arx"] = {"p": p, "s": s, "theta": theta, "theta_tr": theta_tr, "names": names, "val": val, "params": params,
                          "gains": gains, "poles": poles_all, "lag_candidates": cands, "lb_tr": lb_tr, "lb_val": lb_val,
                          "draws": pdraw, "res_all": res_all, "coverage": cover, "calib": calib, "sim": sim, "sim_lo": sim_lo, "sim_hi": sim_hi,
                          "failed": failed, "stable": bool(np.max(np.abs(poles_all)) < 1), "H": H}
    # ---- FOPDT
    if model in ("fopdt", "auto"):
        fo = _fit_fopdt_twin(prep, ntr, nval, outputs, inputs, min(n_boot, 100), rng, level)
        fo["val"] = metrics(Y[ntr:, 0], fo["sim_val"])
        fo["lb_tr"] = ljung_box(Y[:ntr, 0] - fo["tr_fit"], 10, 0)
        results["fopdt"] = fo

    chosen = model
    if model == "auto":
        a_sc = results["arx"]["val"]["free_run"][outputs[0]]["nrmse"]
        f_sc = results["fopdt"]["val"]["nrmse"]
        a_sc = math.inf if a_sc is None else a_sc
        f_sc = math.inf if f_sc is None else f_sc
        chosen = "fopdt" if f_sc <= a_sc * 1.05 else "arx"   # prefer the simpler model when it is about as good
        warnings.append(tx("tw.warn_auto", a=a_sc, f=f_sc, chosen=chosen.upper()))
    twin: dict[str, Any] = {"model": chosen, "outputs": outputs, "inputs": inputs, "dt": dt, "time_kind": time_kind,
                            "level": level, "seed": seed,
                            "training": {"rows": n, "fit_rows": ntr, "validation_rows": nval, "input_range": in_rng,
                                         "output_range": out_rng, "t_first": float(t[0]), "t_last": float(t[-1])}}
    report: dict[str, Any] = {"model": chosen, "outputs": outputs, "inputs": inputs, "sampling": {**prep["info"], "dt": dt},
                              "split": {"fit_rows": ntr, "validation_rows": nval, "validation_fraction": validation_fraction},
                              "nrmse_definition": tx("tw.nrmse_def"), "level": level, "caveat": tx("tw.caveat")}
    obs_val_series = Y[ntr:]
    t_val = t[ntr:]
    if chosen == "arx":
        r = results["arx"]
        H = r["H"]
        twin.update({"p": r["p"], "s": r["s"], "regressors": r["names"], "theta": r["theta"].tolist(),
                     "draws": r["draws"][:200].tolist(),
                     "residuals": r["res_all"][np.linspace(0, len(r["res_all"]) - 1, min(len(r["res_all"]), 400)).astype(int)].tolist(),
                     "history": {"Y": Y[-H:].tolist(), "U": U[-H:].tolist()}})
        if r.get("calib"):
            twin["calibration"] = r["calib"]
            report["interval_calibration"] = {"method": tx("tw.calib_method"), "origins": r["calib"]["origins"],
                                              "horizon": r["calib"]["horizon"]}
        lb = r["lb_tr"]
        report.update({"structure": {"lag_order": r["p"], "input_lag_start": r["s"], "coefficients_per_output": len(r["names"]),
                                     "form": tx("tw.form_arx")},
                       "lag_selection": {"criterion": tx("tw.criterion"),
                                         "candidates": r["lag_candidates"], "chosen": r["p"]},
                       "validation": r["val"], "parameters": r["params"], "gains": r["gains"],
                       "poles": [{"re": float(z.real), "im": float(z.imag), "magnitude": float(abs(z))} for z in r["poles"]],
                       "stable": r["stable"], "bootstrap": {"requested": n_boot, "stable_fits": int(len(r["draws"])), "failed": r["failed"]},
                       "interval_coverage": r["coverage"]})
        resid_ok = True
        chk = {}
        for o in outputs:
            a = r["lb_tr"][o]
            v = r["lb_val"][o]
            ok_tr = a["p_value"] > 0.05
            ok_v = True if v is None else v["p_value"] > 0.05
            chk[o] = {"fit_residuals": a, "validation_residuals": v, "ok": bool(ok_tr and ok_v)}
            resid_ok = resid_ok and chk[o]["ok"]
            if not chk[o]["ok"]:
                warnings.append(tx("tw.warn_resid_arx", out=o,
                                   p=min(a["p_value"], 1 if v is None else v["p_value"])))
        report["residual_check"] = {"ok": resid_ok, "per_output": chk}
        if r["coverage"]:
            for o, c in r["coverage"].items():
                if c < level - 0.15:
                    warnings.append(tx("tw.warn_coverage", level=int(level * 100), out=o, cov=c))
        # validation series for a chart
        ts = thin(list(range(nval)))
        report["validation_series"] = {"time": [float(t_val[i]) for i in ts],
                                       **{o: {"observed": [float(obs_val_series[i, j]) for i in ts],
                                              "simulated": [float(r["sim"][i, j]) for i in ts],
                                              "lo": None if r["sim_lo"] is None else [float(r["sim_lo"][i, j]) for i in ts],
                                              "hi": None if r["sim_hi"] is None else [float(r["sim_hi"][i, j]) for i in ts]}
                                          for j, o in enumerate(outputs)}}
        if "fopdt" in results:
            report["alternative_fopdt"] = {"par": dict(zip(["y0", "gain", "tau", "dead_time"], map(float, results["fopdt"]["par_all"]))),
                                           "validation": results["fopdt"]["val"]}
    else:
        r = results["fopdt"]
        pall, dr = r["par_all"], r["draws"]
        names = ["offset", "gain", "time_constant", "dead_time"]
        lo_q, hi_q = (1 - level) / 2 * 100, (1 + level) / 2 * 100
        twin.update({"fopdt": {"par": [float(v) for v in pall], "u0": float(r["u0_all"]), "draws": dr[:200].tolist(),
                               "residuals": r["res_all"][np.linspace(0, len(r["res_all"]) - 1, min(len(r["res_all"]), 400)).astype(int)].tolist()},
                     "history": {"Y": Y[-4:].tolist(), "U": U[-max(8, int(pall[3] // dt) + 6):].tolist()}})
        if len(dr) < 20:
            warnings.append(tx("tw.warn_boot_conv", n=len(dr)))
        params = [{"output": outputs[0], "term": nm, "estimate": float(pall[i]),
                   "std": float(dr[:, i].std(ddof=1)) if len(dr) > 1 else None,
                   "ci_low": float(np.percentile(dr[:, i], lo_q)) if len(dr) > 1 else None,
                   "ci_high": float(np.percentile(dr[:, i], hi_q)) if len(dr) > 1 else None} for i, nm in enumerate(names)]
        if pall[3] < dt:
            warnings.append(tx("tw.warn_deadtime"))
        span = dt * (n - 1)
        if pall[2] > span / 3:
            warnings.append(tx("tw.warn_tau"))
        report.update({"structure": {"form": tx("tw.form_fopdt")},
                       "validation": {"free_run": {outputs[0]: r["val"]}}, "parameters": params,
                       "gains": [{"output": outputs[0], "input": inputs[0], "gain": float(pall[1]),
                                  "ci_low": params[1]["ci_low"], "ci_high": params[1]["ci_high"]}],
                       "bootstrap": {"requested": min(n_boot, 100), "converged": int(len(dr)), "method": tx("tw.boot_method")}})
        lb = r["lb_tr"]
        ok = lb["p_value"] > 0.05
        report["residual_check"] = {"ok": bool(ok), "per_output": {outputs[0]: {"fit_residuals": lb, "validation_residuals": None, "ok": bool(ok)}},
                                    "note": tx("tw.resid_note")}
        if not ok:
            warnings.append(tx("tw.warn_resid_fopdt", out=outputs[0], p=lb["p_value"]))
        ts = thin(list(range(nval)))
        report["validation_series"] = {"time": [float(t_val[i]) for i in ts],
                                       outputs[0]: {"observed": [float(obs_val_series[i, 0]) for i in ts],
                                                    "simulated": [float(r["sim_val"][i]) for i in ts], "lo": None, "hi": None}}
        if "arx" in results:
            report["alternative_arx"] = {"validation": results["arx"]["val"]["free_run"]}
    # headline numbers + honest warnings on quality
    heads = report["validation"]["free_run"]
    for o, m in heads.items():
        if m["nrmse"] is not None and m["nrmse"] > 0.2:
            warnings.append(tx("tw.warn_nrmse", out=o, v=m["nrmse"]))
        if m["r2"] is not None and m["r2"] < 0:
            warnings.append(tx("tw.warn_r2", out=o, v=m["r2"]))
    twin["validation_headline"] = {o: m["nrmse"] for o, m in heads.items()}
    twin["validation_horizon"] = nval
    report["warnings"] = warnings
    return {"twin": twin, "report": report}


# ---------------------------------------------------------------- what-if simulation
def expand_profile(profile: Optional[dict], inputs: list[str], steps: int, defaults: dict[str, float]) -> np.ndarray:
    """Build the future input matrix. Each input may be a number (held), a list, or
    {"step": {"at": k, "from": a, "to": b}} or {"ramp": {"from": a, "to": b}}. Missing inputs hold their last value."""
    profile = profile or {}
    unknown = [k for k in profile if k not in inputs]
    if unknown:
        raise TwinError(tx("tw.unknown_input", name=unknown[0], inputs=inputs))
    cols = []
    for nm in inputs:
        spec = profile.get(nm, defaults[nm])
        if isinstance(spec, (int, float)) and not isinstance(spec, bool):
            col = np.full(steps, float(spec))
        elif isinstance(spec, list):
            if not spec:
                raise TwinError(tx("tw.profile_empty", name=nm))
            col = np.array([float(v) for v in spec], float)
            if len(col) < steps:
                col = np.concatenate([col, np.full(steps - len(col), col[-1])])
            col = col[:steps]
        elif isinstance(spec, dict) and "step" in spec:
            st = spec["step"]
            at = int(st.get("at", 0))
            col = np.where(np.arange(steps) >= at, float(st["to"]), float(st.get("from", defaults[nm])))
        elif isinstance(spec, dict) and "ramp" in spec:
            rp = spec["ramp"]
            col = np.linspace(float(rp.get("from", defaults[nm])), float(rp["to"]), steps)
        else:
            raise TwinError(tx("tw.profile_form", name=nm))
        cols.append(col)
    out = np.column_stack(cols)
    if not np.isfinite(out).all():
        raise TwinError(tx("tw.profile_finite"))
    return out


def check_extrapolation(twin: dict, Ufut: np.ndarray, max_excess: float, allow: bool) -> dict:
    flagged, worst = [], 0.0
    for j, nm in enumerate(twin["inputs"]):
        lo, hi = twin["training"]["input_range"][nm]
        w = max(hi - lo, 1e-12)
        over = np.maximum(np.maximum(lo - Ufut[:, j], Ufut[:, j] - hi), 0) / w
        if over.max() > 0:
            worst = max(worst, float(over.max()))
            flagged.append({"input": nm, "training_range": [lo, hi], "min_requested": float(Ufut[:, j].min()),
                            "max_requested": float(Ufut[:, j].max()), "max_excess_fraction_of_range": float(over.max()),
                            "steps_outside": [int(i) for i in np.where(over > 0)[0][:50]], "count": int((over > 0).sum())})
    info = {"outside_training_range": bool(flagged), "inputs": flagged, "limit": max_excess, "allowed_by_caller": allow}
    if flagged and worst > max_excess and not allow:
        f = max(flagged, key=lambda x: x["max_excess_fraction_of_range"])
        reached = (f["max_requested"] if f["max_excess_fraction_of_range"] > 0
                   and f["max_requested"] > f["training_range"][1] else f["min_requested"])
        raise TwinError(tx("tw.no_extrapolate", name=f["input"], reached=reached, lo=f["training_range"][0],
                           hi=f["training_range"][1], excess=f["max_excess_fraction_of_range"], limit=max_excess))
    return info


def predict(twin: dict, profile: Optional[dict] = None, steps: Optional[int] = None, initial: Optional[dict] = None,
            level: Optional[float] = None, n_paths: int = 300, seed: int = 0, allow_extrapolation: bool = False,
            max_extrapolation: float = 0.25, baseline: bool = True) -> dict:
    if steps is not None and not 1 <= steps <= 5000:
        raise TwinError(tx("tw.steps_range"))
    inputs, outputs = twin["inputs"], twin["outputs"]
    lens = [len(v) for v in (profile or {}).values() if isinstance(v, list)]
    steps = steps or (max(lens) if lens else 50)
    level = level or twin.get("level", 0.95)
    rng = np.random.default_rng(seed)
    hist = twin["history"]
    histU = np.array(hist["U"], float)
    histY = np.array(hist["Y"], float)
    last_in = {nm: float(histU[-1, j]) for j, nm in enumerate(inputs)}
    if initial:
        # {"inputs": {...}, "outputs": {...}} or flat {"T": 15, "u": 3}; anything else is an error, never silently dropped
        io = dict(initial.get("inputs", {}) or {})
        oo = dict(initial.get("outputs", {}) or {})
        for nm, v in initial.items():
            if nm in ("inputs", "outputs"):
                continue
            if nm in outputs:
                oo[nm] = v
            elif nm in inputs:
                io[nm] = v
            else:
                raise TwinError(tx("tw.initial_unknown", name=nm))
        for nm, v in list(io.items()) + list(oo.items()):
            try:
                float(v)
            except (TypeError, ValueError):
                raise TwinError(tx("tw.initial_unknown", name=f"{nm} = {v!r}"))
        for nm in list(io) + list(oo):
            if nm not in inputs + outputs:
                raise TwinError(tx("tw.initial_unknown", name=nm))
        for j, nm in enumerate(inputs):
            if nm in io:
                histU[:, j] = float(io[nm])
        for j, nm in enumerate(outputs):
            if nm in oo:
                histY[:, j] = float(oo[nm])
        last_in = {nm: float(histU[-1, j]) for j, nm in enumerate(inputs)}
    Uf = expand_profile(profile, inputs, steps, last_in)
    ext = check_extrapolation(twin, Uf, max_extrapolation, allow_extrapolation)
    warnings = []
    if ext["outside_training_range"]:
        warnings.append(tx("tw.warn_extrap"))
    lo_q, hi_q = (1 - level) / 2 * 100, (1 + level) / 2 * 100

    def run(Ufut):
        if twin["model"] == "arx":
            p, s = twin["p"], twin["s"]
            th0 = np.array(twin["theta"], float)
            draws = np.array(twin["draws"], float) if twin.get("draws") else th0[None]
            res = np.array(twin["residuals"], float)
            idx = rng.integers(0, len(draws), size=n_paths)
            D = draws[idx]
            noise = res[rng.integers(0, len(res), size=(n_paths, len(Ufut)))]
            paths = _simulate(D, p, s, histY, histU, Ufut, noise)
            nominal = _simulate(th0, p, s, histY, histU, Ufut)[0]
            bad = ~np.isfinite(paths).all(axis=(1, 2)) | (np.abs(np.nan_to_num(paths, nan=1e12, posinf=1e12, neginf=-1e12)).max(axis=(1, 2)) > 1e9)
            if bad.any() and not bad.all():
                paths = paths[~bad]
            return nominal, paths
        fo = twin["fopdt"]
        par = np.array(fo["par"], float)
        u0 = fo["u0"]
        dr = np.array(fo["draws"], float) if fo["draws"] else par[None]
        res = np.array(fo["residuals"], float)
        dt = twin["dt"]
        hu = histU[:, 0]
        ylast = float(histY[-1, 0])
        nominal = _fopdt_predict(par, hu, ylast, Ufut[:, 0], dt, u0)[:, None]
        paths = np.empty((n_paths, len(Ufut), 1))
        for k in range(n_paths):
            pk = dr[rng.integers(0, len(dr))]
            paths[k, :, 0] = _fopdt_predict(pk, hu, ylast, Ufut[:, 0], dt, u0) + res[rng.integers(0, len(res), len(Ufut))]
        return nominal, paths

    calib = twin.get("calibration") if twin["model"] == "arx" else None

    def band(pths):
        m = pths.mean(0)
        if calib:   # held-out calibrated width per horizon (split-conformal); the 5-95 % band is the reference
            return _apply_calibration(calib, level, m, np.percentile(pths, 5, axis=0), np.percentile(pths, 95, axis=0), level)
        return np.percentile(pths, lo_q, axis=0), np.percentile(pths, hi_q, axis=0)

    nominal, paths = run(Uf)
    mean = paths.mean(0)
    lo, hi = band(paths)
    t_last = twin["training"]["t_last"]
    times = [t_last + twin["dt"] * (k + 1) for k in range(steps)]
    out: dict[str, Any] = {"steps": steps, "dt": twin["dt"], "level": level, "paths": int(len(paths)), "time": times,
                           "inputs": {nm: [float(v) for v in Uf[:, j]] for j, nm in enumerate(inputs)},
                           "outputs": {o: {"nominal": [float(v) for v in nominal[:, j]], "mean": [float(v) for v in mean[:, j]],
                                           "lo": [float(v) for v in lo[:, j]], "hi": [float(v) for v in hi[:, j]]}
                                       for j, o in enumerate(outputs)},
                           "extrapolation": ext}
    if twin.get("time_kind") == "timestamp":
        out["time_iso"] = [_dt.datetime.fromtimestamp(v, _dt.timezone.utc).isoformat() for v in times]
    for j, o in enumerate(outputs):
        lo_o, hi_o = twin["training"]["output_range"][o]
        w = max(hi_o - lo_o, 1e-12)
        if nominal[:, j].max() > hi_o + 0.1 * w or nominal[:, j].min() < lo_o - 0.1 * w:
            warnings.append(tx("tw.warn_leaves", out=o, lo=lo_o, hi=hi_o))
    if steps > twin.get("validation_horizon", steps):
        warnings.append(tx("tw.warn_horizon", steps=steps, val=twin["validation_horizon"]))
    if baseline:
        base_profile = {nm: float(last_in[nm]) for nm in inputs}
        Ub = expand_profile(base_profile, inputs, steps, last_in)
        bn, bp = run(Ub)
        bm = bp.mean(0)
        blo, bhi = band(bp)
        out["baseline"] = {"description": tx("tw.baseline_desc"),
                           "outputs": {o: {"nominal": [float(v) for v in bn[:, j]], "mean": [float(v) for v in bm[:, j]],
                                           "lo": [float(v) for v in blo[:, j]],
                                           "hi": [float(v) for v in bhi[:, j]]}
                                       for j, o in enumerate(outputs)}}
        out["delta_vs_baseline"] = {o: {"final_nominal": float(nominal[-1, j] - bn[-1, j]),
                                        "max_abs_nominal": float(np.max(np.abs(nominal[:, j] - bn[:, j])))}
                                    for j, o in enumerate(outputs)}
    out["interval_note"] = (f"{int(level * 100)}% interval over {len(paths)} simulated paths combining coefficient uncertainty "
                            "(bootstrap) and resampled residuals"
                            + ("; its width per horizon is calibrated on the held-out block so that coverage matches the level for data like it; "
                               if calib else "; ") + "it excludes structural error and changes in the process.")
    out["warnings"] = warnings
    out["caveat"] = tx("tw.caveat")
    return out


# ---------------------------------------------------------------- storage and service wrappers
def _dir(services) -> Path:
    d = Path(services.config.data_dir) / "twins"
    d.mkdir(parents=True, exist_ok=True)
    return d


def _file(services, name: str) -> Path:
    if not isinstance(name, str) or not _NAME.match(name):
        raise TwinError(tx("tw.name"))
    return _dir(services) / (name + ".json")


def list_twins(services) -> dict:
    out = []
    for f in sorted(_dir(services).glob("*.json")):
        try:
            tw = json.loads(f.read_text(encoding="utf-8"))
        except json.JSONDecodeError:
            continue
        out.append({"name": f.stem, "model": tw.get("model"), "outputs": tw.get("outputs"), "inputs": tw.get("inputs"),
                    "dataset": tw.get("dataset"), "validation_nrmse": tw.get("validation_headline"), "updated": f.stat().st_mtime})
    return {"twins": out, "count": len(out)}


def get_twin(services, name: str, full: bool = False) -> dict:
    f = _file(services, name)
    if not f.exists():
        raise LookupError(tx("tw.unknown_saved", name=name, saved=[x["name"] for x in list_twins(services)["twins"]]))
    tw = json.loads(f.read_text(encoding="utf-8"))
    if not full:
        tw = {k: v for k, v in tw.items() if k not in ("draws", "residuals", "history", "theta", "fopdt")} | \
            ({"fopdt": {"par": tw["fopdt"]["par"]}} if "fopdt" in tw else {})
    return {"name": name, "twin": tw}


def delete_twin(services, name: str, source: str = "ui") -> dict:
    def do():
        f = _file(services, name)
        if not f.exists():
            raise LookupError(tx("tw.unknown", name=name))
        f.unlink()
        return {"name": name, "deleted": True, "_log_summary": f"deleted twin {name!r}"}
    return services._log("twin_delete", source, None, {"name": name}, do)


def fit_twin(services, dataset: str, time: Optional[str], outputs: list[str], inputs: list[str], model: str = "arx",
             max_lag: Optional[int] = None, validation_fraction: float = 0.25, input_lag_start: int = 1,
             n_boot: int = 200, seed: int = 0, name: Optional[str] = None, source: str = "ui") -> dict:
    def do():
        spec = {c: "num" for c in outputs + inputs}
        if time:
            spec[time] = "time"
        if len(set(outputs) & set(inputs)):
            raise TwinError(tx("tw.in_and_out"))
        cols, meta = load_frame(services, dataset, spec)
        n = meta["rows"]
        tt = to_float(cols[time]) if time else np.arange(n, dtype=float)
        if not time:
            meta["time_kind"] = None
        Y = np.column_stack([to_float(cols[c]) for c in outputs])
        U = np.column_stack([to_float(cols[c]) for c in inputs])
        res = fit_series(tt, Y, U, outputs, inputs, model, max_lag, input_lag_start, validation_fraction, n_boot, seed,
                         time_kind=meta.get("time_kind"))
        twin, report = res["twin"], res["report"]
        twin.update({"dataset": dataset, "time": time, "created": _time.time()})
        if not time:
            report["warnings"].append(tx("tw.warn_no_time"))
        if name:
            f = _file(services, name)
            report["replaced"] = f.exists()
            f.write_text(json.dumps(twin), encoding="utf-8")
            report["saved_as"] = name
        report["dataset"] = dataset
        hl = twin["validation_headline"]
        report["_log_summary"] = (f"fitted {twin['model']} twin on {dataset}: " + ", ".join(
            f"{o} free-run NRMSE {v:.3f}" if v is not None else f"{o} NRMSE n/a" for o, v in hl.items()))
        return report
    return services._log("twin_fit", source, dataset, {"time": time, "outputs": outputs, "inputs": inputs, "model": model,
                                                         "seed": seed, "name": name}, do)


def simulate_twin(services, name: str, profile: Optional[dict] = None, steps: Optional[int] = None,
                  initial: Optional[dict] = None, level: Optional[float] = None, n_paths: int = 300, seed: int = 0,
                  allow_extrapolation: bool = False, max_extrapolation: float = 0.25, save_as: Optional[str] = None,
                  source: str = "ui") -> dict:
    def do():
        twin = get_twin(services, name, full=True)["twin"]
        res = predict(twin, profile, steps, initial, level, n_paths, seed, allow_extrapolation, max_extrapolation)
        res["twin"] = name
        if save_as:
            import csv
            import io
            buf = io.StringIO()
            w = csv.writer(buf, lineterminator="\n")
            outs = list(res["outputs"])
            w.writerow(["time"] + list(res["inputs"]) + [f"{o}_{k}" for o in outs for k in ("nominal", "lo", "hi")])
            for i in range(res["steps"]):
                w.writerow([res["time"][i]] + [res["inputs"][k][i] for k in res["inputs"]] +
                           [res["outputs"][o][k][i] for o in outs for k in ("nominal", "lo", "hi")])
            saved = services.ingest_text(buf.getvalue(), save_as, "csv", None, source=source)
            res["saved_dataset"] = saved.get("name", save_as)
        res["_log_summary"] = (f"simulated twin {name!r} for {res['steps']} steps"
                               + (" (extrapolating, flagged)" if res["extrapolation"]["outside_training_range"] else ""))
        return res
    return services._log("twin_simulate", source, None, {"name": name, "steps": steps, "profile": profile}, do)

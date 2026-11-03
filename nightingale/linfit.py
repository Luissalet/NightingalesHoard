"""Small least-squares toolkit shared by the twin, experiment and effect modules (numpy and scipy only)."""
from __future__ import annotations

import math
from typing import Any, Optional

import numpy as np
from scipy import stats

CAVEAT_DEFAULT = ""


class FitError(ValueError):
    pass


def q(name: str) -> str:
    return '"' + str(name).replace('"', '""') + '"'


def load_frame(services, dataset: str, spec: dict[str, str]) -> tuple[dict[str, list], dict[str, Any]]:
    """Read columns of a dataset in one query so rows stay aligned.

    ``spec`` maps a column to ``num`` (float or NaN), ``time`` (numeric, or epoch seconds for date/timestamp or
    text that parses as one) or ``raw`` (as stored). Returns the columns and ``{"time_kind": ...}`` details.
    """
    table = services._resolve_table_by_name(dataset)
    with services.engine.lock() as conn:
        desc = {r[0]: str(r[1]).upper() for r in conn.execute(f"DESCRIBE {q(table)}").fetchall()}
        missing = [c for c in spec if c not in desc]
        if missing:
            raise FitError(f"dataset {dataset!r} has no column {missing[0]!r}; columns: {list(desc)}")
        exprs, kinds = [], {}
        for col, kind in spec.items():
            typ = desc[col]
            if kind == "num":
                exprs.append(f"TRY_CAST({q(col)} AS DOUBLE)" if not typ.startswith(("DATE", "TIMESTAMP")) else "NULL")
            elif kind == "time":
                if typ.startswith(("DATE", "TIMESTAMP")):
                    exprs.append(f"epoch(CAST({q(col)} AS TIMESTAMP))")
                    kinds[col] = "timestamp"
                else:
                    numeric = conn.execute(f"SELECT count(TRY_CAST({q(col)} AS DOUBLE)), count({q(col)}) FROM {q(table)}").fetchone()
                    if numeric[1] and numeric[0] >= 0.9 * numeric[1]:
                        exprs.append(f"TRY_CAST({q(col)} AS DOUBLE)")
                        kinds[col] = "numeric"
                    else:
                        exprs.append(f"epoch(TRY_CAST({q(col)} AS TIMESTAMP))")
                        kinds[col] = "timestamp"
            else:
                exprs.append(q(col))
        rows = conn.execute(f"SELECT {', '.join(exprs)} FROM {q(table)}").fetchall()
    out: dict[str, list] = {c: [] for c in spec}
    for r in rows:
        for c, v in zip(spec, r):
            out[c].append(v)
    return out, {"time_kind": next(iter(kinds.values()), None), "rows": len(rows)}


def to_float(values: list) -> np.ndarray:
    return np.array([float(v) if v is not None and not isinstance(v, bool) and _finite(v) else (1.0 if v is True else 0.0 if v is False else np.nan)
                     for v in values], dtype=float)


def _finite(v) -> bool:
    try:
        return math.isfinite(float(v))
    except (TypeError, ValueError):
        return False


def tq(dof: float, level: float = 0.95) -> float:
    if dof is None or dof <= 0:
        return float("nan")
    return float(stats.t.ppf(0.5 + level / 2, dof))


def ols(X: np.ndarray, y: np.ndarray, cov: str = "classical", groups: Optional[np.ndarray] = None,
        hac_lags: Optional[int] = None) -> dict:
    """Ordinary least squares with classical, HC3, Newey-West (HAC) or cluster-robust covariance."""
    X = np.asarray(X, float)
    y = np.asarray(y, float)
    n, p = X.shape
    beta, _, rank, _ = np.linalg.lstsq(X, y, rcond=None)
    rank = int(rank)
    resid = y - X @ beta
    dof = n - rank
    bread = np.linalg.pinv(X.T @ X)
    sigma2 = float(resid @ resid / dof) if dof > 0 else float("nan")
    if cov == "classical":
        V = sigma2 * bread if dof > 0 else np.full((p, p), np.nan)
        vdof = dof
    elif cov == "HC3":
        h = np.einsum("ij,jk,ik->i", X, bread, X)
        w = (resid / np.maximum(1 - h, 1e-8)) ** 2
        V = bread @ (X.T * w) @ X @ bread
        vdof = dof
    elif cov == "HAC":
        L = hac_lags if hac_lags is not None else int(math.floor(4 * (n / 100.0) ** (2 / 9)))
        L = max(0, min(L, n - 2))
        U = X * resid[:, None]
        S = U.T @ U
        for k in range(1, L + 1):
            G = U[k:].T @ U[:-k]
            S += (1 - k / (L + 1)) * (G + G.T)
        V = bread @ S @ bread * (n / dof if dof > 0 else 1.0)
        vdof = dof
    elif cov == "cluster":
        if groups is None:
            raise FitError("cluster covariance needs groups")
        ids = np.unique(groups)
        G = len(ids)
        S = np.zeros((p, p))
        for g in ids:
            m = groups == g
            s = X[m].T @ resid[m]
            S += np.outer(s, s)
        adj = (G / max(G - 1, 1)) * ((n - 1) / max(n - rank, 1))
        V = adj * bread @ S @ bread
        vdof = G - 1
    else:
        raise FitError(f"unknown covariance {cov!r}")
    se = np.sqrt(np.maximum(np.diag(V), 0))
    ybar = y.mean()
    tss = float(((y - ybar) ** 2).sum())
    rss = float(resid @ resid)
    r2 = 1 - rss / tss if tss > 0 else float("nan")
    return {"beta": beta, "se": se, "cov": V, "resid": resid, "dof": dof, "vdof": vdof, "sigma2": sigma2,
            "rank": int(rank), "n": n, "p": p, "r2": r2,
            "adj_r2": 1 - (1 - r2) * (n - 1) / dof if dof > 0 and tss > 0 else float("nan"),
            "rss": rss, "bread": bread}


def coef_table(fit: dict, names: list[str], level: float = 0.95) -> list[dict]:
    t = tq(fit["vdof"], level)
    rows = []
    for i, nm in enumerate(names):
        b, s = float(fit["beta"][i]), float(fit["se"][i])
        tv = b / s if s > 0 and math.isfinite(s) else float("nan")
        pv = float(2 * stats.t.sf(abs(tv), fit["vdof"])) if math.isfinite(tv) and fit["vdof"] > 0 else None
        rows.append({"term": nm, "estimate": b, "se": s if math.isfinite(s) else None,
                     "ci_low": b - t * s if math.isfinite(t) and math.isfinite(s) else None,
                     "ci_high": b + t * s if math.isfinite(t) and math.isfinite(s) else None,
                     "t": tv if math.isfinite(tv) else None, "p_value": pv})
    return rows


def contrast(fit: dict, c: np.ndarray, level: float = 0.95) -> dict:
    c = np.asarray(c, float)
    est = float(c @ fit["beta"])
    se = float(math.sqrt(max(c @ fit["cov"] @ c, 0)))
    t = tq(fit["vdof"], level)
    tv = est / se if se > 0 else float("nan")
    return {"estimate": est, "se": se if math.isfinite(se) else None,
            "ci_low": est - t * se if math.isfinite(t) else None, "ci_high": est + t * se if math.isfinite(t) else None,
            "p_value": float(2 * stats.t.sf(abs(tv), fit["vdof"])) if math.isfinite(tv) and fit["vdof"] > 0 else None}


def acf(x: np.ndarray, nlags: int) -> np.ndarray:
    x = np.asarray(x, float) - np.mean(x)
    d = float(x @ x)
    if d <= 0:
        return np.zeros(nlags)
    return np.array([float(x[k:] @ x[:-k]) / d for k in range(1, nlags + 1)])


def ljung_box(x: np.ndarray, nlags: int, fitted_params: int = 0) -> dict:
    n = len(x)
    nlags = max(1, min(nlags, n // 3))
    r = acf(x, nlags)
    Q = float(n * (n + 2) * np.sum(r ** 2 / (n - np.arange(1, nlags + 1))))
    dof = max(nlags - fitted_params, 1)
    return {"lags": nlags, "statistic": Q, "p_value": float(stats.chi2.sf(Q, dof)),
            "max_abs_acf": float(np.max(np.abs(r))), "acf_bound": float(1.96 / math.sqrt(n)),
            "acf": [float(v) for v in r]}


def durbin_watson(resid: np.ndarray) -> float:
    d = np.diff(resid)
    s = float(resid @ resid)
    return float(d @ d / s) if s > 0 else float("nan")


def thin(a: np.ndarray, n: int = 300) -> list:
    a = list(a)
    if len(a) <= n:
        return a
    idx = np.linspace(0, len(a) - 1, n).round().astype(int)
    return [a[i] for i in idx]


def num(v) -> Optional[float]:
    if v is None:
        return None
    v = float(v)
    return v if math.isfinite(v) else None


def ols_ar1(X: np.ndarray, y: np.ndarray, iters: int = 15) -> dict:
    """Prais-Winsten least squares for AR(1) errors; coefficient covariance from the transformed regression."""
    X = np.asarray(X, float)
    y = np.asarray(y, float)
    n, p = X.shape
    beta = np.linalg.lstsq(X, y, rcond=None)[0]
    rho = 0.0
    for _ in range(iters):
        e = y - X @ beta
        new = float(e[1:] @ e[:-1] / (e[:-1] @ e[:-1])) if (e[:-1] @ e[:-1]) > 0 else 0.0
        new = max(min(new, 0.98), -0.98)
        w = math.sqrt(1 - new ** 2)
        ys = np.concatenate([[w * y[0]], y[1:] - new * y[:-1]])
        Xs = np.vstack([w * X[:1], X[1:] - new * X[:-1]])
        beta = np.linalg.lstsq(Xs, ys, rcond=None)[0]
        if abs(new - rho) < 1e-6:
            rho = new
            break
        rho = new
    es = ys - Xs @ beta
    dof = n - p - 1
    sigma2 = float(es @ es / dof) if dof > 0 else float("nan")
    bread = np.linalg.pinv(Xs.T @ Xs)
    V = sigma2 * bread
    resid = y - X @ beta
    tss = float(((y - y.mean()) ** 2).sum())
    return {"beta": beta, "se": np.sqrt(np.maximum(np.diag(V), 0)), "cov": V, "resid": resid, "dof": dof, "vdof": dof, "sigma2": sigma2,
            "rank": int(np.linalg.matrix_rank(X)), "n": n, "p": p, "rho": rho,
            "r2": 1 - float(resid @ resid) / tss if tss > 0 else float("nan"), "bread": bread}

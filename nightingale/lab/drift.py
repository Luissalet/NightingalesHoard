"""Curve/series comparison and drift: distribution drift between two datasets
(or two slices), and a generic "curve comparison" for grouped series (x, y,
group) — overlay data, noise/gap detection, growth-rate stats, and systematic
bias between groups.
"""

from __future__ import annotations

from typing import Any, Optional

import numpy as np
import pandas as pd
from scipy import stats as _stats

from . import LabError

__all__ = ["compare_distributions", "curve_comparison"]


def _py(v: Any) -> Any:
    if isinstance(v, (np.floating,)):
        return None if np.isnan(v) else round(float(v), 6)
    if isinstance(v, (np.integer,)):
        return int(v)
    return v


def _psi(expected: np.ndarray, actual: np.ndarray, bins: int = 10) -> float:
    """Population Stability Index between two numeric samples, binned on the
    expected (reference) sample's quantiles."""
    quantiles = np.unique(np.quantile(expected, np.linspace(0, 1, bins + 1)))
    if len(quantiles) < 3:
        return 0.0
    quantiles[0], quantiles[-1] = -np.inf, np.inf
    exp_counts, _ = np.histogram(expected, bins=quantiles)
    act_counts, _ = np.histogram(actual, bins=quantiles)
    exp_pct = np.maximum(exp_counts / max(1, len(expected)), 1e-6)
    act_pct = np.maximum(act_counts / max(1, len(actual)), 1e-6)
    return float(np.sum((act_pct - exp_pct) * np.log(act_pct / exp_pct)))


def compare_distributions(df_a: pd.DataFrame, df_b: pd.DataFrame, columns: Optional[list[str]] = None,
                           ks_alpha: float = 0.05, psi_threshold: float = 0.2) -> dict:
    """KS test, PSI, Wasserstein distance and mean/sd shift for every shared
    numeric column between two datasets (or two slices of the same one)."""
    shared = [c for c in (columns or df_a.columns) if c in df_a.columns and c in df_b.columns]
    if not shared:
        raise LabError("no shared column names between the two datasets/slices")
    out = []
    for c in shared:
        if not (pd.api.types.is_numeric_dtype(df_a[c]) and pd.api.types.is_numeric_dtype(df_b[c])):
            continue
        a = df_a[c].dropna().to_numpy(dtype=float)
        b = df_b[c].dropna().to_numpy(dtype=float)
        if len(a) < 2 or len(b) < 2:
            continue
        ks_stat, ks_p = _stats.ks_2samp(a, b)
        psi = _psi(a, b)
        wasserstein = _stats.wasserstein_distance(a, b)
        mean_a, mean_b = float(a.mean()), float(b.mean())
        sd_a, sd_b = float(a.std()), float(b.std())
        flagged = bool(ks_p < ks_alpha or psi > psi_threshold)
        out.append({
            "column": c, "n_a": int(len(a)), "n_b": int(len(b)),
            "ks_statistic": _py(ks_stat), "ks_pvalue": _py(ks_p), "psi": round(psi, 4),
            "wasserstein_distance": _py(wasserstein),
            "mean_a": round(mean_a, 6), "mean_b": round(mean_b, 6), "mean_shift": round(mean_b - mean_a, 6),
            "sd_a": round(sd_a, 6), "sd_b": round(sd_b, 6), "sd_shift": round(sd_b - sd_a, 6),
            "flagged": flagged,
        })
    out.sort(key=lambda r: (-int(r["flagged"]), -(r["psi"] or 0)))
    return {"columns": out, "n_flagged": sum(1 for r in out if r["flagged"]),
            "ks_alpha": ks_alpha, "psi_threshold": psi_threshold}


def _numeric_x(series: pd.Series) -> np.ndarray:
    if pd.api.types.is_numeric_dtype(series):
        return series.to_numpy(dtype=float)
    try:
        return pd.to_datetime(series).astype("int64").to_numpy(dtype=float)
    except (ValueError, TypeError):
        raise LabError("curve comparison's x column must be numeric or date-like")


def _one_series(df: pd.DataFrame, x: str, y: str) -> dict:
    work = df[[x, y]].dropna().sort_values(x)
    if len(work) < 2:
        return {"n": len(work), "note": "not enough points"}
    x_num = _numeric_x(work[x])
    y_num = work[y].to_numpy(dtype=float)

    diffs = np.diff(x_num)
    median_step = float(np.median(diffs)) if len(diffs) else 0.0
    gap_idx = np.where(diffs > 2.5 * median_step)[0] if median_step > 0 else np.array([], dtype=int)
    gaps = [{"after_index": int(i), "x_before": _py(x_num[i]), "x_after": _py(x_num[i + 1]),
             "gap_size": _py(diffs[i])} for i in gap_idx]

    window = max(3, len(y_num) // 20)
    smoothed = pd.Series(y_num).rolling(window, min_periods=1, center=True).mean().to_numpy()
    residual = y_num - smoothed
    noise_ratio = float(np.std(residual) / (np.std(y_num) or 1.0))

    with np.errstate(divide="ignore", invalid="ignore"):
        growth = np.diff(y_num) / np.where(y_num[:-1] == 0, np.nan, y_num[:-1])
    growth = growth[np.isfinite(growth)]
    growth_stats = {"mean": _py(growth.mean()) if len(growth) else None,
                     "median": _py(np.median(growth)) if len(growth) else None,
                     "std": _py(growth.std()) if len(growth) else None}

    return {
        "n": int(len(work)), "overlay": [{"x": _py(xi), "y": _py(yi)} for xi, yi in zip(x_num, y_num)],
        "gaps": gaps, "noise_ratio": round(noise_ratio, 4), "growth_rate": growth_stats,
        "_x": x_num, "_y": y_num,
    }


def curve_comparison(df: pd.DataFrame, x: str, y: str, group: Optional[str] = None) -> dict:
    """Overlay/noise/gap/growth-rate diagnostics for a series, per group when
    `group` is given, plus a systematic-bias readout between exactly two
    groups (their y values interpolated onto a shared x range)."""
    if x not in df.columns or y not in df.columns:
        raise LabError(f"unknown column(s): {[c for c in (x, y) if c not in df.columns]}")
    if group and group not in df.columns:
        raise LabError(f"unknown column: {group}")

    if not group:
        series = _one_series(df, x, y)
        series.pop("_x", None)
        series.pop("_y", None)
        return {"series": series}

    groups = {}
    raw = {}
    for g, sub in df.groupby(group):
        result = _one_series(sub, x, y)
        raw[str(g)] = (result.pop("_x", None), result.pop("_y", None))
        groups[str(g)] = result

    systematic_bias = None
    names = list(raw)
    if len(names) == 2 and all(raw[n][0] is not None and len(raw[n][0]) >= 2 for n in names):
        a, b = names
        xa, ya = raw[a]
        xb, yb = raw[b]
        lo, hi = max(xa.min(), xb.min()), min(xa.max(), xb.max())
        if hi > lo:
            grid = np.linspace(lo, hi, 50)
            ya_i = np.interp(grid, xa, ya)
            yb_i = np.interp(grid, xb, yb)
            diff = yb_i - ya_i
            systematic_bias = {"group_a": a, "group_b": b, "mean_bias": _py(diff.mean()),
                                "std_bias": _py(diff.std()), "max_abs_bias": _py(np.abs(diff).max())}
    return {"groups": groups, "systematic_bias": systematic_bias}

"""Optimization: "which input values maximize/minimize the target?" — Bayesian
optimization over a trained model's inputs with a Gaussian-process surrogate
(or the model's own mean/std when it already is a GP) and an Expected
Improvement / Upper-Confidence-Bound acquisition, implemented with plain
`numpy`/`scipy` (no botorch). Also multi-objective Pareto-front search across
2-3 trained models' predictions, via non-dominated sorting over sampled
candidates.
"""

from __future__ import annotations

from typing import Any, Optional

import numpy as np
import pandas as pd
from scipy.optimize import minimize
from scipy.stats import norm

from . import LabError
from .registry import SavedModel, prep_features

__all__ = ["optimize", "pareto_front"]

_DIRECTIONS = ("maximize", "minimize")


def _py(v: Any) -> Any:
    if isinstance(v, (np.floating,)):
        return None if np.isnan(v) else round(float(v), 6)
    if isinstance(v, (np.integer,)):
        return int(v)
    return v


def _resolve_bounds(df: pd.DataFrame, features: list[str], bounds: Optional[dict], fixed: dict,
                     integer_features: list[str]) -> dict[str, tuple[float, float]]:
    out = {}
    for f in features:
        if f in fixed:
            continue
        if bounds and f in bounds:
            lo, hi = bounds[f]
        else:
            if f not in df.columns or not pd.api.types.is_numeric_dtype(df[f]):
                raise LabError(f"feature {f!r} needs explicit bounds (not numeric, or not in the dataset)")
            lo, hi = float(df[f].min()), float(df[f].max())
        if lo > hi:
            raise LabError(f"bounds for {f!r} are inverted: {lo} > {hi}")
        out[f] = (float(lo), float(hi))
    return out


def _sample_pool(n: int, bounds: dict[str, tuple[float, float]], categorical: dict[str, list],
                  fixed: dict, integer_features: list[str], seed: int) -> pd.DataFrame:
    rng = np.random.RandomState(seed)
    rows = []
    cat_names = list(categorical)
    cat_choices = [categorical[c] for c in cat_names]
    for _ in range(n):
        row: dict[str, Any] = dict(fixed)
        for f, (lo, hi) in bounds.items():
            v = rng.uniform(lo, hi)
            row[f] = int(round(v)) if f in integer_features else float(v)
        for c, choices in zip(cat_names, cat_choices):
            row[c] = choices[rng.randint(0, len(choices))]
        rows.append(row)
    return pd.DataFrame(rows)


def _apply_constraints(pool: pd.DataFrame, constraints: list[dict]) -> pd.DataFrame:
    if not constraints:
        return pool
    mask = np.ones(len(pool), dtype=bool)
    for c in constraints:
        coeffs = c.get("coeffs") or {}
        limit = c.get("le")
        if limit is None:
            raise LabError("each constraint needs 'coeffs' and 'le' (a linear inequality: sum(coeff*col) <= le)")
        total = np.zeros(len(pool))
        for col, coef in coeffs.items():
            if col not in pool.columns:
                raise LabError(f"constraint references unknown column: {col}")
            total = total + pool[col].to_numpy(dtype=float) * float(coef)
        mask &= total <= limit
    return pool[mask]


def _predict(saved: SavedModel, rows: pd.DataFrame) -> np.ndarray:
    X, _ = prep_features(rows, saved.features)
    X = X.reindex(columns=saved.X_columns, fill_value=0)
    preds = saved.model.predict(X)
    return np.asarray(preds, dtype=float)


def _predict_with_std(saved: SavedModel, rows: pd.DataFrame) -> tuple[np.ndarray, np.ndarray]:
    """Mean + std of the model's prediction. When the model is a Gaussian
    Process, its own `return_std` is used directly; otherwise a small GP
    surrogate is fit on the pool's (features -> model prediction) pairs, which
    is what supplies the uncertainty for the acquisition function."""
    X, _ = prep_features(rows, saved.features)
    X = X.reindex(columns=saved.X_columns, fill_value=0)
    if saved.backend == "gaussian_process" and hasattr(saved.model, "predict"):
        try:
            mu, std = saved.model.predict(X, return_std=True)
            return np.asarray(mu, dtype=float), np.asarray(std, dtype=float)
        except TypeError:
            pass
    from sklearn.gaussian_process import GaussianProcessRegressor
    from sklearn.gaussian_process.kernels import RBF, WhiteKernel
    from sklearn.preprocessing import StandardScaler

    y = saved.model.predict(X)
    scaler = StandardScaler().fit(X.values)
    Xs = scaler.transform(X.values)
    surrogate = GaussianProcessRegressor(kernel=RBF() + WhiteKernel(), normalize_y=True, n_restarts_optimizer=1)
    surrogate.fit(Xs, y)
    mu, std = surrogate.predict(Xs, return_std=True)
    return np.asarray(mu, dtype=float), np.asarray(std, dtype=float)


def _acquisition(mu: np.ndarray, std: np.ndarray, best: float, kind: str, kappa: float = 1.96,
                  xi: float = 0.01) -> np.ndarray:
    std = np.maximum(std, 1e-9)
    if kind == "ucb":
        return mu + kappa * std
    z = (mu - best - xi) / std
    return (mu - best - xi) * norm.cdf(z) + std * norm.pdf(z)


def optimize(saved: SavedModel, df: pd.DataFrame, direction: str = "maximize", bounds: Optional[dict] = None,
             fixed: Optional[dict] = None, integer_features: Optional[list[str]] = None,
             categorical_features: Optional[dict[str, list]] = None, constraints: Optional[list[dict]] = None,
             acquisition: str = "ei", n_candidates: int = 3000, batch_size: int = 5, seed: int = 42,
             refine: bool = True) -> dict:
    """Suggest the next `batch_size` input combinations to try, ranked by an
    Expected-Improvement/UCB acquisition over a GP surrogate of the trained
    model's response surface, plus a locally-refined best point."""
    if direction not in _DIRECTIONS:
        raise LabError(f"unknown direction: {direction}; choose from {_DIRECTIONS}")
    fixed = dict(fixed or {})
    integer_features = list(integer_features or [])
    categorical_features = dict(categorical_features or {})
    numeric_features = [f for f in saved.features if f not in fixed and f not in categorical_features]
    unknown_fixed = [f for f in fixed if f not in saved.features]
    if unknown_fixed:
        raise LabError(f"fixed references unknown feature(s): {unknown_fixed}")
    resolved_bounds = _resolve_bounds(df, numeric_features, bounds, fixed, integer_features)

    pool = _sample_pool(n_candidates, resolved_bounds, categorical_features, fixed, integer_features, seed)
    pool = _apply_constraints(pool, constraints or [])
    if pool.empty:
        raise LabError("no candidate points satisfy the given constraints")

    y_pool = _predict(saved, pool)
    mu, std = _predict_with_std(saved, pool)
    sign = 1.0 if direction == "maximize" else -1.0
    best_so_far = float((sign * y_pool).max())
    scores = _acquisition(sign * mu, std, best_so_far, acquisition)

    order = np.argsort(-scores)
    chosen: list[int] = []
    seen_keys: set[tuple] = set()
    for idx in order:
        row = pool.iloc[idx]
        key = tuple(round(float(row[f]), 3) if isinstance(row[f], (int, float, np.floating, np.integer)) else row[f]
                     for f in saved.features)
        if key in seen_keys:
            continue
        seen_keys.add(key)
        chosen.append(int(idx))
        if len(chosen) >= batch_size:
            break

    suggestions = []
    for idx in chosen:
        row = pool.iloc[idx]
        suggestions.append({
            "inputs": {f: _py(row[f]) for f in saved.features},
            "predicted_value": _py(y_pool[idx]),
            "uncertainty": _py(std[idx]),
            "acquisition_score": _py(scores[idx]),
        })

    best_idx = int(np.argmax(sign * y_pool))
    best_point = {"inputs": {f: _py(pool.iloc[best_idx][f]) for f in saved.features},
                  "predicted_value": _py(y_pool[best_idx])}

    refined = None
    if refine and numeric_features:
        start = pool.iloc[best_idx]
        x0 = np.array([start[f] for f in numeric_features], dtype=float)
        lo = np.array([resolved_bounds[f][0] for f in numeric_features])
        hi = np.array([resolved_bounds[f][1] for f in numeric_features])

        def objective(x: np.ndarray) -> float:
            row = dict(fixed)
            for f, v in zip(numeric_features, x):
                row[f] = int(round(v)) if f in integer_features else float(v)
            for c in categorical_features:
                row[c] = start[c]
            pred = _predict(saved, pd.DataFrame([row]))[0]
            return float(-sign * pred)

        result = minimize(objective, x0, method="Nelder-Mead", bounds=list(zip(lo, hi)),
                           options={"xatol": 1e-4, "fatol": 1e-6, "maxiter": 500})
        x_opt = np.clip(result.x, lo, hi)
        refined_row = dict(fixed)
        for f, v in zip(numeric_features, x_opt):
            refined_row[f] = int(round(v)) if f in integer_features else float(v)
        for c in categorical_features:
            refined_row[c] = start[c]
        refined_row_df = _apply_constraints(pd.DataFrame([refined_row]), constraints or [])
        if not refined_row_df.empty:
            refined_pred = _predict(saved, refined_row_df)[0]
            refined = {"inputs": {f: _py(refined_row[f]) for f in saved.features}, "predicted_value": _py(refined_pred)}

    return {
        "direction": direction, "acquisition": acquisition, "n_candidates_evaluated": int(len(pool)),
        "best_observed_in_training_data": _py((sign * df[saved.target]).max() * sign) if saved.target in df.columns else None,
        "best_in_search": best_point, "refined_best": refined, "suggested_points": suggestions,
    }


def _non_dominated_mask(Y: np.ndarray) -> np.ndarray:
    """`Y` is already oriented so that higher is better in every column.
    Returns a boolean mask of the Pareto-efficient rows."""
    n = len(Y)
    dominated = np.zeros(n, dtype=bool)
    for i in range(n):
        if dominated[i]:
            continue
        for j in range(n):
            if i == j or dominated[j]:
                continue
            if np.all(Y[j] >= Y[i]) and np.any(Y[j] > Y[i]):
                dominated[i] = True
                break
    return ~dominated


def pareto_front(saved_models: list[SavedModel], df: pd.DataFrame, directions: list[str],
                  bounds: Optional[dict] = None, fixed: Optional[dict] = None, n_candidates: int = 1000,
                  seed: int = 42) -> dict:
    """Non-dominated front across 2-3 objectives (each a trained model's
    prediction) sampled over the shared input space."""
    if not (2 <= len(saved_models) <= 3):
        raise LabError("pareto_front needs 2 or 3 models (objectives)")
    if len(directions) != len(saved_models):
        raise LabError("directions must have one entry per model")
    bad = [d for d in directions if d not in _DIRECTIONS]
    if bad:
        raise LabError(f"unknown direction(s): {bad}; choose from {_DIRECTIONS}")

    all_features = list(dict.fromkeys(f for m in saved_models for f in m.features))
    fixed = dict(fixed or {})
    numeric_features = [f for f in all_features if f not in fixed]
    resolved_bounds = _resolve_bounds(df, numeric_features, bounds, fixed, [])
    pool = _sample_pool(n_candidates, resolved_bounds, {}, fixed, [], seed)

    Y = np.zeros((len(pool), len(saved_models)))
    for i, (model, direction) in enumerate(zip(saved_models, directions)):
        preds = _predict(model, pool)
        Y[:, i] = preds if direction == "maximize" else -preds

    mask = _non_dominated_mask(Y)
    front = pool[mask].copy()
    raw_Y = np.zeros((len(front), len(saved_models)))
    for i, (model, direction) in enumerate(zip(saved_models, directions)):
        raw_Y[:, i] = _predict(model, front)
    points = []
    for row_i in range(len(front)):
        points.append({
            "inputs": {f: _py(front.iloc[row_i][f]) for f in all_features},
            "objectives": [_py(v) for v in raw_Y[row_i]],
        })
    points.sort(key=lambda p: p["objectives"][0])
    return {
        "objectives": [{"target": m.target, "direction": d} for m, d in zip(saved_models, directions)],
        "n_candidates_evaluated": int(len(pool)), "n_front": len(points), "front": points,
    }

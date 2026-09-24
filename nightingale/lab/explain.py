"""Explanations: SHAP when installed (TreeExplainer for tree ensembles,
KernelExplainer on a background sample otherwise), falling back to
permutation importance + partial dependence when it isn't. Both paths return
the same shape: global importance plus a per-row explanation.
"""

from __future__ import annotations

from typing import Any, Optional

import numpy as np
import pandas as pd
from sklearn.inspection import partial_dependence, permutation_importance

from . import LabError
from .registry import SavedModel, prep_features

try:
    import shap as _shap

    HAVE_SHAP = True
except Exception:  # noqa: BLE001
    HAVE_SHAP = False

__all__ = ["HAVE_SHAP", "explain_model"]

_TREE_MODELS = ("RandomForest", "ExtraTrees", "GradientBoosting", "HistGradientBoosting",
                "XGB", "LGBM", "DecisionTree")


def _py(v: Any) -> Any:
    if isinstance(v, (np.floating,)):
        return None if np.isnan(v) else round(float(v), 6)
    if isinstance(v, (np.integer,)):
        return int(v)
    return v


def _is_tree_model(model) -> bool:
    return any(name in type(model).__name__ for name in _TREE_MODELS)


def _shap_explain(saved: SavedModel, X: pd.DataFrame, sample_size: int, row_index: Optional[int], seed: int) -> dict:
    background = X.sample(n=min(sample_size, len(X)), random_state=seed) if len(X) > sample_size else X
    try:
        if _is_tree_model(saved.model):
            explainer = _shap.TreeExplainer(saved.model)
        else:
            predict_fn = saved.model.predict_proba if hasattr(saved.model, "predict_proba") else saved.model.predict
            kernel_background = background.sample(n=min(50, len(background)), random_state=seed)
            explainer = _shap.KernelExplainer(predict_fn, kernel_background)
        shap_values = explainer.shap_values(background)
    except Exception as exc:  # noqa: BLE001 - degrade to permutation importance rather than fail the call
        return _permutation_explain(saved, X, sample_size, row_index, seed,
                                     note=f"shap failed ({exc}); used permutation importance instead")
    values = shap_values[1] if isinstance(shap_values, list) and len(shap_values) == 2 else shap_values
    values = np.asarray(values)
    if values.ndim == 3:  # multiclass: (n_classes, n_rows, n_features) or (n_rows, n_features, n_classes)
        values = values.mean(axis=0) if values.shape[0] < values.shape[-1] else values.mean(axis=-1)
    global_importance = np.abs(values).mean(axis=0)
    ranking = sorted(({"feature": c, "importance": _py(v)} for c, v in zip(background.columns, global_importance)),
                      key=lambda r: -(r["importance"] or 0))
    out = {"method": "shap", "global_importance": ranking}
    if row_index is not None:
        if row_index < 0 or row_index >= len(X):
            raise LabError(f"row_index out of range: {row_index} (0..{len(X) - 1})")
        row = X.iloc[[row_index]]
        row_values = explainer.shap_values(row)
        row_values = row_values[1] if isinstance(row_values, list) and len(row_values) == 2 else row_values
        row_values = np.asarray(row_values).reshape(-1)
        out["row_explanation"] = {
            "row_index": row_index,
            "base_value": _py(np.ravel(explainer.expected_value)[0]) if hasattr(explainer, "expected_value") else None,
            "contributions": sorted(
                ({"feature": c, "value": _py(row[c].iloc[0]), "contribution": _py(v)}
                 for c, v in zip(X.columns, row_values)),
                key=lambda r: -abs(r["contribution"] or 0)),
        }
    return out


def _permutation_explain(saved: SavedModel, X: pd.DataFrame, sample_size: int, row_index: Optional[int], seed: int,
                          note: Optional[str] = None) -> dict:
    sample = X.sample(n=min(sample_size, len(X)), random_state=seed) if len(X) > sample_size else X
    y_for_scoring = saved.model.predict(sample)  # self-scoring: importance is about sensitivity, not accuracy
    try:
        perm = permutation_importance(saved.model, sample, y_for_scoring, n_repeats=5, random_state=seed)
        importance = dict(zip(sample.columns, (float(v) for v in perm.importances_mean)))
    except Exception:  # noqa: BLE001
        importance = {}
    ranking = sorted(({"feature": k, "importance": round(v, 6)} for k, v in importance.items()),
                      key=lambda r: -r["importance"])
    out: dict[str, Any] = {"method": "permutation_importance", "global_importance": ranking}
    if note:
        out["note"] = note

    top_features = [r["feature"] for r in ranking[:3]]
    pdp = []
    for feat in top_features:
        try:
            result = partial_dependence(saved.model, sample, [feat], kind="average")
            pdp.append({"feature": feat, "grid": [_py(v) for v in result["grid_values"][0]],
                        "average": [_py(v) for v in result["average"][0]]})
        except Exception:  # noqa: BLE001
            continue
    out["partial_dependence"] = pdp

    if row_index is not None:
        if row_index < 0 or row_index >= len(X):
            raise LabError(f"row_index out of range: {row_index} (0..{len(X) - 1})")
        row = X.iloc[[row_index]]
        baseline = X.mean(numeric_only=True)
        pred_row = float(np.ravel(saved.model.predict(row))[0]) if not hasattr(saved.model, "predict_proba") \
            else float(saved.model.predict_proba(row)[0].max())
        contributions = []
        for feat in X.columns:
            if feat not in baseline.index:
                continue
            probe = row.copy()
            probe[feat] = baseline[feat]
            pred_probe = float(np.ravel(saved.model.predict(probe))[0]) if not hasattr(saved.model, "predict_proba") \
                else float(saved.model.predict_proba(probe)[0].max())
            contributions.append({"feature": feat, "value": _py(row[feat].iloc[0]),
                                    "contribution": round(pred_row - pred_probe, 6)})
        contributions.sort(key=lambda r: -abs(r["contribution"]))
        out["row_explanation"] = {"row_index": row_index, "prediction": round(pred_row, 6),
                                    "contributions": contributions}
    return out


def explain_model(saved: SavedModel, df: pd.DataFrame, sample_size: int = 200, row_index: Optional[int] = None,
                   seed: int = 42) -> dict:
    """Global feature importance + (optionally) a single row's explanation."""
    missing_features = [c for c in saved.features if c not in df.columns]
    if missing_features:
        raise LabError(f"dataset is missing feature column(s) the model was trained on: {missing_features}")
    work = df[saved.features].copy()
    X, _ = prep_features(work, saved.features)
    X = X.reindex(columns=saved.X_columns, fill_value=0)
    if HAVE_SHAP:
        return _shap_explain(saved, X, sample_size, row_index, seed)
    return _permutation_explain(saved, X, sample_size, row_index, seed)

"""Model diagnostics ("model testing"): metrics, residuals, predicted-vs-actual,
error by group, bias over time, over/under-fitting diagnosis, and calibration
for classifiers — everything returned as JSON the UI can chart directly.
"""

from __future__ import annotations

from typing import Any, Optional

import numpy as np
import pandas as pd
from scipy import stats as _stats
from sklearn.calibration import calibration_curve
from sklearn.metrics import (
    accuracy_score,
    confusion_matrix,
    f1_score,
    mean_absolute_error,
    mean_absolute_percentage_error,
    precision_score,
    r2_score,
    recall_score,
    roc_auc_score,
)
from sklearn.model_selection import learning_curve

from . import LabError
from .registry import SavedModel, _ALGOS, prep_features

__all__ = ["evaluate_model"]

_MAX_POINTS = 500  # cap payload size for predicted-vs-actual / residual point clouds


def _py(v: Any) -> Any:
    if isinstance(v, (np.floating,)):
        return None if np.isnan(v) else round(float(v), 6)
    if isinstance(v, (np.integer,)):
        return int(v)
    return v


def _prepare_xy(saved: SavedModel, df: pd.DataFrame) -> tuple[pd.DataFrame, np.ndarray, np.ndarray]:
    if saved.target not in df.columns:
        raise LabError(f"evaluation data has no target column {saved.target!r}")
    missing_features = [c for c in saved.features if c not in df.columns]
    if missing_features:
        raise LabError(f"evaluation data is missing feature column(s) the model was trained on: {missing_features}")
    work = df.dropna(subset=[saved.target])
    if work.empty:
        raise LabError("evaluation data has no non-null target rows")
    X, _ = prep_features(work, saved.features)
    X = X.reindex(columns=saved.X_columns, fill_value=0)
    y_raw = work[saved.target]
    if saved.label_encoder is not None:
        # rows whose label wasn't seen at training time can't be scored
        known = y_raw.astype(str).isin(saved.label_encoder.classes_)
        X, y_raw, work = X[known], y_raw[known], work[known]
        if work.empty:
            raise LabError("none of the evaluation rows' target labels were seen during training")
        y = saved.label_encoder.transform(y_raw.astype(str))
    else:
        y = y_raw.to_numpy()
    return X, y, work


def _regression_diagnostics(saved: SavedModel, X: pd.DataFrame, y: np.ndarray, work: pd.DataFrame,
                             date_col: Optional[str], group_col: Optional[str]) -> dict:
    y_pred = saved.model.predict(X)
    y = np.asarray(y, dtype=float)
    y_pred = np.asarray(y_pred, dtype=float)
    residuals = y - y_pred

    metrics = {
        "rmse": _py(np.sqrt(np.mean(residuals ** 2))),
        "mae": _py(mean_absolute_error(y, y_pred)),
        "r2": _py(r2_score(y, y_pred)) if len(y) > 1 else None,
        "mape": _py(mean_absolute_percentage_error(y[y != 0], y_pred[y != 0])) if (y != 0).any() else None,
    }

    idx = np.linspace(0, len(y) - 1, num=min(_MAX_POINTS, len(y)), dtype=int) if len(y) > _MAX_POINTS else np.arange(len(y))
    predicted_vs_actual = [{"actual": _py(y[i]), "predicted": _py(y_pred[i])} for i in idx]
    hist_counts, hist_edges = np.histogram(residuals, bins=min(30, max(5, len(residuals) // 5 or 5)))
    qq_theoretical, qq_sample = _stats.probplot(residuals, dist="norm", fit=False) if len(residuals) >= 3 else ([], [])

    out: dict[str, Any] = {
        "metrics": metrics,
        "residuals": {
            "vs_predicted": [{"predicted": _py(y_pred[i]), "residual": _py(residuals[i])} for i in idx],
            "histogram": {"counts": [int(c) for c in hist_counts], "edges": [_py(e) for e in hist_edges]},
            "qq": {"theoretical": [_py(v) for v in qq_theoretical], "sample": [_py(v) for v in qq_sample]},
        },
        "predicted_vs_actual": predicted_vs_actual,
    }
    if group_col:
        if group_col not in work.columns:
            raise LabError(f"unknown group_col: {group_col}")
        groups = work[group_col].astype(str).to_numpy()
        rows = []
        for g in pd.unique(groups):
            mask = groups == g
            rows.append({"group": g, "n": int(mask.sum()), "mean_residual": _py(residuals[mask].mean()),
                          "mae": _py(np.mean(np.abs(residuals[mask])))})
        out["error_by_group"] = sorted(rows, key=lambda r: -abs(r["mean_residual"] or 0))
    if date_col:
        if date_col not in work.columns:
            raise LabError(f"unknown date_col: {date_col}")
        dates = pd.to_datetime(work[date_col])
        order = np.argsort(dates.to_numpy())
        window = max(3, len(order) // 20)
        rolling = pd.Series(residuals[order]).rolling(window, min_periods=1).mean()
        out["bias_over_time"] = [{"date": pd.Timestamp(dates.to_numpy()[order][i]).date().isoformat(),
                                    "rolling_mean_residual": _py(rolling.iloc[i])}
                                   for i in range(0, len(order), max(1, len(order) // _MAX_POINTS))]
    return out


def _classification_diagnostics(saved: SavedModel, X: pd.DataFrame, y: np.ndarray, work: pd.DataFrame,
                                  date_col: Optional[str], group_col: Optional[str]) -> dict:
    y_pred = saved.model.predict(X)
    # Use the union of true and predicted labels, not just `y`'s: an evaluation
    # slice (e.g. a holdout with a single true class) can still see the model
    # predict a class that never appears in `y`, and confusion_matrix(labels=...)
    # silently drops any row/column not in `labels` — leaving `matrix` smaller
    # than `class_names` and misreporting accuracy instead of raising.
    labels = sorted(set(np.unique(y)) | set(np.unique(y_pred)))
    n_classes = len(labels)
    average = "binary" if n_classes == 2 else "macro"
    class_names = ([saved.label_encoder.classes_[int(l)] for l in labels] if saved.label_encoder is not None
                    else [str(l) for l in labels])

    metrics: dict[str, Any] = {
        "accuracy": _py(accuracy_score(y, y_pred)),
        "f1": _py(f1_score(y, y_pred, average=average, zero_division=0)),
        "precision": _py(precision_score(y, y_pred, average=average, zero_division=0)),
        "recall": _py(recall_score(y, y_pred, average=average, zero_division=0)),
        "confusion_matrix": {"labels": class_names, "matrix": confusion_matrix(y, y_pred, labels=labels).tolist()},
    }
    proba = None
    if hasattr(saved.model, "predict_proba"):
        try:
            proba = saved.model.predict_proba(X)
        except Exception:  # noqa: BLE001
            proba = None
    if proba is not None:
        try:
            # Branch on how many classes the model was actually trained on
            # (proba's column count), not on how many appear in this
            # particular evaluation slice: a holdout with only one true (or
            # predicted) class must still use the binary form, and
            # roc_auc_score itself raises (caught below) when `y` ends up
            # with a single class -- exactly the "one class in holdout" case.
            if proba.shape[1] == 2:
                metrics["roc_auc"] = _py(roc_auc_score(y, proba[:, 1]))
            else:
                metrics["roc_auc"] = _py(roc_auc_score(y, proba, multi_class="ovr", average="macro"))
        except Exception:  # noqa: BLE001
            pass

    out: dict[str, Any] = {"metrics": metrics}
    if group_col:
        if group_col not in work.columns:
            raise LabError(f"unknown group_col: {group_col}")
        groups = work[group_col].astype(str).to_numpy()
        rows = []
        for g in pd.unique(groups):
            mask = groups == g
            rows.append({"group": g, "n": int(mask.sum()), "accuracy": _py(accuracy_score(y[mask], y_pred[mask]))})
        out["error_by_group"] = sorted(rows, key=lambda r: r["accuracy"] or 0)
    if date_col:
        if date_col not in work.columns:
            raise LabError(f"unknown date_col: {date_col}")
        dates = pd.to_datetime(work[date_col])
        order = np.argsort(dates.to_numpy())
        correct = (y_pred[order] == y[order]).astype(float)
        window = max(3, len(order) // 20)
        rolling = pd.Series(1 - correct).rolling(window, min_periods=1).mean()
        out["bias_over_time"] = [{"date": pd.Timestamp(dates.to_numpy()[order][i]).date().isoformat(),
                                    "rolling_error_rate": _py(rolling.iloc[i])}
                                   for i in range(0, len(order), max(1, len(order) // _MAX_POINTS))]
    if proba is not None and n_classes == 2:
        try:
            frac_pos, mean_pred = calibration_curve(y, proba[:, 1], n_bins=min(10, max(2, len(y) // 20)))
            out["calibration"] = {"mean_predicted": [_py(v) for v in mean_pred], "fraction_positive": [_py(v) for v in frac_pos]}
        except Exception:  # noqa: BLE001
            pass
    return out


def _fit_gap_diagnosis(saved: SavedModel, df_train: Optional[pd.DataFrame], eval_score: Optional[float],
                        scoring: str, seed: int) -> dict:
    """Train vs CV vs holdout gap, plus learning-curve points. Needs the
    original training dataframe to refit fresh copies of the estimator at
    increasing sample sizes; skipped (with a note) when it isn't supplied."""
    if df_train is None or saved.target not in df_train.columns:
        return {"note": "no training dataset supplied; over/under-fitting diagnosis skipped"}
    work = df_train.dropna(subset=[saved.target])
    X, _ = prep_features(work, saved.features)
    X = X.reindex(columns=saved.X_columns, fill_value=0)
    y_raw = work[saved.target]
    if saved.label_encoder is not None:
        known = y_raw.astype(str).isin(saved.label_encoder.classes_)
        X, y_raw = X[known], y_raw[known]
        y = saved.label_encoder.transform(y_raw.astype(str))
    else:
        y = y_raw.to_numpy()
    if len(X) < 20:
        return {"note": "training dataset too small for a learning curve (need >= 20 rows)"}
    algos = _ALGOS.get(saved.task, {})
    factory = algos.get(saved.backend)
    if factory is None:
        return {"note": f"backend {saved.backend!r} unavailable for a fresh learning-curve fit"}
    train_pred = saved.model.predict(X)
    if saved.task == "regression":
        train_score = float(r2_score(y, train_pred))
    else:
        train_score = float(accuracy_score(y, train_pred))
    try:
        sizes, train_scores, test_scores = learning_curve(
            factory(seed), X, y, cv=min(5, max(2, len(X) // 10)), train_sizes=np.linspace(0.2, 1.0, 5),
            scoring=scoring, random_state=seed,
        )
        curve = [{"train_size": int(n), "train_score": _py(tr.mean()), "cv_score": _py(te.mean())}
                  for n, tr, te in zip(sizes, train_scores, test_scores)]
        cv_score = curve[-1]["cv_score"] if curve else None
    except Exception:  # noqa: BLE001
        curve, cv_score = [], None
    gap = None
    diagnosis = "unknown"
    if eval_score is not None:
        gap = round(train_score - eval_score, 4)
        if gap > 0.15 and train_score > 0.7:
            diagnosis = "likely overfitting (train much better than holdout)"
        elif train_score < 0.5 and (eval_score or 0) < 0.5:
            diagnosis = "likely underfitting (both train and holdout are weak)"
        else:
            diagnosis = "no strong over/under-fitting signal"
    return {"train_score": _py(train_score), "cv_score": cv_score, "holdout_score": _py(eval_score),
            "train_holdout_gap": gap, "diagnosis": diagnosis, "learning_curve": curve}


def evaluate_model(saved: SavedModel, df_eval: pd.DataFrame, df_train: Optional[pd.DataFrame] = None,
                    seed: int = 42, date_col: Optional[str] = None, group_col: Optional[str] = None) -> dict:
    """Full diagnostics for a saved model against an evaluation dataset (which
    may be a genuine holdout or the training dataset re-scored). `df_train`,
    when supplied, additionally enables the over/under-fitting diagnosis."""
    X, y, work = _prepare_xy(saved, df_eval)
    if saved.task == "regression":
        result = _regression_diagnostics(saved, X, y, work, date_col, group_col)
        eval_score = result["metrics"]["r2"]
        scoring = "r2"
    else:
        result = _classification_diagnostics(saved, X, y, work, date_col, group_col)
        eval_score = result["metrics"]["accuracy"]
        scoring = "accuracy"
    result["task"] = saved.task
    result["backend"] = saved.backend
    result["n_rows"] = int(len(work))
    result["fit_diagnosis"] = _fit_gap_diagnosis(saved, df_train, eval_score, scoring, seed)
    return result

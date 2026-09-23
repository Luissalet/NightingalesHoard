"""Models: supervised learning, clustering, PCA, anomaly detection, forecasting.

Pure functions over a `pandas.DataFrame` — no FastAPI, no DuckDB connection —
so `services.py` can run them in a thread pool, off the event loop, and tests
can call them directly on small in-memory frames. `statsmodels` is optional:
forecasting degrades to a documented seasonal-naive fallback when it is not
installed (see `HAVE_STATSMODELS`).
"""

from __future__ import annotations

import time
from dataclasses import dataclass
from typing import Any, Optional

import numpy as np
import pandas as pd
from sklearn.cluster import KMeans
from sklearn.decomposition import PCA
from sklearn.ensemble import (
    GradientBoostingClassifier,
    GradientBoostingRegressor,
    IsolationForest,
    RandomForestClassifier,
    RandomForestRegressor,
)
from sklearn.inspection import permutation_importance
from sklearn.linear_model import LinearRegression, LogisticRegression
from sklearn.metrics import (
    accuracy_score,
    confusion_matrix,
    f1_score,
    mean_absolute_error,
    r2_score,
    silhouette_score,
)
from sklearn.model_selection import cross_val_score, train_test_split
from sklearn.preprocessing import LabelEncoder, StandardScaler

try:
    from statsmodels.tsa.holtwinters import ExponentialSmoothing

    HAVE_STATSMODELS = True
except Exception:  # noqa: BLE001
    HAVE_STATSMODELS = False

__all__ = [
    "ModelError", "detect_task", "train_supervised", "run_kmeans", "run_pca",
    "run_anomaly", "run_forecast", "HAVE_STATSMODELS", "MAX_TRAIN_ROWS",
]

MAX_TRAIN_ROWS = 200_000  # rows above this are sampled before training (limit from the brief)


class ModelError(ValueError):
    pass


def _sample(df: pd.DataFrame, seed: int) -> tuple[pd.DataFrame, bool]:
    if len(df) > MAX_TRAIN_ROWS:
        return df.sample(n=MAX_TRAIN_ROWS, random_state=seed), True
    return df, False


def detect_task(series: pd.Series) -> str:
    """Regression vs. classification, from the target column alone."""
    non_null = series.dropna()
    if non_null.empty:
        raise ModelError("target column has no non-null values")
    if non_null.dtype.name in ("category", "bool") or not pd.api.types.is_numeric_dtype(non_null):
        return "classification"
    nunique = non_null.nunique()
    if nunique <= 20 and (non_null == non_null.round()).all():
        return "classification"
    return "regression"


_SUPERVISED_ALGOS = {
    "regression": {
        "linear": lambda seed: LinearRegression(),
        "random_forest": lambda seed: RandomForestRegressor(n_estimators=200, random_state=seed, n_jobs=-1),
        "gradient_boosting": lambda seed: GradientBoostingRegressor(random_state=seed),
    },
    "classification": {
        "logistic": lambda seed: LogisticRegression(max_iter=1000),
        "random_forest": lambda seed: RandomForestClassifier(n_estimators=200, random_state=seed, n_jobs=-1),
        "gradient_boosting": lambda seed: GradientBoostingClassifier(random_state=seed),
    },
}
_DEFAULT_ALGO = {"regression": "linear", "classification": "logistic"}


def _prep_features(df: pd.DataFrame, features: list[str]) -> tuple[pd.DataFrame, dict]:
    """One-hot encode text columns, impute numeric NaNs with the column mean."""
    X = df[features].copy()
    encoders: dict[str, list] = {}
    for col in X.columns:
        if X[col].dtype.name == "category" or not pd.api.types.is_numeric_dtype(X[col]):
            dummies = pd.get_dummies(X[col].astype(str), prefix=col)
            encoders[col] = list(dummies.columns)
            X = X.drop(columns=[col]).join(dummies)
        else:
            X[col] = X[col].fillna(X[col].mean())
    return X, encoders


def train_supervised(df: pd.DataFrame, target: str, features: Optional[list[str]] = None, task: Optional[str] = None,
                      algorithm: Optional[str] = None, test_size: float = 0.2, seed: int = 42, cv: int = 5) -> dict:
    if target not in df.columns:
        raise ModelError(f"unknown target column: {target}")
    features = features or [c for c in df.columns if c != target]
    missing = [c for c in features if c not in df.columns]
    if missing:
        raise ModelError(f"unknown feature column(s): {missing}")
    work = df[[target, *features]].dropna(subset=[target])
    if len(work) < 10:
        raise ModelError("need at least 10 rows with a non-null target to train a model")
    work, sampled = _sample(work, seed)
    task = task or detect_task(work[target])
    algos = _SUPERVISED_ALGOS.get(task)
    if not algos:
        raise ModelError(f"unknown task: {task}")
    algorithm = algorithm or _DEFAULT_ALGO[task]
    if algorithm not in algos:
        raise ModelError(f"unknown algorithm {algorithm!r} for {task}; choose from {sorted(algos)}")

    y_raw = work[target]
    label_encoder = None
    if task == "classification" and not pd.api.types.is_numeric_dtype(y_raw):
        label_encoder = LabelEncoder()
        y = label_encoder.fit_transform(y_raw.astype(str))
    else:
        y = y_raw.values

    X, encoders = _prep_features(work, features)
    n_classes = len(np.unique(y)) if task == "classification" else None
    stratify = y if (task == "classification" and n_classes and n_classes > 1 and min(np.bincount(y.astype(int))) >= 2) else None
    X_train, X_test, y_train, y_test = train_test_split(X, y, test_size=test_size, random_state=seed, stratify=stratify)

    model = algos[algorithm](seed)
    start = time.monotonic()
    model.fit(X_train, y_train)
    fit_seconds = time.monotonic() - start
    y_pred = model.predict(X_test)

    metrics: dict[str, Any] = {}
    if task == "regression":
        metrics["r2"] = round(float(r2_score(y_test, y_pred)), 4)
        metrics["mae"] = round(float(mean_absolute_error(y_test, y_pred)), 4)
        metrics["rmse"] = round(float(np.sqrt(np.mean((np.asarray(y_test) - np.asarray(y_pred)) ** 2))), 4)
        scoring = "r2"
    else:
        metrics["accuracy"] = round(float(accuracy_score(y_test, y_pred)), 4)
        average = "binary" if n_classes == 2 else "macro"
        metrics["f1"] = round(float(f1_score(y_test, y_pred, average=average, zero_division=0)), 4)
        labels = sorted(np.unique(y))
        cm = confusion_matrix(y_test, y_pred, labels=labels).tolist()
        class_names = list(label_encoder.classes_) if label_encoder is not None else [str(l) for l in labels]
        metrics["confusion_matrix"] = {"labels": class_names, "matrix": cm}
        scoring = "accuracy"

    try:
        cv_n = min(cv, max(2, len(X_train) // 5)) if len(X_train) >= 10 else 0
        if cv_n >= 2:
            scores = cross_val_score(algos[algorithm](seed), X, y, cv=cv_n, scoring=scoring)
            metrics["cross_val"] = {"folds": cv_n, "scores": [round(float(s), 4) for s in scores],
                                     "mean": round(float(scores.mean()), 4)}
    except Exception:  # noqa: BLE001 - CV is a bonus, never fatal
        pass

    if hasattr(model, "feature_importances_"):
        importance = dict(zip(X.columns, (float(v) for v in model.feature_importances_)))
    else:
        try:
            perm = permutation_importance(model, X_test, y_test, n_repeats=5, random_state=seed)
            importance = dict(zip(X.columns, (float(v) for v in perm.importances_mean)))
        except Exception:  # noqa: BLE001
            importance = {}
    feature_importance = sorted(({"feature": k, "importance": round(v, 4)} for k, v in importance.items()),
                                 key=lambda r: -r["importance"])[:30]

    X_all, _ = _prep_features(df.assign(**{c: df[c] for c in features}), features)
    X_all = X_all.reindex(columns=X.columns, fill_value=0)
    predictions = model.predict(X_all)
    if label_encoder is not None:
        predictions = label_encoder.inverse_transform(predictions.astype(int))

    return {
        "task": task, "algorithm": algorithm, "target": target, "features": features,
        "metrics": metrics, "feature_importance": feature_importance,
        "predictions": [_py(v) for v in predictions],
        "train_rows": int(len(X_train)), "test_rows": int(len(X_test)), "sampled": sampled,
        "fit_seconds": round(fit_seconds, 3),
    }


def _py(v: Any) -> Any:
    if isinstance(v, (np.floating,)):
        return float(v)
    if isinstance(v, (np.integer,)):
        return int(v)
    return v


def run_kmeans(df: pd.DataFrame, features: list[str], k: Optional[int] = None, k_min: int = 2, k_max: int = 8,
               seed: int = 42) -> dict:
    missing = [c for c in features if c not in df.columns]
    if missing:
        raise ModelError(f"unknown column(s): {missing}")
    work = df[features].dropna()
    if len(work) < k_max:
        k_max = max(k_min, len(work) // 2)
    work, sampled = _sample(work, seed)
    X = StandardScaler().fit_transform(work.values)

    elbow = []
    silhouettes = []
    for candidate in range(k_min, k_max + 1):
        if candidate >= len(X):
            break
        km = KMeans(n_clusters=candidate, random_state=seed, n_init=10).fit(X)
        elbow.append({"k": candidate, "inertia": round(float(km.inertia_), 3)})
        sil = silhouette_score(X, km.labels_) if candidate > 1 and candidate < len(X) else None
        silhouettes.append({"k": candidate, "silhouette": round(float(sil), 4) if sil is not None else None})

    chosen_k = k or max((s["k"] for s in silhouettes if s["silhouette"] is not None),
                         key=lambda kk: next(s["silhouette"] for s in silhouettes if s["k"] == kk), default=k_min)
    final = KMeans(n_clusters=chosen_k, random_state=seed, n_init=10).fit(X)
    labels_full = pd.Series(index=df.index, dtype="float64")
    labels_full.loc[work.index] = final.labels_
    return {"k": chosen_k, "elbow": elbow, "silhouette": silhouettes, "features": features,
            "labels": [None if pd.isna(v) else int(v) for v in labels_full], "sampled": sampled}


def run_pca(df: pd.DataFrame, features: list[str], n_components: int = 2, seed: int = 42) -> dict:
    missing = [c for c in features if c not in df.columns]
    if missing:
        raise ModelError(f"unknown column(s): {missing}")
    work = df[features].dropna()
    if len(work) < 3:
        raise ModelError("need at least 3 complete rows for PCA")
    work, sampled = _sample(work, seed)
    n_components = min(n_components, len(features), len(work))
    X = StandardScaler().fit_transform(work.values)
    pca = PCA(n_components=n_components, random_state=seed).fit(X)
    projected = pca.transform(X)
    proj_full = pd.DataFrame(index=df.index, columns=[f"pc{i+1}" for i in range(n_components)], dtype="float64")
    proj_full.loc[work.index, :] = projected
    return {
        "n_components": n_components, "features": features,
        "explained_variance_ratio": [round(float(v), 4) for v in pca.explained_variance_ratio_],
        "components": pca.components_.tolist(),
        "projection": [[None if pd.isna(v) else round(float(v), 4) for v in row] for row in proj_full.values],
        "sampled": sampled,
    }


def run_anomaly(df: pd.DataFrame, features: list[str], contamination: float = 0.05, seed: int = 42) -> dict:
    missing = [c for c in features if c not in df.columns]
    if missing:
        raise ModelError(f"unknown column(s): {missing}")
    work = df[features].dropna()
    if len(work) < 10:
        raise ModelError("need at least 10 complete rows for anomaly detection")
    work, sampled = _sample(work, seed)
    model = IsolationForest(contamination=contamination, random_state=seed, n_estimators=200)
    preds = model.fit_predict(work.values)
    scores = model.score_samples(work.values)
    is_anomaly_full = pd.Series(index=df.index, dtype="object")
    score_full = pd.Series(index=df.index, dtype="float64")
    is_anomaly_full.loc[work.index] = preds == -1
    score_full.loc[work.index] = scores
    n_anomalies = int((preds == -1).sum())
    return {"features": features, "contamination": contamination, "n_anomalies": n_anomalies,
            "is_anomaly": [None if pd.isna(v) else bool(v) for v in is_anomaly_full],
            "anomaly_score": [None if pd.isna(v) else round(float(v), 5) for v in score_full], "sampled": sampled}


def _infer_period(index: pd.DatetimeIndex) -> int:
    if len(index) < 4:
        return 1
    diffs = index.to_series().diff().dropna()
    if diffs.empty:
        return 1
    median_days = diffs.dt.total_seconds().median() / 86400
    if median_days <= 1.5:
        return 7  # daily data: weekly seasonality
    if median_days <= 8:
        return 52  # weekly data: yearly seasonality (roughly)
    if median_days <= 31:
        return 12  # monthly data
    return 4  # quarterly


def run_forecast(df: pd.DataFrame, date_col: str, value_col: str, horizon: int = 12,
                  seasonal_period: Optional[int] = None) -> dict:
    if date_col not in df.columns or value_col not in df.columns:
        raise ModelError(f"unknown column(s): {[c for c in (date_col, value_col) if c not in df.columns]}")
    work = df[[date_col, value_col]].dropna().copy()
    work[date_col] = pd.to_datetime(work[date_col])
    work = work.sort_values(date_col).set_index(date_col)
    series = work[value_col].astype(float)
    if len(series) < 4:
        raise ModelError("need at least 4 data points to forecast")
    period = seasonal_period or _infer_period(series.index)
    freq_step = series.index.to_series().diff().median()
    last_date = series.index[-1]
    future_index = [last_date + freq_step * (i + 1) for i in range(horizon)]

    method = "holt_winters"
    if HAVE_STATSMODELS and len(series) >= max(10, period * 2):
        try:
            model = ExponentialSmoothing(series, trend="add",
                                          seasonal="add" if len(series) >= period * 2 else None,
                                          seasonal_periods=period if len(series) >= period * 2 else None,
                                          initialization_method="estimated").fit()
            forecast_values = model.forecast(horizon)
            resid_std = float(np.std(model.resid)) if hasattr(model, "resid") else float(series.std())
        except Exception:  # noqa: BLE001 - fall back below on any convergence failure
            method = "seasonal_naive"
            forecast_values, resid_std = _seasonal_naive(series, horizon, period)
    else:
        method = "seasonal_naive" if HAVE_STATSMODELS else "seasonal_naive_no_statsmodels"
        forecast_values, resid_std = _seasonal_naive(series, horizon, period)

    z = 1.959963985  # ~95% CI
    forecast_list = [round(float(v), 4) for v in np.asarray(forecast_values)]
    return {
        "method": method, "seasonal_period": period, "horizon": horizon,
        "history": [{"date": d.date().isoformat(), "value": round(float(v), 4)} for d, v in series.items()],
        "forecast": [{"date": pd.Timestamp(d).date().isoformat(), "value": v,
                       "lower": round(v - z * resid_std, 4), "upper": round(v + z * resid_std, 4)}
                      for d, v in zip(future_index, forecast_list)],
    }


def _seasonal_naive(series: pd.Series, horizon: int, period: int) -> tuple[np.ndarray, float]:
    """Documented fallback when statsmodels is unavailable or the series is
    too short for Holt-Winters: repeat the last observed seasonal cycle
    (or the last value, with no seasonality) plus the series' linear trend."""
    values = series.values.astype(float)
    resid_std = float(np.std(np.diff(values))) if len(values) > 1 else float(np.std(values)) or 1.0
    if len(values) >= period >= 2:
        last_cycle = values[-period:]
        reps = int(np.ceil(horizon / period))
        out = np.tile(last_cycle, reps)[:horizon]
    else:
        out = np.full(horizon, values[-1])
    return out, resid_std

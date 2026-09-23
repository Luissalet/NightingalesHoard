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
    "run_anomaly", "run_forecast", "HAVE_STATSMODELS", "MAX_TRAIN_ROWS", "FREQ_CHOICES",
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


def _is_id_like(series: pd.Series, n_rows: int) -> bool:
    """A free-text/identifier-ish column (order ids, customer ids, raw dates
    with near-unique values...) whose one-hot encoding would blow up the
    feature matrix and stall training. Excluded only when both conditions
    hold, so a genuinely useful categorical with many values in a large
    dataset (e.g. 40 countries in 50,000 rows) is not thrown away. Only
    applied when features are auto-selected — an explicit feature list from
    the caller is always trusted as-is."""
    if pd.api.types.is_numeric_dtype(series):
        return False
    nunique = series.nunique(dropna=True)
    return nunique > 30 and nunique > 0.05 * n_rows


def train_supervised(df: pd.DataFrame, target: str, features: Optional[list[str]] = None, task: Optional[str] = None,
                      algorithm: Optional[str] = None, test_size: float = 0.2, seed: int = 42, cv: int = 5) -> dict:
    if target not in df.columns:
        raise ModelError(f"unknown target column: {target}")
    excluded_id_like: list[str] = []
    if not features:
        candidates = [c for c in df.columns if c != target]
        features = [c for c in candidates if not _is_id_like(df[c], len(df))] or candidates
        excluded_id_like = [c for c in candidates if c not in features]
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
        "excluded_id_like_columns": excluded_id_like,
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


FREQ_CHOICES = ("auto", "day", "week", "month", "quarter")
_FREQ_RULE = {"day": "D", "week": "W-MON", "month": "MS", "quarter": "QS"}
_SEASONAL_PERIOD_BY_FREQ = {"day": 7, "week": 52, "month": 12, "quarter": 4}


def _choose_freq(dates: pd.DatetimeIndex) -> str:
    """Pick a regular calendar grain to resample onto, from how far the data
    spans and how densely it actually fills that span. Holt-Winters needs a
    *regular* index with enough non-empty periods — not just enough raw rows
    — so a short or genuinely-complete run stays at daily resolution, while a
    long span or one with real gaps (irregular daily transactions, missing
    days) is coarsened until each period reliably has something in it."""
    span_days = max(1, (dates.max() - dates.min()).days + 1)
    density = dates.nunique() / span_days
    span_months = span_days / 30.4368
    if span_months <= 3 or density >= 0.9:
        return "day"
    if span_months <= 15 and density >= 0.5:
        return "week"
    if span_months <= 36:
        return "month"
    return "quarter"


def _resample_series(per_day: pd.Series, freq: str) -> pd.Series:
    """Roll a (possibly gappy) per-day sum onto a fully regular index at the
    chosen grain, summing values landing in the same period and filling any
    period with no rows at all as 0 — the same convention as a sum aggregate
    over an empty group."""
    if freq == "day":
        full_index = pd.date_range(per_day.index.min(), per_day.index.max(), freq="D")
        return per_day.reindex(full_index, fill_value=0.0)
    return per_day.resample(_FREQ_RULE[freq]).sum()


def run_forecast(df: pd.DataFrame, date_col: str, value_col: str, horizon: int = 12,
                  seasonal_period: Optional[int] = None, freq: str = "auto") -> dict:
    if date_col not in df.columns or value_col not in df.columns:
        raise ModelError(f"unknown column(s): {[c for c in (date_col, value_col) if c not in df.columns]}")
    if freq not in FREQ_CHOICES:
        raise ModelError(f"unknown freq: {freq!r}; choose from {FREQ_CHOICES}")
    work = df[[date_col, value_col]].dropna().copy()
    work[date_col] = pd.to_datetime(work[date_col])
    # Normalize to calendar-day granularity before anything else: row-level
    # data (e.g. one row per transaction) commonly has several rows on the
    # same day, sometimes at different times, and a time series needs one
    # point per period. Without this the median step between sorted-but-
    # often-repeated timestamps collapses to zero and every forecast row
    # lands on the same date (a real bug caught during the UI walk).
    per_day = work.groupby(work[date_col].dt.floor("D"))[value_col].sum().sort_index()
    if len(per_day) < 2:
        raise ModelError("need at least 2 distinct dates to forecast")

    resolved_freq = _choose_freq(per_day.index) if freq == "auto" else freq
    series = _resample_series(per_day, resolved_freq).astype(float)
    if len(series) < 4:
        raise ModelError(
            f"only {len(series)} periods at '{resolved_freq}' resolution — need at least 4; "
            "try a finer freq or provide more history"
        )
    period = seasonal_period or _SEASONAL_PERIOD_BY_FREQ[resolved_freq]
    offset = series.index.freq or pd.tseries.frequencies.to_offset(_FREQ_RULE[resolved_freq])
    last_date = series.index[-1]
    future_index = [last_date + offset * (i + 1) for i in range(horizon)]

    # Holt-Winters/ETS needs relatively few points for a trend-only fit; a
    # *seasonal* component additionally needs at least two full cycles, so
    # it's only attempted when the resampled series is long enough for that.
    min_len_for_hw = 8
    use_seasonal = len(series) >= period * 2
    why = None
    if HAVE_STATSMODELS and len(series) >= min_len_for_hw:
        try:
            model = ExponentialSmoothing(series, trend="add",
                                          seasonal="add" if use_seasonal else None,
                                          seasonal_periods=period if use_seasonal else None,
                                          initialization_method="estimated").fit()
            forecast_values = model.forecast(horizon)
            resid_std = float(np.std(model.resid)) if hasattr(model, "resid") else float(series.std())
            method = "holt_winters"
        except Exception as exc:  # noqa: BLE001 - fall back below on any convergence failure
            method = "seasonal_naive"
            why = f"Holt-Winters failed to converge ({exc}); used a seasonal-naive fallback instead."
            forecast_values, resid_std = _seasonal_naive(series, horizon, period)
    else:
        method = "seasonal_naive" if HAVE_STATSMODELS else "seasonal_naive_no_statsmodels"
        why = (
            f"only {len(series)} periods at '{resolved_freq}' resolution — need at least {min_len_for_hw} "
            "for Holt-Winters" if HAVE_STATSMODELS else
            "statsmodels is not installed — install it to get Holt-Winters/ETS forecasting instead of "
            "the seasonal-naive fallback"
        )
        forecast_values, resid_std = _seasonal_naive(series, horizon, period)

    z = 1.959963985  # ~95% CI
    forecast_list = [round(float(v), 4) for v in np.asarray(forecast_values)]
    return {
        "method": method, "seasonal_period": period, "horizon": horizon,
        "freq": freq, "resampled_to": resolved_freq, "why": why,
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

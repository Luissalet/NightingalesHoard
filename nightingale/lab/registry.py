"""Pluggable model backend registry (recycled in spirit from the old
factories/manager pattern, but generic — no domain-specific defaults, no
cloud dependencies): a name maps to a constructor and a bit of metadata, so
`data_model(action="train", algorithm=<name>)` can pick between many more
algorithms than the original `data_model`'s linear/logistic/random_forest/
gradient_boosting quartet, while that original path (`algorithm=None` or
`"auto"`) keeps behaving exactly as before via `workbench/models.py`.

Every trained Lab model is persisted to disk (joblib if installed, else the
stdlib `pickle` — functionally identical for scikit-learn estimators) and
registered as a row in the existing `models` sqlite table (`db.py`), tagged
`params_json.lab = true` so the Lab registry can list/filter its own models
without disturbing the pre-existing `data_model`/`data_cluster` bookkeeping.
"""

from __future__ import annotations

import pickle
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Callable, Optional

import numpy as np
import pandas as pd
from sklearn.ensemble import (
    ExtraTreesClassifier,
    ExtraTreesRegressor,
    HistGradientBoostingClassifier,
    HistGradientBoostingRegressor,
    RandomForestClassifier,
    RandomForestRegressor,
)
from sklearn.gaussian_process import GaussianProcessClassifier, GaussianProcessRegressor
from sklearn.gaussian_process.kernels import RBF, WhiteKernel
from sklearn.inspection import permutation_importance
from sklearn.linear_model import LinearRegression, LogisticRegression, Ridge, RidgeClassifier
from sklearn.metrics import (
    accuracy_score,
    confusion_matrix,
    f1_score,
    mean_absolute_error,
    r2_score,
)
from sklearn.model_selection import cross_val_score, train_test_split
from sklearn.neighbors import KNeighborsClassifier, KNeighborsRegressor
from sklearn.neural_network import MLPClassifier, MLPRegressor
from sklearn.preprocessing import LabelEncoder

from . import LabError
from ..workbench.models import is_id_like  # shared "don't one-hot an id column" heuristic

try:
    import joblib as _joblib

    HAVE_JOBLIB = True
except Exception:  # noqa: BLE001
    HAVE_JOBLIB = False

try:
    from xgboost import XGBClassifier, XGBRegressor

    HAVE_XGBOOST = True
except Exception:  # noqa: BLE001
    HAVE_XGBOOST = False

try:
    from lightgbm import LGBMClassifier, LGBMRegressor

    HAVE_LIGHTGBM = True
except Exception:  # noqa: BLE001
    HAVE_LIGHTGBM = False

__all__ = [
    "HAVE_JOBLIB", "HAVE_XGBOOST", "HAVE_LIGHTGBM",
    "list_backends", "train_model", "save_model", "load_model", "prep_features",
]

MAX_TRAIN_ROWS = 200_000


def _sample(df: pd.DataFrame, seed: int) -> tuple[pd.DataFrame, bool]:
    if len(df) > MAX_TRAIN_ROWS:
        return df.sample(n=MAX_TRAIN_ROWS, random_state=seed), True
    return df, False


def _gpr(seed):
    kernel = RBF(length_scale=1.0) + WhiteKernel(noise_level=1.0)
    return GaussianProcessRegressor(kernel=kernel, normalize_y=True, random_state=seed, n_restarts_optimizer=2)


def _gpc(seed):
    kernel = RBF(length_scale=1.0)
    return GaussianProcessClassifier(kernel=kernel, random_state=seed)


def _not_installed(pkg: str):
    def _raise(seed):
        raise LabError(f"the {pkg!r} backend needs the optional '{pkg}' package; "
                        f"install it with `pip install -r requirements-lab.txt`")
    return _raise


@dataclass(frozen=True)
class BackendInfo:
    name: str
    available: bool
    note: str = ""


_REGRESSION: dict[str, Callable[[int], Any]] = {
    "linear": lambda seed: LinearRegression(),
    "ridge": lambda seed: Ridge(random_state=seed),
    "random_forest": lambda seed: RandomForestRegressor(n_estimators=200, random_state=seed, n_jobs=-1),
    "gradient_boosting": lambda seed: HistGradientBoostingRegressor(random_state=seed),
    "extra_trees": lambda seed: ExtraTreesRegressor(n_estimators=200, random_state=seed, n_jobs=-1),
    "knn": lambda seed: KNeighborsRegressor(n_neighbors=5),
    "mlp": lambda seed: MLPRegressor(random_state=seed, max_iter=1000, hidden_layer_sizes=(64, 32)),
    "gaussian_process": _gpr,
    "xgboost": (lambda seed: XGBRegressor(n_estimators=300, random_state=seed, verbosity=0))
        if HAVE_XGBOOST else _not_installed("xgboost"),
    "lightgbm": (lambda seed: LGBMRegressor(n_estimators=300, random_state=seed, verbosity=-1))
        if HAVE_LIGHTGBM else _not_installed("lightgbm"),
}

_CLASSIFICATION: dict[str, Callable[[int], Any]] = {
    "logistic": lambda seed: LogisticRegression(max_iter=2000),
    "ridge": lambda seed: RidgeClassifier(random_state=seed),
    "random_forest": lambda seed: RandomForestClassifier(n_estimators=200, random_state=seed, n_jobs=-1),
    "gradient_boosting": lambda seed: HistGradientBoostingClassifier(random_state=seed),
    "extra_trees": lambda seed: ExtraTreesClassifier(n_estimators=200, random_state=seed, n_jobs=-1),
    "knn": lambda seed: KNeighborsClassifier(n_neighbors=5),
    "mlp": lambda seed: MLPClassifier(random_state=seed, max_iter=1000, hidden_layer_sizes=(64, 32)),
    "gaussian_process": _gpc,
    "xgboost": (lambda seed: XGBClassifier(n_estimators=300, random_state=seed, verbosity=0,
                                            use_label_encoder=False, eval_metric="logloss"))
        if HAVE_XGBOOST else _not_installed("xgboost"),
    "lightgbm": (lambda seed: LGBMClassifier(n_estimators=300, random_state=seed, verbosity=-1))
        if HAVE_LIGHTGBM else _not_installed("lightgbm"),
}

_ALGOS = {"regression": _REGRESSION, "classification": _CLASSIFICATION}
_OPTIONAL_NOTE = {"xgboost": "requires the optional 'xgboost' package", "lightgbm": "requires the optional 'lightgbm' package"}


def list_backends(task: Optional[str] = None) -> dict:
    """List every backend name for a task (or both tasks), and whether it's
    usable right now — the graceful "not installed" surfacing for optional
    dependencies."""
    tasks = [task] if task else ["regression", "classification"]
    bad = [t for t in tasks if t not in _ALGOS]
    if bad:
        raise LabError(f"unknown task(s): {bad}; choose from {sorted(_ALGOS)}")
    out = {}
    for t in tasks:
        entries = []
        for name in _ALGOS[t]:
            available = not ((name == "xgboost" and not HAVE_XGBOOST) or (name == "lightgbm" and not HAVE_LIGHTGBM))
            entries.append({"name": name, "available": available, "note": "" if available else _OPTIONAL_NOTE.get(name, "")})
        out[t] = entries
    return out


def prep_features(df: pd.DataFrame, features: list[str]) -> tuple[pd.DataFrame, dict]:
    """One-hot encode text columns, impute numeric NaNs with the column mean.
    Mirrors `workbench/models.py::_prep_features` (kept private there) so Lab
    training/tuning/explanation/optimization all agree on the same feature
    matrix shape."""
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


def _py(v: Any) -> Any:
    if isinstance(v, (np.floating,)):
        return float(v)
    if isinstance(v, (np.integer,)):
        return int(v)
    return v


def detect_task(series: pd.Series) -> str:
    non_null = series.dropna()
    if non_null.empty:
        raise LabError("target column has no non-null values")
    if non_null.dtype.name in ("category", "bool") or not pd.api.types.is_numeric_dtype(non_null):
        return "classification"
    nunique = non_null.nunique()
    if nunique <= 20 and (non_null == non_null.round()).all():
        return "classification"
    return "regression"


def train_model(df: pd.DataFrame, target: str, features: Optional[list[str]] = None, task: Optional[str] = None,
                 backend: str = "random_forest", test_size: float = 0.2, seed: int = 42, cv: int = 5) -> dict:
    """Train one registry backend on `target`. Returns metrics plus the fitted
    estimator/encoders/label_encoder/feature columns under `_` keys, which the
    caller (`services.py`) persists and strips before sending the result on."""
    if target not in df.columns:
        raise LabError(f"unknown target column: {target}")
    excluded_id_like: list[str] = []
    if not features:
        candidates = [c for c in df.columns if c != target]
        features = [c for c in candidates if not is_id_like(df[c], len(df))] or candidates
        excluded_id_like = [c for c in candidates if c not in features]
    missing = [c for c in features if c not in df.columns]
    if missing:
        raise LabError(f"unknown feature column(s): {missing}")
    work = df[[target, *features]].dropna(subset=[target])
    if len(work) < 10:
        raise LabError("need at least 10 rows with a non-null target to train a model")
    work, sampled = _sample(work, seed)
    task = task or detect_task(work[target])
    algos = _ALGOS.get(task)
    if not algos:
        raise LabError(f"unknown task: {task}; choose from {sorted(_ALGOS)}")
    if backend not in algos:
        raise LabError(f"unknown backend {backend!r} for {task}; choose from {sorted(algos)}")

    y_raw = work[target]
    label_encoder = None
    if task == "classification" and not pd.api.types.is_numeric_dtype(y_raw):
        label_encoder = LabelEncoder()
        y = label_encoder.fit_transform(y_raw.astype(str))
    else:
        y = y_raw.values

    X, encoders = prep_features(work, features)
    n_classes = len(np.unique(y)) if task == "classification" else None
    stratify = y if (task == "classification" and n_classes and n_classes > 1
                      and min(np.bincount(y.astype(int))) >= 2) else None
    X_train, X_test, y_train, y_test = train_test_split(X, y, test_size=test_size, random_state=seed,
                                                          stratify=stratify)

    model = algos[backend](seed)
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
            scores = cross_val_score(algos[backend](seed), X, y, cv=cv_n, scoring=scoring)
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

    return {
        "task": task, "backend": backend, "target": target, "features": features,
        "excluded_id_like_columns": excluded_id_like, "metrics": metrics,
        "feature_importance": feature_importance,
        "train_rows": int(len(X_train)), "test_rows": int(len(X_test)), "sampled": sampled,
        "fit_seconds": round(fit_seconds, 3),
        "_model": model, "_encoders": encoders, "_label_encoder": label_encoder,
        "_X_columns": list(X.columns), "_X_test": X_test, "_y_test": y_test, "_y_pred": y_pred,
        "_X_train": X_train, "_y_train": y_train,
    }


# ---------------------------------------------------------------------------
# Persistence
# ---------------------------------------------------------------------------

@dataclass
class SavedModel:
    model: Any
    encoders: dict
    label_encoder: Any
    X_columns: list[str]
    features: list[str]
    target: Optional[str]
    task: str
    backend: str


def save_model(models_dir: Path, model_id: int, result: dict) -> str:
    """Persist a trained model + its feature/encoder metadata to disk. Uses
    joblib when installed (better numpy-array compression), else pickle."""
    models_dir.mkdir(parents=True, exist_ok=True)
    payload = SavedModel(
        model=result["_model"], encoders=result["_encoders"], label_encoder=result.get("_label_encoder"),
        X_columns=result["_X_columns"], features=result["features"], target=result.get("target"),
        task=result["task"], backend=result["backend"],
    )
    path = models_dir / f"model_{model_id}.joblib"
    if HAVE_JOBLIB:
        _joblib.dump(payload, path)
    else:
        with path.open("wb") as fh:
            pickle.dump(payload, fh)
    return str(path)


def load_model(path: str) -> SavedModel:
    p = Path(path)
    if not p.exists():
        raise LabError(f"model artifact not found on disk: {p}")
    if HAVE_JOBLIB:
        try:
            return _joblib.load(p)
        except Exception:  # noqa: BLE001 - fall through to pickle for a pickle-written file
            pass
    with p.open("rb") as fh:
        return pickle.load(fh)


def predict_with(saved: SavedModel, df: pd.DataFrame) -> np.ndarray:
    """Run a saved model's prediction pipeline (encode -> align columns ->
    predict -> decode labels) over new data."""
    X, _ = prep_features(df, saved.features)
    X = X.reindex(columns=saved.X_columns, fill_value=0)
    preds = saved.model.predict(X)
    if saved.label_encoder is not None:
        preds = saved.label_encoder.inverse_transform(preds.astype(int))
    return preds

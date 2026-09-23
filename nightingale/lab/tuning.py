"""Hyperparameter tuning: Optuna (TPE) when installed, otherwise a randomized
sklearn search — both return the same shape (best params + a trial history
the UI can chart) and both save the tuned model through the same
`registry.train_model`-shaped result, ready for `registry.save_model`.
"""

from __future__ import annotations

from typing import Any, Optional

import numpy as np
import pandas as pd
from sklearn.model_selection import ParameterSampler, cross_val_score

from . import LabError
from .registry import _ALGOS, detect_task, prep_features, train_model

try:
    import optuna
    from optuna.samplers import TPESampler

    optuna.logging.set_verbosity(optuna.logging.WARNING)
    HAVE_OPTUNA = True
except Exception:  # noqa: BLE001
    HAVE_OPTUNA = False

__all__ = ["HAVE_OPTUNA", "DEFAULT_PARAM_SPACE", "tune_model"]

# Reasonable default search spaces per backend, used when the caller doesn't
# supply `param_space`. Each value is (kind, ...): "float"/"int" -> (lo, hi,
# log?), "categorical" -> list of choices.
DEFAULT_PARAM_SPACE: dict[str, dict[str, tuple]] = {
    "random_forest": {"n_estimators": ("int", 50, 400), "max_depth": ("int", 2, 20)},
    "extra_trees": {"n_estimators": ("int", 50, 400), "max_depth": ("int", 2, 20)},
    "gradient_boosting": {"max_iter": ("int", 50, 400), "learning_rate": ("float", 0.01, 0.3, True),
                          "max_depth": ("int", 2, 12)},
    "ridge": {"alpha": ("float", 0.001, 10.0, True)},
    "knn": {"n_neighbors": ("int", 2, 30)},
    "mlp": {"alpha": ("float", 1e-5, 1e-1, True)},
    "xgboost": {"n_estimators": ("int", 50, 400), "max_depth": ("int", 2, 12), "learning_rate": ("float", 0.01, 0.3, True)},
    "lightgbm": {"n_estimators": ("int", 50, 400), "max_depth": ("int", 2, 12), "learning_rate": ("float", 0.01, 0.3, True)},
}


def _param_space_for(backend: str, custom: Optional[dict]) -> dict[str, tuple]:
    space = custom or DEFAULT_PARAM_SPACE.get(backend)
    if not space:
        raise LabError(f"no default tuning search space for backend {backend!r}; pass param_space explicitly")
    return space


def _sample_params(space: dict[str, tuple], rng: np.random.RandomState) -> dict:
    out = {}
    for name, spec in space.items():
        kind = spec[0]
        if kind == "categorical":
            out[name] = rng.choice(spec[1])
        elif kind == "int":
            out[name] = int(rng.randint(spec[1], spec[2] + 1))
        elif kind == "float":
            lo, hi = spec[1], spec[2]
            log = len(spec) > 3 and spec[3]
            out[name] = float(np.exp(rng.uniform(np.log(lo), np.log(hi)))) if log else float(rng.uniform(lo, hi))
        else:
            raise LabError(f"unknown param kind: {kind}")
    return out


def _cv_score(df: pd.DataFrame, target: str, features: list[str], task: str, backend: str, params: dict,
              seed: int, cv: int) -> float:
    factory = _ALGOS[task][backend]
    model = factory(seed)
    model.set_params(**params)
    work = df[[target, *features]].dropna(subset=[target])
    X, _ = prep_features(work, features)
    y = work[target].to_numpy() if task == "regression" else pd.factorize(work[target])[0]
    scoring = "r2" if task == "regression" else "accuracy"
    cv_n = min(cv, max(2, len(X) // 5))
    scores = cross_val_score(model, X, y, cv=cv_n, scoring=scoring)
    return float(scores.mean())


def tune_model(df: pd.DataFrame, target: str, features: Optional[list[str]] = None, task: Optional[str] = None,
               backend: str = "random_forest", param_space: Optional[dict] = None, n_trials: int = 20,
               timeout: Optional[float] = None, cv: int = 5, seed: int = 42, test_size: float = 0.2) -> dict:
    """Search `param_space` (or a sensible default for `backend`) for the best
    cross-validated score, then retrain the winning params on a normal
    train/test split via `registry.train_model` so the result is a regular,
    persistable trained model plus the tuning trial history."""
    if not features:
        features = [c for c in df.columns if c != target]
    task = task or detect_task(df[target])
    if task not in _ALGOS:
        raise LabError(f"unknown task: {task}")
    if backend not in _ALGOS[task]:
        raise LabError(f"unknown backend {backend!r} for {task}; choose from {sorted(_ALGOS[task])}")
    space = _param_space_for(backend, param_space)

    trials: list[dict] = []
    if HAVE_OPTUNA:
        def objective(trial: "optuna.Trial") -> float:
            params = {}
            for name, spec in space.items():
                kind = spec[0]
                if kind == "categorical":
                    params[name] = trial.suggest_categorical(name, list(spec[1]))
                elif kind == "int":
                    params[name] = trial.suggest_int(name, int(spec[1]), int(spec[2]))
                else:
                    log = len(spec) > 3 and spec[3]
                    params[name] = trial.suggest_float(name, float(spec[1]), float(spec[2]), log=bool(log))
            score = _cv_score(df, target, features, task, backend, params, seed, cv)
            trials.append({"trial": len(trials), "params": dict(params), "score": round(score, 4)})
            return score

        study = optuna.create_study(direction="maximize", sampler=TPESampler(seed=seed))
        study.optimize(objective, n_trials=n_trials, timeout=timeout, show_progress_bar=False)
        best_params = dict(study.best_params)
        best_score = float(study.best_value)
        method = "optuna_tpe"
    else:
        rng = np.random.RandomState(seed)
        best_params, best_score = None, -np.inf
        for i in range(n_trials):
            params = _sample_params(space, rng)
            try:
                score = _cv_score(df, target, features, task, backend, params, seed, cv)
            except Exception:  # noqa: BLE001 - an invalid sampled combination just scores badly, not fatally
                score = -np.inf
            trials.append({"trial": i, "params": dict(params), "score": round(score, 4) if score != -np.inf else None})
            if score > best_score:
                best_score, best_params = score, params
        method = "random_search"
        if best_params is None:
            raise LabError("no trial produced a valid score; check param_space")

    result = train_model(df, target, features=features, task=task, backend=backend, test_size=test_size, seed=seed)
    result["_model"].set_params(**best_params)
    # retrain the final model with the tuned hyperparameters on the same split `train_model` used,
    # then refresh the headline metrics so they reflect the tuned model, not the pre-tuning default one
    result["_model"].fit(result["_X_train"], result["_y_train"])
    y_pred = result["_model"].predict(result["_X_test"])
    if task == "regression":
        from sklearn.metrics import mean_absolute_error, r2_score
        y_test_arr = np.asarray(result["_y_test"], dtype=float)
        y_pred_arr = np.asarray(y_pred, dtype=float)
        result["metrics"] = {
            "r2": round(float(r2_score(y_test_arr, y_pred_arr)), 4),
            "mae": round(float(mean_absolute_error(y_test_arr, y_pred_arr)), 4),
            "rmse": round(float(np.sqrt(np.mean((y_test_arr - y_pred_arr) ** 2))), 4),
        }
    else:
        from sklearn.metrics import accuracy_score, f1_score
        n_classes = len(np.unique(result["_y_test"]))
        average = "binary" if n_classes == 2 else "macro"
        result["metrics"] = {
            "accuracy": round(float(accuracy_score(result["_y_test"], y_pred)), 4),
            "f1": round(float(f1_score(result["_y_test"], y_pred, average=average, zero_division=0)), 4),
        }
    result["_y_pred"] = y_pred
    result["tuning"] = {"method": method, "best_params": best_params, "best_cv_score": round(best_score, 4),
                         "n_trials": len(trials), "trials": trials}
    return result

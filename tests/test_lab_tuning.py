"""Hyperparameter tuning: Optuna (TPE) when installed, and the randomized
sklearn-search fallback when it isn't (exercised via monkeypatch either way,
so both paths run regardless of what's installed in the test environment)."""

import numpy as np
import pandas as pd
import pytest

from nightingale.lab import LabError, tuning


def make_regression_df(n=300, seed=0):
    rng = np.random.default_rng(seed)
    x1 = rng.normal(0, 1, n)
    x2 = rng.normal(0, 1, n)
    y = 3 * x1 - 2 * x2 + rng.normal(0, 0.2, n)
    return pd.DataFrame({"x1": x1, "x2": x2, "y": y})


def test_tune_model_with_optuna_when_available():
    if not tuning.HAVE_OPTUNA:
        pytest.skip("optuna not installed in this environment")
    df = make_regression_df()
    result = tuning.tune_model(df, "y", ["x1", "x2"], "regression", "random_forest", n_trials=5, seed=0)
    assert result["tuning"]["method"] == "optuna_tpe"
    assert result["tuning"]["best_params"]
    assert len(result["tuning"]["trials"]) == 5
    assert "r2" in result["metrics"]
    assert result["_model"] is not None


def test_tune_model_random_search_fallback(monkeypatch):
    monkeypatch.setattr(tuning, "HAVE_OPTUNA", False)
    df = make_regression_df()
    result = tuning.tune_model(df, "y", ["x1", "x2"], "regression", "random_forest", n_trials=6, seed=0)
    assert result["tuning"]["method"] == "random_search"
    assert result["tuning"]["best_params"]
    assert len(result["tuning"]["trials"]) == 6
    assert "r2" in result["metrics"]


def test_tune_model_unknown_backend_needs_explicit_param_space():
    df = make_regression_df()
    with pytest.raises(LabError):
        tuning.tune_model(df, "y", ["x1", "x2"], "regression", "gaussian_process", n_trials=3, seed=0)


def test_tune_model_custom_param_space(monkeypatch):
    monkeypatch.setattr(tuning, "HAVE_OPTUNA", False)
    df = make_regression_df()
    space = {"alpha": ("float", 0.01, 5.0, True)}
    result = tuning.tune_model(df, "y", ["x1", "x2"], "regression", "ridge", param_space=space, n_trials=4, seed=0)
    assert "alpha" in result["tuning"]["best_params"]


def test_tune_model_unknown_task_raises():
    df = make_regression_df()
    with pytest.raises(LabError):
        tuning.tune_model(df, "y", ["x1", "x2"], "not_a_task", "random_forest", n_trials=2, seed=0)


def make_classification_df(n=200, seed=2):
    rng = np.random.default_rng(seed)
    x1 = rng.normal(0, 1, n)
    x2 = rng.normal(0, 1, n)
    label = np.where(x1 + x2 > 0, "yes", "no")
    return pd.DataFrame({"x1": x1, "x2": x2, "label": label})


def test_tune_model_classification_result_includes_confusion_matrix(monkeypatch):
    # `tune_model` rebuilds `metrics` after retraining with the tuned
    # hyperparameters; that rebuild must keep the same shape train_model
    # produces for classification (accuracy/f1/confusion_matrix), or callers
    # relying on "same shape as train, plus tuning" (registry compare, the
    # UI) silently lose the confusion matrix for every tuned model.
    monkeypatch.setattr(tuning, "HAVE_OPTUNA", False)
    df = make_classification_df()
    result = tuning.tune_model(df, "label", ["x1", "x2"], "classification", "random_forest", n_trials=3, seed=0)
    assert "confusion_matrix" in result["metrics"]
    cm = result["metrics"]["confusion_matrix"]
    n = len(cm["labels"])
    assert len(cm["matrix"]) == n
    assert all(len(row) == n for row in cm["matrix"])


@pytest.mark.parametrize("bad_space", [
    {"n_estimators": ["int", "oops", 100]},
    {"n_estimators": ["int", None, 100]},
    {"n_estimators": ["int", 100, 50]},
    {"n_estimators": []},
    {"n_estimators": ["not_a_kind", 1, 2]},
    {"c": ["categorical", []]},
    "not_a_dict",
])
def test_tune_model_malformed_param_space_raises_lab_error_not_a_raw_exception(bad_space):
    # A malformed custom param_space used to blow up deep inside Optuna's
    # trial-suggestion calls (TypeError/ValueError from a bad `int()`/`float()`
    # conversion) instead of failing fast with a clear, API-safe LabError --
    # and a bare TypeError isn't caught by the API layer's ValueError/
    # LookupError -> 4xx mapping, so it would have surfaced as a 500.
    df = make_regression_df()
    with pytest.raises(LabError):
        tuning.tune_model(df, "y", ["x1", "x2"], "regression", "random_forest",
                           param_space=bad_space, n_trials=2, cv=2, seed=0)

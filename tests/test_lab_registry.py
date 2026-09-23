"""Model registry: backend listing (with graceful "not installed" for
optional deps), training every backend, and joblib/pickle persistence
round-trips."""

import numpy as np
import pandas as pd
import pytest

from nightingale.lab import LabError, registry


def make_regression_df(n=300, seed=0):
    rng = np.random.default_rng(seed)
    x1 = rng.normal(0, 1, n)
    x2 = rng.normal(0, 1, n)
    y = 3 * x1 - 2 * x2 + rng.normal(0, 0.2, n)
    return pd.DataFrame({"x1": x1, "x2": x2, "y": y})


def make_classification_df(n=300, seed=1):
    rng = np.random.default_rng(seed)
    x1 = rng.normal(0, 1, n)
    x2 = rng.normal(0, 1, n)
    label = np.where(x1 + x2 > 0, "yes", "no")
    return pd.DataFrame({"x1": x1, "x2": x2, "label": label})


def test_list_backends_reports_availability():
    result = registry.list_backends()
    assert "regression" in result and "classification" in result
    names = {b["name"] for b in result["regression"]}
    assert {"linear", "ridge", "random_forest", "gradient_boosting", "extra_trees", "knn", "mlp",
            "gaussian_process", "xgboost", "lightgbm"} <= names
    xgb = next(b for b in result["regression"] if b["name"] == "xgboost")
    assert xgb["available"] is registry.HAVE_XGBOOST


def test_list_backends_unknown_task_raises():
    with pytest.raises(LabError):
        registry.list_backends("nope")


@pytest.mark.parametrize("backend", ["linear", "ridge", "random_forest", "gradient_boosting", "extra_trees",
                                       "knn", "mlp", "gaussian_process"])
def test_train_model_regression_backends(backend):
    df = make_regression_df()
    result = registry.train_model(df, "y", ["x1", "x2"], "regression", backend, seed=0)
    assert result["backend"] == backend
    assert result["task"] == "regression"
    assert "r2" in result["metrics"]
    assert result["_model"] is not None


@pytest.mark.parametrize("backend", ["logistic", "ridge", "random_forest", "gradient_boosting", "extra_trees",
                                       "knn", "mlp", "gaussian_process"])
def test_train_model_classification_backends(backend):
    df = make_classification_df()
    result = registry.train_model(df, "label", ["x1", "x2"], "classification", backend, seed=0)
    assert result["backend"] == backend
    assert result["task"] == "classification"
    assert "accuracy" in result["metrics"]


def test_train_model_unknown_backend_raises():
    df = make_regression_df()
    with pytest.raises(LabError):
        registry.train_model(df, "y", ["x1", "x2"], "regression", "not_a_backend", seed=0)


def test_optional_backend_not_installed_raises_clear_message(monkeypatch):
    monkeypatch.setattr(registry, "HAVE_XGBOOST", False)
    monkeypatch.setitem(registry._REGRESSION, "xgboost", registry._not_installed("xgboost"))
    df = make_regression_df()
    with pytest.raises(LabError, match="xgboost"):
        registry.train_model(df, "y", ["x1", "x2"], "regression", "xgboost", seed=0)


@pytest.mark.skipif(not registry.HAVE_XGBOOST, reason="xgboost not installed in this environment")
def test_xgboost_backend_trains_when_installed():
    df = make_regression_df()
    result = registry.train_model(df, "y", ["x1", "x2"], "regression", "xgboost", seed=0)
    assert result["metrics"]["r2"] > 0.5


@pytest.mark.skipif(not registry.HAVE_LIGHTGBM, reason="lightgbm not installed in this environment")
def test_lightgbm_backend_trains_when_installed():
    df = make_regression_df()
    result = registry.train_model(df, "y", ["x1", "x2"], "regression", "lightgbm", seed=0)
    assert result["metrics"]["r2"] > 0.5


def test_save_and_load_model_round_trip(tmp_path):
    df = make_regression_df()
    result = registry.train_model(df, "y", ["x1", "x2"], "regression", "random_forest", seed=0)
    path = registry.save_model(tmp_path, 1, result)
    assert (tmp_path / "model_1.joblib").exists()
    saved = registry.load_model(path)
    assert saved.backend == "random_forest"
    assert saved.task == "regression"
    assert saved.features == ["x1", "x2"]
    preds = registry.predict_with(saved, df)
    assert len(preds) == len(df)


def test_save_and_load_model_round_trip_without_joblib(tmp_path, monkeypatch):
    """The fallback path (no joblib installed) must round-trip identically."""
    monkeypatch.setattr(registry, "HAVE_JOBLIB", False)
    df = make_classification_df()
    result = registry.train_model(df, "label", ["x1", "x2"], "classification", "logistic", seed=0)
    path = registry.save_model(tmp_path, 2, result)
    saved = registry.load_model(path)
    preds = registry.predict_with(saved, df)
    assert set(preds) <= {"yes", "no"}


def test_load_missing_artifact_raises():
    with pytest.raises(LabError):
        registry.load_model("/does/not/exist/model.joblib")

"""Model diagnostics: regression/classification metrics, residuals,
predicted-vs-actual, error-by-group, bias-over-time, and the fit-gap
(train/CV/holdout) diagnosis."""

import numpy as np
import pandas as pd
import pytest

from nightingale.lab import LabError, diagnostics, registry


def make_regression_df(n=300, seed=0, with_extras=True):
    rng = np.random.default_rng(seed)
    x1 = rng.normal(0, 1, n)
    x2 = rng.normal(0, 1, n)
    y = 3 * x1 - 2 * x2 + rng.normal(0, 0.2, n)
    df = pd.DataFrame({"x1": x1, "x2": x2, "y": y})
    if with_extras:
        df["region"] = rng.choice(["north", "south"], n)
        df["date"] = pd.date_range("2023-01-01", periods=n, freq="D")
    return df


def make_classification_df(n=300, seed=1):
    rng = np.random.default_rng(seed)
    x1 = rng.normal(0, 1, n)
    x2 = rng.normal(0, 1, n)
    label = np.where(x1 + x2 > 0, "yes", "no")
    return pd.DataFrame({"x1": x1, "x2": x2, "label": label,
                          "region": rng.choice(["north", "south"], n),
                          "date": pd.date_range("2023-01-01", periods=n, freq="D")})


def _saved_regression():
    df = make_regression_df()
    result = registry.train_model(df, "y", ["x1", "x2"], "regression", "random_forest", seed=0)
    return registry.SavedModel(model=result["_model"], encoders=result["_encoders"],
                                label_encoder=result["_label_encoder"], X_columns=result["_X_columns"],
                                features=result["features"], target="y", task="regression", backend="random_forest"), df


def _saved_classification():
    df = make_classification_df()
    result = registry.train_model(df, "label", ["x1", "x2"], "classification", "logistic", seed=0)
    return registry.SavedModel(model=result["_model"], encoders=result["_encoders"],
                                label_encoder=result["_label_encoder"], X_columns=result["_X_columns"],
                                features=result["features"], target="label", task="classification",
                                backend="logistic"), df


def test_evaluate_regression_metrics_and_residuals():
    saved, df = _saved_regression()
    result = diagnostics.evaluate_model(saved, df)
    assert result["metrics"]["r2"] > 0.8
    assert "rmse" in result["metrics"] and "mae" in result["metrics"]
    assert len(result["residuals"]["vs_predicted"]) > 0
    assert result["residuals"]["histogram"]["counts"]
    assert result["predicted_vs_actual"]


def test_evaluate_regression_error_by_group_and_bias_over_time():
    saved, df = _saved_regression()
    result = diagnostics.evaluate_model(saved, df, date_col="date", group_col="region")
    assert {"north", "south"} == {r["group"] for r in result["error_by_group"]}
    assert result["bias_over_time"]


def test_evaluate_regression_fit_gap_diagnosis_with_training_data():
    saved, df = _saved_regression()
    result = diagnostics.evaluate_model(saved, df, df_train=df, seed=0)
    diag = result["fit_diagnosis"]
    assert diag["train_score"] is not None
    assert diag["learning_curve"]
    assert diag["diagnosis"] != "unknown"


def test_evaluate_without_training_data_skips_fit_diagnosis():
    saved, df = _saved_regression()
    result = diagnostics.evaluate_model(saved, df)
    assert "note" in result["fit_diagnosis"]


def test_evaluate_classification_metrics_and_confusion():
    saved, df = _saved_classification()
    result = diagnostics.evaluate_model(saved, df)
    assert result["metrics"]["accuracy"] > 0.6
    assert result["metrics"]["confusion_matrix"]["matrix"]
    assert "roc_auc" in result["metrics"]


def test_evaluate_classification_calibration_and_error_by_group():
    saved, df = _saved_classification()
    result = diagnostics.evaluate_model(saved, df, group_col="region", date_col="date")
    assert result.get("calibration") is not None
    assert result["error_by_group"]
    assert result["bias_over_time"]


def test_evaluate_missing_target_column_raises():
    saved, df = _saved_regression()
    with pytest.raises(LabError):
        diagnostics.evaluate_model(saved, df.drop(columns=["y"]))


def test_evaluate_unknown_group_col_raises():
    saved, df = _saved_regression()
    with pytest.raises(LabError):
        diagnostics.evaluate_model(saved, df, group_col="nope")


def test_evaluate_classification_unseen_label_is_dropped_not_fatal():
    saved, df = _saved_classification()
    extra = df.iloc[:5].copy()
    extra["label"] = "maybe"  # never seen at training time
    combined = pd.concat([df, extra], ignore_index=True)
    result = diagnostics.evaluate_model(saved, combined)
    assert result["n_rows"] == len(df)  # the unseen-label rows were excluded, not fatal

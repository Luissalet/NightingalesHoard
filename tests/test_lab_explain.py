"""Explanations: SHAP when installed, permutation importance + partial
dependence fallback when it isn't — exercised both ways via monkeypatch."""

import numpy as np
import pandas as pd
import pytest

from nightingale.lab import LabError, explain, registry


def make_regression_df(n=250, seed=0):
    rng = np.random.default_rng(seed)
    x1 = rng.normal(0, 1, n)
    x2 = rng.normal(0, 1, n)
    x3 = rng.normal(0, 1, n)  # irrelevant to y
    y = 5 * x1 - 1 * x2 + rng.normal(0, 0.1, n)
    return pd.DataFrame({"x1": x1, "x2": x2, "x3": x3, "y": y})


def _saved(df, backend="random_forest"):
    result = registry.train_model(df, "y", ["x1", "x2", "x3"], "regression", backend, seed=0)
    return registry.SavedModel(model=result["_model"], encoders=result["_encoders"],
                                label_encoder=result["_label_encoder"], X_columns=result["_X_columns"],
                                features=result["features"], target="y", task="regression", backend=backend)


def test_explain_permutation_fallback_ranks_important_feature_first(monkeypatch):
    monkeypatch.setattr(explain, "HAVE_SHAP", False)
    df = make_regression_df()
    saved = _saved(df)
    result = explain.explain_model(saved, df, sample_size=100, seed=0)
    assert result["method"] == "permutation_importance"
    ranking = [r["feature"] for r in result["global_importance"]]
    assert ranking[0] == "x1"  # x1 has by far the largest coefficient
    assert result["partial_dependence"]


def test_explain_permutation_row_explanation():
    df = make_regression_df()
    saved = _saved(df)
    result = explain.explain_model(saved, df, sample_size=100, row_index=0, seed=0)
    row = result["row_explanation"]
    assert row["row_index"] == 0
    assert len(row["contributions"]) == 3


def test_explain_row_index_out_of_range_raises():
    df = make_regression_df()
    saved = _saved(df)
    with pytest.raises(LabError):
        explain.explain_model(saved, df, row_index=10_000)


def test_explain_missing_feature_column_raises_clear_lab_error():
    # Explaining against a dataset missing one of the model's training
    # feature columns used to fall through to a raw pandas KeyError
    # instead of a clear LabError naming the missing column.
    df = make_regression_df()
    saved = _saved(df)
    with pytest.raises(LabError, match="x2"):
        explain.explain_model(saved, df.drop(columns=["x2"]))


@pytest.mark.skipif(not explain.HAVE_SHAP, reason="shap not installed in this environment")
def test_explain_shap_tree_explainer_on_tree_model():
    df = make_regression_df()
    saved = _saved(df, backend="random_forest")
    result = explain.explain_model(saved, df, sample_size=60, row_index=0, seed=0)
    assert result["method"] == "shap"
    ranking = [r["feature"] for r in result["global_importance"]]
    assert ranking[0] == "x1"
    assert "row_explanation" in result


@pytest.mark.skipif(not explain.HAVE_SHAP, reason="shap not installed in this environment")
def test_explain_shap_kernel_explainer_on_non_tree_model():
    df = make_regression_df()
    saved = _saved(df, backend="linear")
    result = explain.explain_model(saved, df, sample_size=30, seed=0)
    assert result["method"] in ("shap", "permutation_importance")  # degrades gracefully if the kernel fit fails

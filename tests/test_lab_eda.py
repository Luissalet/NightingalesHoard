"""EDA: correlation, null patterns/imputation preview, outliers, encoding
suggestions, and the 0-100 quality score."""

import numpy as np
import pandas as pd
import pytest

from nightingale.lab import LabError, eda


def make_df(n=300, seed=0):
    rng = np.random.default_rng(seed)
    x1 = rng.normal(0, 1, n)
    x2 = 2 * x1 + rng.normal(0, 0.1, n)  # strongly correlated with x1
    x3 = rng.normal(0, 1, n)
    cat = rng.choice(["a", "b", "c"], n)
    return pd.DataFrame({"x1": x1, "x2": x2, "x3": x3, "cat": cat})


def test_correlation_matrix_finds_the_strong_pair():
    df = make_df()
    result = eda.correlation_matrix(df)
    assert set(result["columns"]) == {"x1", "x2", "x3"}
    top = result["top_pairs"][0]
    assert {top["a"], top["b"]} == {"x1", "x2"}
    assert abs(top["pearson"]) > 0.95
    assert "spearman" in top


def test_correlation_matrix_needs_two_numeric_columns():
    df = pd.DataFrame({"x": [1, 2, 3]})
    with pytest.raises(LabError):
        eda.correlation_matrix(df)


def test_null_patterns_reports_co_occurrence():
    df = make_df(n=100)
    mask = np.zeros(100, dtype=bool)
    mask[:20] = True
    df.loc[mask, "x1"] = None
    df.loc[mask, "x3"] = None  # nulls together with x1
    result = eda.null_patterns(df)
    by_col = {c["column"]: c for c in result["columns"]}
    assert by_col["x1"]["nulls"] == 20
    pair_cols = {(p["a"], p["b"]) for p in result["co_occurrence"]}
    assert ("x1", "x3") in pair_cols or ("x3", "x1") in pair_cols


def test_imputation_preview_mean_and_mode():
    df = pd.DataFrame({"num": [1.0, 2.0, None, 4.0], "cat": ["a", "a", None, "b"]})
    result = eda.imputation_preview(df, "num", "mean")
    assert result["n_missing"] == 1
    assert result["after"]["count"] == 4
    assert result["before"]["count"] == 3

    result2 = eda.imputation_preview(df, "cat", "mode")
    assert result2["n_missing"] == 1
    assert result2["after"]["count"] == 4


def test_imputation_preview_mean_on_text_column_raises():
    df = pd.DataFrame({"cat": ["a", None, "b"]})
    with pytest.raises(LabError):
        eda.imputation_preview(df, "cat", "mean")


def test_outliers_report_iqr_and_zscore():
    values = list(np.random.default_rng(0).normal(0, 1, 100)) + [50, -50]  # two obvious outliers
    df = pd.DataFrame({"x": values})
    result = eda.outliers_report(df, ["x"])
    col = result["columns"][0]
    assert col["iqr"]["count"] >= 2
    assert col["zscore"]["count"] >= 2
    assert col["box_plot"]["median"] is not None


def test_encoding_suggestions_flags_id_like_and_categoricals():
    rng = np.random.default_rng(0)
    n = 1000
    df = pd.DataFrame({
        "id": [f"ID{i:04d}" for i in range(n)],  # unique -> id-like
        "skewed": rng.exponential(2, n),
        "low_card": rng.choice(["x", "y"], n),
        "high_card": rng.choice([f"v{i}" for i in range(25)], n),  # 25 distinct values, well under 5% of n
    })
    result = eda.encoding_suggestions(df)
    by_col = {c["column"]: c for c in result["columns"]}
    assert by_col["id"]["kind"] == "id_like"
    assert by_col["skewed"]["suggestion"] in ("log", "yeo_johnson")
    assert by_col["low_card"]["suggestion"] == "one_hot"
    assert "encoding" in by_col["high_card"]["suggestion"] or "target" in by_col["high_card"]["suggestion"]


def test_quality_score_drops_with_nulls_and_duplicates():
    rng = np.random.default_rng(0)
    df = pd.DataFrame({"a": rng.normal(0, 1, 200), "b": rng.normal(0, 1, 200),
                        "c": rng.choice(["x", "y", "z"], 200)})
    baseline = eda.quality_score(df)
    assert baseline["score"] > 80

    dirty = df.copy()
    dirty.loc[:40, "a"] = None  # inject nulls
    dirty = pd.concat([dirty, dirty.iloc[:30]], ignore_index=True)  # inject duplicate rows
    degraded = eda.quality_score(dirty)
    assert degraded["score"] < baseline["score"]
    assert degraded["components"]["completeness"] < baseline["components"]["completeness"]
    assert degraded["components"]["uniqueness"] < baseline["components"]["uniqueness"]


def test_quality_score_flags_placeholder_values_as_invalid():
    df = pd.DataFrame({"a": list(range(100)), "b": ["ok"] * 90 + ["N/A"] * 10})
    result = eda.quality_score(df)
    assert result["components"]["validity"] < 1.0


def test_quality_score_flags_constant_columns():
    df = pd.DataFrame({"a": list(range(50)), "constant": [1] * 50})
    result = eda.quality_score(df)
    assert "constant" in result["constant_columns"]
    assert result["components"]["constant_columns"] < 1.0


def test_eda_profile_bundle_has_every_section():
    df = make_df()
    result = eda.eda_profile(df)
    assert "correlation" in result
    assert "null_patterns" in result
    assert "outliers" in result
    assert "encoding_suggestions" in result
    assert "quality_score" in result

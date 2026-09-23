"""Drift: distribution comparison between two datasets/slices (KS, PSI,
Wasserstein, mean/sd shift), and curve comparison (overlay, noise/gaps,
growth rate, systematic bias between groups)."""

import numpy as np
import pandas as pd
import pytest

from nightingale.lab import LabError, drift


def test_compare_distributions_flags_a_shifted_column():
    rng = np.random.default_rng(0)
    n = 500
    df_a = pd.DataFrame({"stable": rng.normal(0, 1, n), "shifted": rng.normal(0, 1, n)})
    df_b = pd.DataFrame({"stable": rng.normal(0, 1, n), "shifted": rng.normal(5, 1, n)})  # big mean shift
    result = drift.compare_distributions(df_a, df_b)
    by_col = {r["column"]: r for r in result["columns"]}
    assert by_col["shifted"]["flagged"] is True
    assert by_col["shifted"]["psi"] > by_col["stable"]["psi"]
    assert by_col["shifted"]["ks_pvalue"] < 0.05
    assert result["n_flagged"] >= 1


def test_compare_distributions_no_shared_columns_raises():
    df_a = pd.DataFrame({"a": [1, 2, 3]})
    df_b = pd.DataFrame({"b": [1, 2, 3]})
    with pytest.raises(LabError):
        drift.compare_distributions(df_a, df_b)


def test_compare_distributions_stable_column_not_flagged():
    rng = np.random.default_rng(1)
    n = 400
    df_a = pd.DataFrame({"x": rng.normal(0, 1, n)})
    df_b = pd.DataFrame({"x": rng.normal(0, 1, n)})
    result = drift.compare_distributions(df_a, df_b)
    assert result["columns"][0]["flagged"] is False


def test_curve_comparison_single_series_detects_gap_and_growth():
    x = list(range(20)) + list(range(30, 40))  # a gap between 20 and 30
    y = [i * 2.0 for i in range(len(x))]
    df = pd.DataFrame({"x": x, "y": y})
    result = drift.curve_comparison(df, "x", "y")
    series = result["series"]
    assert series["gaps"]
    assert series["growth_rate"]["mean"] is not None
    assert len(series["overlay"]) == len(x)


def test_curve_comparison_grouped_with_systematic_bias():
    rng = np.random.default_rng(0)
    x = np.linspace(0, 10, 50)
    df = pd.concat([
        pd.DataFrame({"x": x, "y": np.sin(x) + rng.normal(0, 0.02, 50), "g": "a"}),
        pd.DataFrame({"x": x, "y": np.sin(x) + 2.0 + rng.normal(0, 0.02, 50), "g": "b"}),  # offset by +2
    ], ignore_index=True)
    result = drift.curve_comparison(df, "x", "y", group="g")
    assert set(result["groups"]) == {"a", "b"}
    bias = result["systematic_bias"]
    assert bias is not None
    assert abs(bias["mean_bias"] - 2.0) < 0.2


def test_curve_comparison_unknown_column_raises():
    df = pd.DataFrame({"x": [1, 2, 3], "y": [1, 2, 3]})
    with pytest.raises(LabError):
        drift.curve_comparison(df, "x", "nope")

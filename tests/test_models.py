"""Models: supervised (regression + classification), clustering, PCA, anomaly, forecast."""

import numpy as np
import pandas as pd
import pytest

from nightingale.workbench import models as m


def make_regression_df(n=200, seed=1):
    rng = np.random.default_rng(seed)
    x1 = rng.normal(0, 1, n)
    x2 = rng.normal(0, 1, n)
    y = 3 * x1 - 2 * x2 + rng.normal(0, 0.2, n)
    return pd.DataFrame({"x1": x1, "x2": x2, "y": y})


def make_classification_df(n=200, seed=2):
    rng = np.random.default_rng(seed)
    x1 = rng.normal(0, 1, n)
    x2 = rng.normal(0, 1, n)
    label = np.where(x1 + x2 > 0, "yes", "no")
    return pd.DataFrame({"x1": x1, "x2": x2, "label": label})


def test_detect_task():
    assert m.detect_task(pd.Series([1.5, 2.6, 3.7])) == "regression"
    assert m.detect_task(pd.Series(["a", "b", "a"])) == "classification"
    assert m.detect_task(pd.Series([0, 1, 0, 1, 1])) == "classification"


def test_train_supervised_regression():
    df = make_regression_df()
    result = m.train_supervised(df, "y", ["x1", "x2"], seed=0)
    assert result["task"] == "regression"
    assert result["metrics"]["r2"] > 0.8
    assert len(result["predictions"]) == len(df)
    assert result["feature_importance"]


def test_train_supervised_classification_random_forest():
    df = make_classification_df()
    result = m.train_supervised(df, "label", ["x1", "x2"], algorithm="random_forest", seed=0)
    assert result["task"] == "classification"
    assert result["metrics"]["accuracy"] > 0.7
    assert "confusion_matrix" in result["metrics"]


def test_train_supervised_unknown_target_raises():
    df = make_regression_df()
    with pytest.raises(m.ModelError):
        m.train_supervised(df, "nope", ["x1"])


def test_train_supervised_auto_features_exclude_id_like_columns():
    """A near-unique text column (order id, customer id...) must not be
    auto-selected as a feature: one-hot encoding it would blow up the design
    matrix and stall training. An explicit feature list is still honored."""
    df = make_classification_df(n=300)
    df["order_id"] = [f"S{i:05d}" for i in range(len(df))]  # all-unique -> id-like
    result = m.train_supervised(df, "label", seed=0)  # features=None -> auto
    assert "order_id" not in result["features"]
    assert "order_id" in result["excluded_id_like_columns"]
    assert result["fit_seconds"] < 5

    # an explicit feature list is trusted even if it includes an id-like column
    result2 = m.train_supervised(df, "label", ["x1", "x2", "order_id"], seed=0)
    assert result2["excluded_id_like_columns"] == []


def test_run_kmeans():
    rng = np.random.default_rng(0)
    a = rng.normal(0, 0.3, (50, 2))
    b = rng.normal(5, 0.3, (50, 2))
    df = pd.DataFrame(np.vstack([a, b]), columns=["x", "y"])
    result = m.run_kmeans(df, ["x", "y"], k=2, seed=0)
    assert result["k"] == 2
    assert len(result["labels"]) == 100
    assert len(set(result["labels"])) == 2


def test_run_pca():
    df = make_regression_df(100)
    result = m.run_pca(df, ["x1", "x2", "y"], n_components=2)
    assert len(result["explained_variance_ratio"]) == 2
    assert sum(result["explained_variance_ratio"]) > 0.5
    assert len(result["projection"]) == 100


def test_run_anomaly():
    rng = np.random.default_rng(0)
    normal = rng.normal(0, 1, (95, 2))
    outliers = rng.normal(20, 1, (5, 2))
    df = pd.DataFrame(np.vstack([normal, outliers]), columns=["x", "y"])
    result = m.run_anomaly(df, ["x", "y"], contamination=0.05, seed=0)
    assert result["n_anomalies"] >= 1
    assert len(result["is_anomaly"]) == 100


def test_run_forecast_with_statsmodels():
    dates = pd.date_range("2023-01-01", periods=60, freq="D")
    values = 10 + 0.1 * np.arange(60) + 2 * np.sin(2 * np.pi * np.arange(60) / 7)
    df = pd.DataFrame({"d": dates, "v": values})
    result = m.run_forecast(df, "d", "v", horizon=7)
    assert len(result["forecast"]) == 7
    assert result["forecast"][0]["lower"] <= result["forecast"][0]["value"] <= result["forecast"][0]["upper"]


def test_seasonal_naive_fallback_used_for_short_series():
    dates = pd.date_range("2023-01-01", periods=6, freq="D")
    df = pd.DataFrame({"d": dates, "v": [1, 2, 3, 4, 5, 6]})
    result = m.run_forecast(df, "d", "v", horizon=3)
    assert result["method"].startswith("seasonal_naive")
    assert len(result["forecast"]) == 3


def test_run_forecast_aggregates_duplicate_dates_and_advances_them():
    """Row-level data (several rows per date, as in a transaction log) must be
    summed per date before forecasting: otherwise the median step between
    sorted-but-often-repeated timestamps collapses to zero and every forecast
    row lands on the same date (a real bug caught during the UI walk)."""
    rng = np.random.default_rng(0)
    n = 300
    dates = pd.to_datetime("2024-01-01") + pd.to_timedelta(rng.integers(0, 90, n), unit="D")
    values = rng.uniform(10, 100, n)
    df = pd.DataFrame({"d": dates, "v": values})
    result = m.run_forecast(df, "d", "v", horizon=5)
    forecast_dates = [row["date"] for row in result["forecast"]]
    assert len(set(forecast_dates)) == len(forecast_dates)  # every date distinct, strictly advancing
    assert forecast_dates == sorted(forecast_dates)
    assert result["resampled_to"] == "day"
    assert result["why"] is None


# ---- freq: resampling irregular data onto a regular grain ------------------


def _irregular_daily_df(rng, n_days=18 * 30, hit_rate=0.6, max_per_day=4):
    """~18 months of gappy daily transactions: only `hit_rate` of days have
    any rows at all, and the days that do have 1..max_per_day of them — the
    real shape reported from a live MCP walk that used to fall back to
    seasonal_naive instead of Holt-Winters."""
    start = pd.Timestamp("2023-01-01")
    rows = []
    for day_offset in range(n_days):
        if rng.random() < hit_rate:
            for _ in range(rng.integers(1, max_per_day)):
                rows.append({"d": start + pd.Timedelta(days=day_offset), "v": rng.uniform(50, 200)})
    return pd.DataFrame(rows)


def test_irregular_daily_data_auto_resamples_to_month_and_uses_holt_winters():
    rng = np.random.default_rng(3)
    df = _irregular_daily_df(rng)
    result = m.run_forecast(df, "d", "v", horizon=3, freq="auto")
    assert result["resampled_to"] == "month"
    assert result["seasonal_period"] == 12
    if m.HAVE_STATSMODELS:
        assert result["method"] == "holt_winters"
        assert result["why"] is None
    assert len(result["forecast"]) == 3


def test_freq_explicit_value_is_honored_even_when_auto_would_pick_differently():
    rng = np.random.default_rng(3)
    df = _irregular_daily_df(rng)
    result = m.run_forecast(df, "d", "v", horizon=2, freq="week")
    assert result["resampled_to"] == "week"
    assert result["freq"] == "week"


def test_freq_day_zero_fills_gaps_instead_of_dropping_them():
    df = pd.DataFrame({
        "d": pd.to_datetime(["2024-01-01", "2024-01-02", "2024-01-05"]),  # gaps on the 3rd and 4th
        "v": [10.0, 20.0, 40.0],
    })
    result = m.run_forecast(df, "d", "v", horizon=2, freq="day")
    history = {h["date"]: h["value"] for h in result["history"]}
    assert history["2024-01-03"] == 0.0
    assert history["2024-01-04"] == 0.0
    assert len(result["history"]) == 5  # 1st..5th, no gaps


def test_unknown_freq_raises():
    dates = pd.date_range("2023-01-01", periods=30, freq="D")
    df = pd.DataFrame({"d": dates, "v": range(30)})
    with pytest.raises(m.ModelError):
        m.run_forecast(df, "d", "v", freq="fortnight")


def test_forecast_reports_why_on_seasonal_naive_fallback():
    dates = pd.date_range("2023-01-01", periods=6, freq="D")
    df = pd.DataFrame({"d": dates, "v": [1, 2, 3, 4, 5, 6]})
    result = m.run_forecast(df, "d", "v", horizon=3)
    assert result["method"].startswith("seasonal_naive")
    assert result["why"]  # a human-readable reason is always given on fallback

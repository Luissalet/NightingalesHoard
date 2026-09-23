"""Model training through Services: writes results back as a new dataset version."""

import numpy as np
import pandas as pd
import pytest


@pytest.fixture
def numeric_ds(services, tmp_path):
    rng = np.random.default_rng(0)
    n = 150
    df = pd.DataFrame({
        "x1": rng.normal(0, 1, n),
        "x2": rng.normal(0, 1, n),
        "y": rng.normal(0, 1, n),
    })
    df["y"] = 2 * df["x1"] - df["x2"] + rng.normal(0, 0.1, n)
    path = tmp_path / "numeric.csv"
    df.to_csv(path, index=False)
    services.ingest_file(str(path), "numeric")
    return "numeric"


def test_model_train_writes_predictions(services, numeric_ds):
    result = services.model_train(numeric_ds, "y", ["x1", "x2"], seed=0)
    assert result["metrics"]["r2"] > 0.7
    assert "prediction_dataset_version" in result
    preview = services.preview(numeric_ds, limit=5)
    assert "predicted_y" in [c["name"] for c in preview["columns"]]
    assert services.model_list(numeric_ds)["models"]


def test_model_cluster_writes_labels(services, numeric_ds):
    result = services.model_cluster(numeric_ds, ["x1", "x2"], k=3)
    assert result["k"] == 3
    preview = services.preview(numeric_ds, limit=5)
    assert "cluster" in [c["name"] for c in preview["columns"]]


def test_model_anomaly_writes_flags(services, numeric_ds):
    result = services.model_anomaly(numeric_ds, ["x1", "x2"])
    preview = services.preview(numeric_ds, limit=5)
    names = [c["name"] for c in preview["columns"]]
    assert "is_anomaly" in names and "anomaly_score" in names


def test_model_pca(services, numeric_ds):
    result = services.model_pca(numeric_ds, ["x1", "x2", "y"], n_components=2)
    assert len(result["explained_variance_ratio"]) == 2


def test_model_forecast_creates_forecast_dataset(services, tmp_path):
    import numpy as np
    dates = pd.date_range("2023-01-01", periods=60, freq="D")
    values = 10 + 0.1 * np.arange(60)
    df = pd.DataFrame({"d": dates.astype(str), "v": values})
    path = tmp_path / "ts.csv"
    df.to_csv(path, index=False)
    services.ingest_file(str(path), "ts")
    result = services.model_forecast("ts", "d", "v", horizon=5)
    assert len(result["forecast"]) == 5
    assert "forecast_dataset" in result
    fc_preview = services.preview(result["forecast_dataset"], limit=10)
    assert fc_preview["row_count_total"] == 5

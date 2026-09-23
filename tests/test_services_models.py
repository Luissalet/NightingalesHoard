"""Model training through Services: by default, results land in a new,
separate dataset (source untouched); `write_to="new_version"` is an explicit
opt-in that must never change an existing column's type."""

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


def test_model_train_default_writes_separate_dataset(services, numeric_ds):
    result = services.model_train(numeric_ds, "y", ["x1", "x2"], seed=0)
    assert result["metrics"]["r2"] > 0.7
    assert result["write_to"] == "new_dataset"
    assert result["dataset"] == f"numeric__model_{result['model_id']}"
    # source dataset is untouched: no predicted_y column, still version 0
    source = services.dataset_summary(services._dataset_row(numeric_ds))
    assert source["current_version"] == 0
    assert "predicted_y" not in [c["name"] for c in source["columns"]]
    # the new dataset holds inputs + prediction
    preview = services.preview(result["dataset"], limit=5)
    names = [c["name"] for c in preview["columns"]]
    assert "predicted_y" in names and "x1" in names and "y" in names
    assert services.model_list(numeric_ds)["models"]


def test_model_train_new_version_preserves_types(services, tmp_path):
    df = pd.DataFrame({
        "fecha": pd.date_range("2024-01-01", periods=40, freq="D").date,
        "importe": [round(x, 2) for x in np.random.default_rng(1).uniform(10, 1000, 40)],
        "target": np.random.default_rng(1).normal(0, 1, 40),
    })
    path = tmp_path / "sales.csv"
    df.to_csv(path, index=False)
    services.ingest_file(str(path), "sales")
    # cast fecha to DATE and importe to DECIMAL, as a cleaned dataset would have
    services.transform_apply("sales", "cast", {"column": "fecha", "to": "date"})
    services.transform_apply("sales", "cast", {"column": "importe", "to": "decimal"})
    before = {c["name"]: c["type"] for c in services.dataset_summary(services._dataset_row("sales"))["columns"]}
    assert before["fecha"] == "DATE"
    assert before["importe"].startswith("DECIMAL")

    result = services.model_train("sales", "target", ["importe"], seed=0, write_to="new_version")
    assert result["write_to"] == "new_version"
    after_row = services._dataset_row("sales")
    assert after_row["current_version"] == result["version"]
    after = {c["name"]: c["type"] for c in services.dataset_summary(after_row)["columns"]}
    assert after["fecha"] == before["fecha"], "DATE must not become TIMESTAMP"
    assert after["importe"] == before["importe"], "DECIMAL must not become DOUBLE"
    assert "predicted_target" in after


def test_model_train_write_to_none(services, numeric_ds):
    before = services.list_datasets()["datasets"]
    result = services.model_train(numeric_ds, "y", ["x1", "x2"], seed=0, write_to="none")
    assert result["write_to"] == "none"
    assert services.list_datasets()["datasets"] == before


def test_model_cluster_default_writes_separate_dataset(services, numeric_ds):
    result = services.model_cluster(numeric_ds, ["x1", "x2"], k=3)
    assert result["k"] == 3
    assert result["write_to"] == "new_dataset"
    preview = services.preview(result["dataset"], limit=5)
    assert "cluster" in [c["name"] for c in preview["columns"]]
    source = services.dataset_summary(services._dataset_row(numeric_ds))
    assert "cluster" not in [c["name"] for c in source["columns"]]


def test_model_anomaly_default_writes_separate_dataset(services, numeric_ds):
    result = services.model_anomaly(numeric_ds, ["x1", "x2"])
    assert result["write_to"] == "new_dataset"
    preview = services.preview(result["dataset"], limit=5)
    names = [c["name"] for c in preview["columns"]]
    assert "is_anomaly" in names and "anomaly_score" in names
    source = services.dataset_summary(services._dataset_row(numeric_ds))
    assert "is_anomaly" not in [c["name"] for c in source["columns"]]


def test_model_pca(services, numeric_ds):
    result = services.model_pca(numeric_ds, ["x1", "x2", "y"], n_components=2)
    assert len(result["explained_variance_ratio"]) == 2


def test_model_forecast_creates_forecast_dataset(services, tmp_path):
    dates = pd.date_range("2023-01-01", periods=60, freq="D")
    values = 10 + 0.1 * np.arange(60)
    df = pd.DataFrame({"d": dates.astype(str), "v": values})
    path = tmp_path / "ts.csv"
    df.to_csv(path, index=False)
    services.ingest_file(str(path), "ts")
    result = services.model_forecast("ts", "d", "v", horizon=5)
    assert len(result["forecast"]) == 5
    assert result["forecast_dataset"] == f"ts__forecast_{result['model_id']}"
    fc_preview = services.preview(result["forecast_dataset"], limit=10)
    assert fc_preview["row_count_total"] == 5


def test_model_forecast_write_to_none(services, tmp_path):
    dates = pd.date_range("2023-01-01", periods=60, freq="D")
    values = 10 + 0.1 * np.arange(60)
    df = pd.DataFrame({"d": dates.astype(str), "v": values})
    path = tmp_path / "ts2.csv"
    df.to_csv(path, index=False)
    services.ingest_file(str(path), "ts2")
    before = services.list_datasets()["datasets"]
    result = services.model_forecast("ts2", "d", "v", horizon=5, write_to="none")
    assert "forecast_dataset" not in result
    assert services.list_datasets()["datasets"] == before


def test_model_forecast_rejects_new_version(services, tmp_path):
    dates = pd.date_range("2023-01-01", periods=60, freq="D")
    values = 10 + 0.1 * np.arange(60)
    df = pd.DataFrame({"d": dates.astype(str), "v": values})
    path = tmp_path / "ts3.csv"
    df.to_csv(path, index=False)
    services.ingest_file(str(path), "ts3")
    with pytest.raises(ValueError):
        services.model_forecast("ts3", "d", "v", horizon=5, write_to="new_version")

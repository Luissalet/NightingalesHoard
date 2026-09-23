"""Lab reached through the agent/MCP surface: data_profile's new `mode`s and
data_model's new `action`s, all via the same 18-tool catalogue (no 19th tool)."""

import asyncio

import numpy as np
import pandas as pd
import pytest

from nightingale.agent_tools import call_tool


def run(coro):
    return asyncio.run(coro)


@pytest.fixture
def reg_ds(services, tmp_path):
    rng = np.random.default_rng(0)
    n = 200
    df = pd.DataFrame({"x1": rng.normal(0, 1, n), "x2": rng.normal(0, 1, n)})
    df["y"] = 2 * df["x1"] - df["x2"] + rng.normal(0, 0.1, n)
    path = tmp_path / "reg.csv"
    df.to_csv(path, index=False)
    services.ingest_file(str(path), "reg")
    return "reg"


def test_data_profile_mode_eda_and_quality_score(services, reg_ds):
    result = run(call_tool(services, "data_profile", {"dataset": reg_ds, "mode": "eda"}))
    assert "quality_score" in result

    result = run(call_tool(services, "data_profile", {"dataset": reg_ds, "mode": "quality_score"}))
    assert "score" in result


def test_data_profile_mode_drift(services, reg_ds, tmp_path):
    rng = np.random.default_rng(1)
    shifted = pd.DataFrame({"x1": rng.normal(5, 1, 100), "x2": rng.normal(0, 1, 100)})
    path = tmp_path / "shifted.csv"
    shifted.to_csv(path, index=False)
    services.ingest_file(str(path), "shifted")
    result = run(call_tool(services, "data_profile",
                            {"dataset": reg_ds, "mode": "drift", "other_dataset": "shifted"}))
    assert result["n_flagged"] >= 1


def test_data_profile_mode_drift_needs_other_dataset(services, reg_ds):
    with pytest.raises(ValueError):
        run(call_tool(services, "data_profile", {"dataset": reg_ds, "mode": "drift"}))


def test_data_model_action_backends(services):
    result = run(call_tool(services, "data_model", {"action": "backends", "task": "regression"}))
    assert any(b["name"] == "random_forest" for b in result["regression"])


def test_data_model_train_lab_true_vs_original_path(services, reg_ds):
    # lab=false (default): the original, unpersisted path
    original = run(call_tool(services, "data_model", {"action": "train", "dataset": reg_ds, "target": "y",
                                                        "features": ["x1", "x2"], "seed": 0}))
    assert "model_id" in original
    listed = run(call_tool(services, "data_model", {"action": "registry"}))
    assert not any(m["id"] == original["model_id"] for m in listed["models"])

    # lab=true: goes through the registry, with a persisted artifact
    lab_result = run(call_tool(services, "data_model", {"action": "train", "dataset": reg_ds, "target": "y",
                                                          "features": ["x1", "x2"], "algorithm": "random_forest",
                                                          "lab": True, "seed": 0}))
    listed = run(call_tool(services, "data_model", {"action": "registry"}))
    assert any(m["id"] == lab_result["model_id"] for m in listed["models"])
    return lab_result["model_id"]


def test_data_model_full_lab_flow(services, reg_ds):
    trained = run(call_tool(services, "data_model", {"action": "train", "dataset": reg_ds, "target": "y",
                                                       "features": ["x1", "x2"], "algorithm": "random_forest",
                                                       "lab": True, "seed": 0}))
    model_id = trained["model_id"]

    got = run(call_tool(services, "data_model", {"action": "registry", "registry_action": "get",
                                                   "model_id": model_id}))
    assert got["backend"] == "random_forest"

    evaluated = run(call_tool(services, "data_model", {"action": "evaluate", "model_id": model_id}))
    assert evaluated["metrics"]["r2"] > 0.5

    explained = run(call_tool(services, "data_model", {"action": "explain", "model_id": model_id,
                                                          "params": {"sample_size": 50}}))
    assert explained["global_importance"]

    optimized = run(call_tool(services, "data_model", {
        "action": "optimize", "model_id": model_id,
        "params": {"direction": "maximize", "n_candidates": 300, "batch_size": 2},
    }))
    assert len(optimized["suggested_points"]) == 2

    tuned = run(call_tool(services, "data_model", {"action": "tune", "dataset": reg_ds, "target": "y",
                                                     "features": ["x1", "x2"], "algorithm": "random_forest",
                                                     "params": {"n_trials": 3}, "seed": 0}))
    assert tuned["tuning"]["n_trials"] == 3

    compared = run(call_tool(services, "data_model", {"action": "compare",
                                                         "model_ids": [model_id, tuned["model_id"]]}))
    assert len(compared["models"]) == 2

    curve = run(call_tool(services, "data_model", {"action": "compare", "dataset": reg_ds, "x": "x1", "y": "y"}))
    assert "series" in curve

    report = run(call_tool(services, "data_model", {"action": "report", "dataset": reg_ds,
                                                       "model_id": model_id}))
    assert report["path"].endswith(".pdf")

    deleted = run(call_tool(services, "data_model", {"action": "registry", "registry_action": "delete",
                                                        "model_id": model_id}))
    assert deleted["ok"] is True


def test_data_model_pareto_action(services, reg_ds):
    m1 = run(call_tool(services, "data_model", {"action": "train", "dataset": reg_ds, "target": "y",
                                                  "features": ["x1", "x2"], "algorithm": "random_forest",
                                                  "lab": True, "seed": 0, "name": "m1"}))
    m2 = run(call_tool(services, "data_model", {"action": "train", "dataset": reg_ds, "target": "x1",
                                                  "features": ["x2"], "algorithm": "random_forest",
                                                  "lab": True, "seed": 0, "name": "m2"}))
    result = run(call_tool(services, "data_model", {
        "action": "pareto", "model_ids": [m1["model_id"], m2["model_id"]],
        "params": {"directions": ["maximize", "minimize"], "n_candidates": 200},
    }))
    assert result["n_front"] >= 1


def test_data_model_pareto_needs_directions(services, reg_ds):
    m1 = run(call_tool(services, "data_model", {"action": "train", "dataset": reg_ds, "target": "y",
                                                  "features": ["x1", "x2"], "algorithm": "random_forest",
                                                  "lab": True, "seed": 0}))
    with pytest.raises(ValueError):
        run(call_tool(services, "data_model", {"action": "pareto", "model_ids": [m1["model_id"]]}))

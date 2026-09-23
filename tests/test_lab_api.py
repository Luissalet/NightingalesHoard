"""/api/lab/* REST surface, exercised end-to-end through the FastAPI TestClient."""

import numpy as np
import pandas as pd
import pytest


@pytest.fixture
def reg_ds(client, tmp_path):
    rng = np.random.default_rng(0)
    n = 200
    df = pd.DataFrame({"x1": rng.normal(0, 1, n), "x2": rng.normal(0, 1, n)})
    df["y"] = 2 * df["x1"] - df["x2"] + rng.normal(0, 0.1, n)
    path = tmp_path / "reg.csv"
    df.to_csv(path, index=False)
    client.post("/api/sources/ingest", json={"kind": "file", "path": str(path), "name": "reg"})
    return "reg"


def test_eda_and_quality_score_routes(client, reg_ds):
    r = client.get(f"/api/lab/datasets/{reg_ds}/eda")
    assert r.status_code == 200
    assert "quality_score" in r.json()

    r = client.get(f"/api/lab/datasets/{reg_ds}/quality-score")
    assert r.status_code == 200
    assert "score" in r.json()


def test_drift_route(client, reg_ds, tmp_path):
    rng = np.random.default_rng(3)
    shifted = pd.DataFrame({"x1": rng.normal(5, 1, 100), "x2": rng.normal(0, 1, 100)})
    path = tmp_path / "shifted.csv"
    shifted.to_csv(path, index=False)
    client.post("/api/sources/ingest", json={"kind": "file", "path": str(path), "name": "shifted"})
    r = client.post("/api/lab/drift", json={"dataset": reg_ds, "other_dataset": "shifted"})
    assert r.status_code == 200
    assert r.json()["n_flagged"] >= 1


def test_backends_route(client):
    r = client.get("/api/lab/backends?task=regression")
    assert r.status_code == 200
    assert any(b["name"] == "random_forest" for b in r.json()["regression"])


def test_train_evaluate_explain_optimize_flow(client, reg_ds):
    r = client.post("/api/lab/models/train", json={"dataset": reg_ds, "target": "y", "features": ["x1", "x2"],
                                                      "backend": "random_forest", "seed": 0})
    assert r.status_code == 200
    model_id = r.json()["model_id"]

    r = client.get("/api/lab/models", params={"dataset": reg_ds})
    assert r.status_code == 200
    listed = next(m for m in r.json()["models"] if m["id"] == model_id)
    assert listed["dataset_version"] == 0

    r = client.get(f"/api/lab/models/{model_id}")
    assert r.status_code == 200
    assert r.json()["backend"] == "random_forest"

    r = client.post(f"/api/lab/models/{model_id}/evaluate", json={})
    assert r.status_code == 200
    assert r.json()["metrics"]["r2"] > 0.5

    r = client.post(f"/api/lab/models/{model_id}/explain", json={"sample_size": 50})
    assert r.status_code == 200
    assert r.json()["global_importance"]

    r = client.post(f"/api/lab/models/{model_id}/optimize", json={"direction": "maximize", "n_candidates": 400,
                                                                     "batch_size": 2})
    assert r.status_code == 200
    assert len(r.json()["suggested_points"]) == 2

    r = client.delete(f"/api/lab/models/{model_id}")
    assert r.status_code == 200
    r = client.get(f"/api/lab/models/{model_id}")
    assert r.status_code == 404


def test_tune_route(client, reg_ds):
    r = client.post("/api/lab/models/tune", json={"dataset": reg_ds, "target": "y", "features": ["x1", "x2"],
                                                     "backend": "random_forest", "n_trials": 3, "seed": 0})
    assert r.status_code == 200
    assert r.json()["tuning"]["n_trials"] == 3


def test_pareto_route(client, reg_ds):
    r1 = client.post("/api/lab/models/train", json={"dataset": reg_ds, "target": "y", "features": ["x1", "x2"],
                                                       "backend": "random_forest", "seed": 0, "name": "m1"})
    r2 = client.post("/api/lab/models/train", json={"dataset": reg_ds, "target": "x1", "features": ["x2"],
                                                       "backend": "random_forest", "seed": 0, "name": "m2"})
    ids = [r1.json()["model_id"], r2.json()["model_id"]]
    r = client.post("/api/lab/pareto", json={"model_ids": ids, "directions": ["maximize", "minimize"],
                                               "n_candidates": 200})
    assert r.status_code == 200
    assert r.json()["n_front"] >= 1

    r = client.post("/api/lab/models/compare", json={"model_ids": ids})
    assert r.status_code == 200
    assert len(r.json()["models"]) == 2


def test_compare_curves_route(client, reg_ds):
    r = client.post("/api/lab/compare-curves", json={"dataset": reg_ds, "x": "x1", "y": "y"})
    assert r.status_code == 200
    assert "series" in r.json()


def test_report_route(client, reg_ds):
    r = client.post("/api/lab/models/train", json={"dataset": reg_ds, "target": "y", "features": ["x1", "x2"],
                                                      "backend": "random_forest", "seed": 0})
    model_id = r.json()["model_id"]
    r = client.post("/api/lab/report", json={"dataset": reg_ds, "model_id": model_id})
    assert r.status_code == 200
    path = r.json()["path"]
    assert path.endswith(".pdf")

    r = client.get("/api/lab/report/download", params={"path": path})
    assert r.status_code == 200
    assert r.headers["content-type"] == "application/pdf"

    r = client.get("/api/lab/report/download", params={"path": "/etc/passwd"})
    assert r.status_code == 404


def test_pipeline_graph_and_apply_routes(client, reg_ds):
    r = client.get(f"/api/lab/datasets/{reg_ds}/pipeline")
    assert r.status_code == 200
    graph = r.json()

    edited = {
        "nodes": [{"id": "v0", "kind": "source", "params": {}},
                  {"id": "step_1", "kind": "filter", "params": {"expr": "x1 > 0"}}],
        "edges": [["v0", "step_1"]],
    }
    r = client.post(f"/api/lab/datasets/{reg_ds}/pipeline/apply", json={"graph": edited, "dry_run": True})
    assert r.status_code == 200
    assert r.json()["dry_run"] is True
    assert isinstance(r.json()["preview_rows"], list)
    assert len(r.json()["preview_rows"]) > 0

    r = client.post(f"/api/lab/datasets/{reg_ds}/pipeline/apply", json={"graph": edited, "dry_run": False})
    assert r.status_code == 200
    assert r.json()["current_version"] == 1


def test_bad_request_returns_400(client, reg_ds):
    r = client.post("/api/lab/drift", json={"dataset": reg_ds, "other_dataset": "does-not-exist"})
    assert r.status_code == 404 or r.status_code == 400

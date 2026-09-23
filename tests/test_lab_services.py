"""Lab wiring through Services: every Lab feature reachable the same way the
rest of the app is — ingest a dataset, call a Services method, check the
analysis log got an entry, and that the dataset invariants (source untouched,
its column types unchanged, versions kept) still hold."""

import numpy as np
import pandas as pd
import pytest


@pytest.fixture
def reg_ds(services, tmp_path):
    rng = np.random.default_rng(0)
    n = 250
    df = pd.DataFrame({
        "x1": rng.normal(0, 1, n), "x2": rng.normal(0, 1, n),
        "region": rng.choice(["north", "south"], n),
        "date": pd.date_range("2023-01-01", periods=n, freq="D"),
    })
    df["y"] = 2 * df["x1"] - df["x2"] + rng.normal(0, 0.1, n)
    path = tmp_path / "reg.csv"
    df.to_csv(path, index=False)
    services.ingest_file(str(path), "reg")
    return "reg"


def test_lab_eda_profile_logs_and_reports_quality(services, reg_ds):
    result = services.lab_eda_profile(reg_ds)
    assert "quality_score" in result
    assert result["log_id"].startswith("N-")
    log = services.log_search(dataset=reg_ds)["entries"]
    assert any(e["op"] == "lab_eda" for e in log)


def test_lab_quality_score(services, reg_ds):
    result = services.lab_quality_score(reg_ds)
    assert 0 <= result["score"] <= 100


def test_lab_drift_between_two_datasets(services, reg_ds, tmp_path):
    rng = np.random.default_rng(9)
    shifted = pd.DataFrame({"x1": rng.normal(5, 1, 200), "x2": rng.normal(0, 1, 200)})
    path = tmp_path / "shifted.csv"
    shifted.to_csv(path, index=False)
    services.ingest_file(str(path), "shifted")
    result = services.lab_drift(reg_ds, "shifted", columns=["x1", "x2"])
    by_col = {c["column"]: c for c in result["columns"]}
    assert by_col["x1"]["flagged"] is True


def test_lab_compare_curves(services, reg_ds):
    result = services.lab_compare_curves(reg_ds, "date", "y", group="region")
    assert set(result["groups"]) == {"north", "south"}


def test_lab_backends_list(services):
    result = services.lab_backends("regression")
    assert any(b["name"] == "random_forest" for b in result["regression"])


def test_lab_model_train_persists_artifact_and_writes_new_dataset(services, reg_ds):
    result = services.lab_model_train(reg_ds, "y", ["x1", "x2"], backend_name="random_forest", seed=0)
    assert result["metrics"]["r2"] > 0.7
    assert result["write_to"] == "new_dataset"
    model_id = result["model_id"]
    # source untouched
    source = services.dataset_summary(services._dataset_row(reg_ds))
    assert source["current_version"] == 0
    # registered with an artifact, loadable for further Lab actions
    row = services.lab_registry_get(model_id)
    assert row["has_artifact"] is True
    assert row["backend"] == "random_forest"


def test_lab_registry_list_filters_to_lab_models(services, reg_ds):
    services.model_train(reg_ds, "y", ["x1", "x2"], seed=0)  # original path: NOT tagged lab
    lab_result = services.lab_model_train(reg_ds, "y", ["x1", "x2"], backend_name="ridge", seed=0)
    listed = services.lab_registry_list(reg_ds)["models"]
    assert all(m["params"]["lab"] for m in listed)
    assert any(m["id"] == lab_result["model_id"] for m in listed)


def test_lab_model_evaluate_and_explain(services, reg_ds):
    trained = services.lab_model_train(reg_ds, "y", ["x1", "x2"], backend_name="random_forest", seed=0)
    model_id = trained["model_id"]
    evaluation = services.lab_model_evaluate(model_id, date_col="date", group_col="region")
    assert evaluation["metrics"]["r2"] > 0.5
    assert evaluation["note"].startswith("no eval_dataset")
    explanation = services.lab_model_explain(model_id, sample_size=50, row_index=0)
    assert explanation["global_importance"]
    assert "row_explanation" in explanation


def test_lab_model_tune_persists_a_model(services, reg_ds):
    result = services.lab_model_tune(reg_ds, "y", ["x1", "x2"], backend_name="random_forest", n_trials=4, seed=0)
    assert result["tuning"]["n_trials"] == 4
    row = services.lab_registry_get(result["model_id"])
    assert row["params"]["tuned"] is True


def test_lab_model_optimize_writes_dataset_when_asked(services, reg_ds):
    trained = services.lab_model_train(reg_ds, "y", ["x1", "x2"], backend_name="random_forest", seed=0)
    result = services.lab_model_optimize(trained["model_id"], direction="maximize", n_candidates=500,
                                          batch_size=3, write_to="new_dataset")
    assert result["write_to"] == "new_dataset"
    preview = services.preview(result["optimize_dataset"], limit=10)
    assert preview["row_count_total"] == 3


def test_lab_pareto_across_two_models(services, reg_ds):
    m1 = services.lab_model_train(reg_ds, "y", ["x1", "x2"], backend_name="random_forest", seed=0, name="m1")
    m2 = services.lab_model_train(reg_ds, "x1", ["x2"], backend_name="random_forest", seed=0, name="m2")
    result = services.lab_pareto([m1["model_id"], m2["model_id"]], ["maximize", "minimize"], n_candidates=300)
    assert result["n_front"] >= 1


def test_lab_registry_compare_and_delete(services, reg_ds):
    m1 = services.lab_model_train(reg_ds, "y", ["x1", "x2"], backend_name="ridge", seed=0)
    m2 = services.lab_model_train(reg_ds, "y", ["x1", "x2"], backend_name="random_forest", seed=0)
    compared = services.lab_registry_compare([m1["model_id"], m2["model_id"]])
    assert len(compared["models"]) == 2
    services.lab_registry_delete(m1["model_id"])
    with pytest.raises(LookupError):
        services.lab_registry_get(m1["model_id"])


def test_lab_report_writes_a_pdf(services, reg_ds):
    trained = services.lab_model_train(reg_ds, "y", ["x1", "x2"], backend_name="random_forest", seed=0)
    result = services.lab_report(reg_ds, model_id=trained["model_id"])
    from pathlib import Path
    assert Path(result["path"]).exists()


def test_lab_pipeline_graph_and_apply_keeps_versions(services, reg_ds):
    graph = services.lab_pipeline_graph(reg_ds)
    assert graph["nodes"][0]["kind"] == "source"

    new_graph = {
        "nodes": [{"id": "v0", "kind": "source", "params": {}},
                  {"id": "step_1", "kind": "filter", "params": {"expr": "x1 > 0"}},
                  {"id": "step_2", "kind": "sort", "params": {"by": [{"column": "x1"}]}}],
        "edges": [["v0", "step_1"], ["step_1", "step_2"]],
    }
    preview = services.lab_pipeline_apply(reg_ds, new_graph, dry_run=True)
    assert preview["dry_run"] is True
    assert preview["row_count"] < 250

    # dataset unaffected by dry_run
    assert services.dataset_summary(services._dataset_row(reg_ds))["current_version"] == 0

    applied = services.lab_pipeline_apply(reg_ds, new_graph, dry_run=False)
    assert applied["current_version"] == 2
    assert applied["version_count"] == 3  # v0 + the two applied steps
    assert applied["row_count"] == preview["row_count"]

    # the graph now reflects what was actually applied
    graph_after = services.lab_pipeline_graph(reg_ds)
    kinds = [n["kind"] for n in graph_after["nodes"]]
    assert kinds == ["source", "filter", "sort"]

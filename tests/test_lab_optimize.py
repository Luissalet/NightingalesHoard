"""Optimization: Bayesian optimization (GP surrogate + EI/UCB) over a trained
model's inputs, and multi-objective Pareto-front search (non-dominated
sorting)."""

import numpy as np
import pandas as pd
import pytest

from nightingale.lab import LabError, optimize, registry


def _saved_gp_on_known_max(seed=0):
    """A model whose response surface has one, exactly-known maximum: a
    negative paraboloid peaking at (x1, x2) = (3, -2), value 10. Trained with
    a Gaussian Process so the model itself is smooth and near-exact within
    the sampled range (the hardest, most literal case for "find the known
    max within tolerance")."""
    rng = np.random.default_rng(seed)
    n = 120
    x1 = rng.uniform(-10, 10, n)
    x2 = rng.uniform(-10, 10, n)
    y = 10 - 0.1 * (x1 - 3) ** 2 - 0.1 * (x2 + 2) ** 2
    df = pd.DataFrame({"x1": x1, "x2": x2, "y": y})
    result = registry.train_model(df, "y", ["x1", "x2"], "regression", "gaussian_process", seed=seed)
    saved = registry.SavedModel(model=result["_model"], encoders=result["_encoders"],
                                 label_encoder=result["_label_encoder"], X_columns=result["_X_columns"],
                                 features=result["features"], target="y", task="regression",
                                 backend="gaussian_process")
    return saved, df


def test_optimize_finds_known_maximum_within_tolerance():
    saved, df = _saved_gp_on_known_max()
    result = optimize.optimize(saved, df, direction="maximize", n_candidates=4000, batch_size=5, seed=0)
    best = result["refined_best"] or result["best_in_search"]
    assert abs(best["inputs"]["x1"] - 3) < 1.0
    assert abs(best["inputs"]["x2"] - (-2)) < 1.0
    assert best["predicted_value"] > 9.0  # true max is 10
    assert len(result["suggested_points"]) == 5
    for s in result["suggested_points"]:
        assert s["uncertainty"] is not None


def test_optimize_non_gp_backend_stays_fast_at_default_candidate_count():
    """Regression test: `_predict_with_std`'s GP surrogate must never be fit on
    the full candidate pool (O(n^3) in the training set size), or a plain
    `/api/lab/models/{id}/optimize` call at its own default `n_candidates`
    (3000) on any non-`gaussian_process` backend hangs for minutes. This uses
    `random_forest` (a backend that needs the surrogate path) at that same
    default candidate count and enforces a generous wall-clock budget."""
    import time

    rng = np.random.default_rng(0)
    n = 200
    x1 = rng.uniform(-10, 10, n)
    x2 = rng.uniform(-10, 10, n)
    y = 10 - 0.1 * (x1 - 3) ** 2 - 0.1 * (x2 + 2) ** 2
    df = pd.DataFrame({"x1": x1, "x2": x2, "y": y})
    result = registry.train_model(df, "y", ["x1", "x2"], "regression", "random_forest", seed=0)
    saved = registry.SavedModel(model=result["_model"], encoders=result["_encoders"],
                                 label_encoder=result["_label_encoder"], X_columns=result["_X_columns"],
                                 features=result["features"], target="y", task="regression", backend="random_forest")

    start = time.monotonic()
    out = optimize.optimize(saved, df, direction="maximize", n_candidates=3000, batch_size=5, seed=0)
    elapsed = time.monotonic() - start
    assert elapsed < 15.0, f"optimize() took {elapsed:.1f}s at the default candidate count — the GP surrogate is refitting on the whole pool again"
    assert len(out["suggested_points"]) == 5


def test_optimize_minimize_direction():
    saved, df = _saved_gp_on_known_max()
    result = optimize.optimize(saved, df, direction="minimize", n_candidates=1500, batch_size=3, seed=0)
    # the minimum over the observed range is at a far corner, not the peak
    best = result["best_in_search"]
    assert best["predicted_value"] < 5.0


def test_optimize_respects_fixed_and_bounds():
    saved, df = _saved_gp_on_known_max()
    result = optimize.optimize(saved, df, direction="maximize", fixed={"x1": 0.0},
                                bounds={"x2": (-1.0, 1.0)}, n_candidates=500, batch_size=2, seed=0)
    for s in result["suggested_points"]:
        assert s["inputs"]["x1"] == 0.0
        assert -1.0 <= s["inputs"]["x2"] <= 1.0


def test_optimize_constraints_filter_candidates():
    saved, df = _saved_gp_on_known_max()
    result = optimize.optimize(saved, df, direction="maximize",
                                constraints=[{"coeffs": {"x1": 1, "x2": 1}, "le": 0}],
                                n_candidates=2000, batch_size=3, seed=0, refine=False)
    for s in result["suggested_points"]:
        assert s["inputs"]["x1"] + s["inputs"]["x2"] <= 1e-6


def test_optimize_impossible_constraints_raise():
    saved, df = _saved_gp_on_known_max()
    with pytest.raises(LabError):
        optimize.optimize(saved, df, constraints=[{"coeffs": {"x1": 1}, "le": -1000}], n_candidates=200)


def test_optimize_integer_features_are_rounded():
    saved, df = _saved_gp_on_known_max()
    result = optimize.optimize(saved, df, direction="maximize", integer_features=["x1"],
                                n_candidates=500, batch_size=3, seed=0, refine=False)
    for s in result["suggested_points"]:
        assert float(s["inputs"]["x1"]).is_integer()


def test_optimize_unknown_direction_raises():
    saved, df = _saved_gp_on_known_max()
    with pytest.raises(LabError):
        optimize.optimize(saved, df, direction="sideways")


def test_pareto_front_is_non_dominated():
    rng = np.random.default_rng(0)
    n = 200
    x = rng.uniform(0, 10, n)
    # two competing objectives over the same input: one increasing, one decreasing
    y_a = x + rng.normal(0, 0.1, n)
    y_b = (10 - x) + rng.normal(0, 0.1, n)
    df = pd.DataFrame({"x": x, "y_a": y_a, "y_b": y_b})
    model_a = registry.train_model(df, "y_a", ["x"], "regression", "random_forest", seed=0)
    model_b = registry.train_model(df, "y_b", ["x"], "regression", "random_forest", seed=0)
    saved_a = registry.SavedModel(model=model_a["_model"], encoders={}, label_encoder=None,
                                   X_columns=model_a["_X_columns"], features=["x"], target="y_a",
                                   task="regression", backend="random_forest")
    saved_b = registry.SavedModel(model=model_b["_model"], encoders={}, label_encoder=None,
                                   X_columns=model_b["_X_columns"], features=["x"], target="y_b",
                                   task="regression", backend="random_forest")
    result = optimize.pareto_front([saved_a, saved_b], df, ["maximize", "maximize"], n_candidates=300, seed=0)
    assert result["n_front"] >= 2
    front = result["front"]
    objectives = np.array([p["objectives"] for p in front])
    # verify non-domination directly: no front point is beaten-or-equal in both objectives by another
    for i in range(len(objectives)):
        for j in range(len(objectives)):
            if i == j:
                continue
            dominates = np.all(objectives[j] >= objectives[i]) and np.any(objectives[j] > objectives[i])
            assert not dominates, f"point {i} is dominated by point {j}"


def test_pareto_front_needs_two_or_three_models():
    saved, df = _saved_gp_on_known_max()
    with pytest.raises(LabError):
        optimize.pareto_front([saved], df, ["maximize"])


def test_pareto_front_directions_length_must_match():
    saved, df = _saved_gp_on_known_max()
    with pytest.raises(LabError):
        optimize.pareto_front([saved, saved], df, ["maximize"])


def test_predict_with_std_surrogate_gp_is_seeded(monkeypatch):
    """`_predict_with_std`'s fallback GP surrogate must be seeded from the
    caller's `seed`: with n_restarts_optimizer=1 sklearn still draws an extra
    hyperparameter restart from its own RNG, which defaults to the unseeded
    global numpy state when `random_state` isn't passed -- so two `optimize()`
    calls with the same seed could silently converge the surrogate to
    different kernel hyperparameters (and so different reported uncertainty)
    even though every other input was identical."""
    import sklearn.gaussian_process as gp_module

    captured = {}
    real_init = gp_module.GaussianProcessRegressor.__init__

    def _spy_init(self, **kwargs):
        captured["random_state"] = kwargs.get("random_state")
        real_init(self, **kwargs)

    monkeypatch.setattr(gp_module.GaussianProcessRegressor, "__init__", _spy_init)

    df = pd.DataFrame({"x1": np.linspace(0, 10, 60), "x2": np.linspace(-5, 5, 60)})
    df["y"] = df["x1"] * 2 - df["x2"]
    result = registry.train_model(df, "y", ["x1", "x2"], "regression", "random_forest", seed=0)
    saved = registry.SavedModel(model=result["_model"], encoders=result["_encoders"],
                                 label_encoder=result["_label_encoder"], X_columns=result["_X_columns"],
                                 features=result["features"], target="y", task="regression", backend="random_forest")

    optimize._predict_with_std(saved, df[["x1", "x2"]], seed=123)
    assert captured["random_state"] == 123

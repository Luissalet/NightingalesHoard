# Lab

Lab is a set of deeper data-science features built on top of the workbench: exploratory data
analysis and a quality score, a pluggable model registry with many more backends than the original
`data_model`, model diagnostics, hyperparameter tuning, explanations, Bayesian optimization and
Pareto fronts, drift/curve comparison, PDF reports, and a graph API for the UI's visual pipeline
editor.

Everything here is pure Python (`nightingale/lab/*.py`) — `pandas`/`numpy`/`scipy`/`scikit-learn`,
with `optuna`/`shap`/`xgboost`/`lightgbm`/`joblib` as **optional** extras (`requirements-lab.txt`).
Every feature that would use an optional package degrades to a documented fallback instead of
failing when it isn't installed. Nothing in Lab mutates a source dataset's existing columns; model
outputs and optimization/Pareto results go to new datasets, exactly like the original `data_model`/
`data_cluster`/`data_forecast`.

Reached two ways:

- **REST**: `/api/lab/*`, documented below.
- **MCP/agent tools**: new `mode`s on `data_profile` and new `action`s on `data_model` — see
  `docs/MCP.md`. The tool catalogue stays at 18 tools; nothing new was added.

Every call is recorded in the analysis log (`data_log` / `GET /api/log`) with an id like
`N-000123`, same as every other operation in the app.

## The model registry vs. the original `data_model`

The original `model_train`/`model_cluster`/`model_forecast` methods (and their `data_model`/
`data_cluster`/`data_forecast` tools) are **unchanged** and keep working exactly as before — they
do not persist a model artifact to disk, so they can't later be evaluated/tuned/explained/
optimized.

Lab adds a second, persisted path: `POST /api/lab/models/train` (or `data_model` with `lab=true`)
trains through a larger backend registry and saves the fitted model (joblib, or pickle as a
fallback when joblib isn't importable — in practice joblib is always present, since scikit-learn
depends on it) under `<data_dir>/lab_models/model_<id>.joblib`, with its row in the same `models`
SQLite table tagged `params.lab = true`. Every other Lab model action (`evaluate`, `explain`,
`tune`, `optimize`, `pareto`, registry `get`/`delete`) operates on one of these persisted models by
its `model_id`.

## 1. EDA / quality score / drift

### `GET /api/lab/datasets/{name}/eda`

The full bundle: correlation, null patterns, outliers, encoding suggestions, quality score.

```json
{
  "dataset": "sales", "version": 2, "row_count": 5000, "column_count": 8,
  "correlation": {
    "columns": ["price", "quantity", "revenue"],
    "pearson": [[1.0, -0.12, 0.87], [-0.12, 1.0, 0.31], [0.87, 0.31, 1.0]],
    "spearman": [[1.0, -0.10, 0.85], ["...", 1.0, "..."], ["...", "...", 1.0]],
    "top_pairs": [{"a": "price", "b": "revenue", "pearson": 0.87, "spearman": 0.85}]
  },
  "null_patterns": {
    "row_count": 5000,
    "columns": [{"column": "region", "nulls": 40, "null_rate": 0.008}],
    "co_occurrence": [{"a": "region", "b": "postal_code", "correlation": 0.62}]
  },
  "outliers": {
    "columns": [{
      "column": "revenue", "n": 4990,
      "iqr": {"count": 32, "rate": 0.0064, "lower_fence": -120.0, "upper_fence": 980.0},
      "zscore": {"count": 18, "rate": 0.0036},
      "box_plot": {"min": 0.0, "q1": 45.0, "median": 120.0, "q3": 310.0, "max": 4200.0,
                    "whisker_low": 0.0, "whisker_high": 950.0}
    }]
  },
  "encoding_suggestions": {
    "columns": [
      {"column": "revenue", "kind": "numeric", "skew": 1.8, "min": 0.0, "max": 4200.0, "suggestion": "log"},
      {"column": "region", "kind": "categorical", "cardinality": 6, "suggestion": "one_hot"},
      {"column": "order_id", "kind": "id_like", "suggestion": "treat as an identifier, not a feature"}
    ]
  },
  "quality_score": {
    "score": 91.4, "weights": {"completeness": 0.3, "uniqueness": 0.2, "validity": 0.2,
                                 "outliers": 0.15, "constant_columns": 0.15},
    "components": {"completeness": 0.98, "uniqueness": 0.99, "validity": 0.97,
                    "outliers": 0.95, "constant_columns": 1.0},
    "row_count": 5000, "column_count": 8, "duplicate_rows": 12, "null_cells": 40,
    "constant_columns": []
  }
}
```

### `GET /api/lab/datasets/{name}/quality-score`

Just the `quality_score` object above.

### `POST /api/lab/drift`

Distribution drift between two datasets (or two slices, each ingested/filtered into its own
dataset first): KS test, PSI, Wasserstein distance, mean/sd shift, per numeric column.

Request:
```json
{"dataset": "sales_jan", "other_dataset": "sales_feb", "columns": ["price", "revenue"]}
```
Response:
```json
{
  "columns": [
    {"column": "price", "n_a": 3000, "n_b": 3200, "ks_statistic": 0.21, "ks_pvalue": 0.0001,
     "psi": 0.34, "wasserstein_distance": 4.2, "mean_a": 42.1, "mean_b": 46.3, "mean_shift": 4.2,
     "sd_a": 5.1, "sd_b": 5.4, "sd_shift": 0.3, "flagged": true}
  ],
  "n_flagged": 1, "ks_alpha": 0.05, "psi_threshold": 0.2,
  "dataset_a": "sales_jan", "dataset_b": "sales_feb"
}
```

### `POST /api/lab/compare-curves`

Generic curve/series comparison: overlay, gap/noise detection, growth rate, and (with exactly two
groups) systematic bias between them.

Request:
```json
{"dataset": "sensor_readings", "x": "timestamp", "y": "value", "group": "sensor_id"}
```
Response (grouped):
```json
{
  "groups": {
    "sensor_a": {"n": 500, "overlay": [{"x": 1.0, "y": 12.3}, "..."], "gaps": [],
                  "noise_ratio": 0.04, "growth_rate": {"mean": 0.001, "median": 0.0009, "std": 0.02}},
    "sensor_b": {"n": 500, "overlay": ["..."], "gaps": [{"after_index": 220, "x_before": 220.0,
                                                            "x_after": 260.0, "gap_size": 40.0}],
                  "noise_ratio": 0.09, "growth_rate": {"mean": 0.0012, "median": 0.001, "std": 0.03}}
  },
  "systematic_bias": {"group_a": "sensor_a", "group_b": "sensor_b", "mean_bias": 2.1,
                        "std_bias": 0.4, "max_abs_bias": 3.0}
}
```
Without `group`, the response is `{"series": {...one group's shape...}}`.

## 2. Model registry

### `GET /api/lab/backends?task=regression|classification`

```json
{
  "regression": [
    {"name": "linear", "available": true, "note": ""},
    {"name": "ridge", "available": true, "note": ""},
    {"name": "random_forest", "available": true, "note": ""},
    {"name": "gradient_boosting", "available": true, "note": ""},
    {"name": "extra_trees", "available": true, "note": ""},
    {"name": "knn", "available": true, "note": ""},
    {"name": "mlp", "available": true, "note": ""},
    {"name": "gaussian_process", "available": true, "note": ""},
    {"name": "xgboost", "available": false, "note": "requires the optional 'xgboost' package"},
    {"name": "lightgbm", "available": false, "note": "requires the optional 'lightgbm' package"}
  ]
}
```
(`classification` swaps `linear`/`ridge`→`logistic`/`ridge` classifiers etc.; omit `task` to get
both.)

### `POST /api/lab/models/train`

Request:
```json
{"dataset": "sales", "target": "revenue", "features": ["price", "quantity"],
 "backend": "gaussian_process", "test_size": 0.2, "seed": 42, "write_to": "new_dataset"}
```
Response (shape mirrors the original `data_model` train result, plus `backend` instead of
`algorithm`, and always a persisted, evaluable model):
```json
{
  "model_id": 7, "task": "regression", "backend": "gaussian_process", "target": "revenue",
  "features": ["price", "quantity"], "excluded_id_like_columns": [],
  "metrics": {"r2": 0.91, "mae": 12.4, "rmse": 18.2},
  "feature_importance": [{"feature": "price", "importance": 0.71}],
  "train_rows": 4000, "test_rows": 1000, "sampled": false, "fit_seconds": 0.42,
  "write_to": "new_dataset", "dataset": "sales__lab_model_7", "prediction_column": "predicted_revenue",
  "log_id": "N-000042"
}
```

### `POST /api/lab/models/tune`

Request:
```json
{"dataset": "sales", "target": "revenue", "backend": "random_forest",
 "param_space": {"n_estimators": ["int", 50, 400], "max_depth": ["int", 2, 20]},
 "n_trials": 30, "cv": 5, "seed": 0}
```
`param_space` is optional — every backend with a default search space (`random_forest`,
`extra_trees`, `gradient_boosting`, `ridge`, `knn`, `mlp`, `xgboost`, `lightgbm`) works without one.

Response: the same shape as `train`, plus:
```json
{"tuning": {"method": "optuna_tpe", "best_params": {"n_estimators": 240, "max_depth": 11},
            "best_cv_score": 0.93, "n_trials": 30,
            "trials": [{"trial": 0, "params": {"...": "..."}, "score": 0.88}, "..."]}}
```
`method` is `"random_search"` (a `ParameterSampler`-driven, cross-validated search) when Optuna
isn't installed — same response shape either way.

### `GET /api/lab/models`, `GET /api/lab/models/{id}`, `POST /api/lab/models/compare`, `DELETE /api/lab/models/{id}`

List/get/compare/delete registered Lab models (`?dataset=name` filters the list).
`compare` request: `{"model_ids": [7, 9]}` → `{"models": [<registry get shape>, ...]}`.
`delete` removes the SQLite row and the joblib file.

### `POST /api/lab/models/{id}/evaluate`

Request: `{"eval_dataset": "sales_holdout", "date_col": "order_date", "group_col": "region"}` (all
optional — with no `eval_dataset`, the model's own training dataset is re-scored in-sample, and the
response says so in `note`).

Response (regression):
```json
{
  "task": "regression", "backend": "random_forest", "n_rows": 1000,
  "metrics": {"rmse": 18.1, "mae": 12.0, "r2": 0.90, "mape": 0.08},
  "residuals": {
    "vs_predicted": [{"predicted": 120.4, "residual": -3.1}, "..."],
    "histogram": {"counts": [1, 4, 22, "..."], "edges": [-40.0, -30.0, "..."]},
    "qq": {"theoretical": [-2.1, -1.8, "..."], "sample": [-35.0, -28.0, "..."]}
  },
  "predicted_vs_actual": [{"actual": 118.0, "predicted": 120.4}, "..."],
  "error_by_group": [{"group": "north", "n": 500, "mean_residual": 1.2, "mae": 11.0}],
  "bias_over_time": [{"date": "2024-01-01", "rolling_mean_residual": 0.4}],
  "fit_diagnosis": {
    "train_score": 0.97, "cv_score": 0.89, "holdout_score": 0.90, "train_holdout_gap": 0.07,
    "diagnosis": "no strong over/under-fitting signal",
    "learning_curve": [{"train_size": 800, "train_score": 0.96, "cv_score": 0.88}]
  },
  "model_id": 7, "eval_dataset": "sales_holdout"
}
```
Classification swaps `metrics` for `{accuracy, f1, precision, recall, roc_auc, confusion_matrix}`,
and (binary only) adds `calibration: {mean_predicted: [...], fraction_positive: [...]}`. When the
model exposes `predict_proba`, it also adds a `roc_curve`: for a binary classifier,
`{"positive_class": "yes", "points": [{"fpr": 0.0, "tpr": 0.0, "threshold": 1.9}, "..."]}`
(downsampled to at most 100 points); for multiclass, one-vs-rest curves per class instead â€”
`{"one_vs_rest": [{"class": "north", "auc": 0.88, "points": ["..."]}, "..."]}`. If an evaluation
slice leaves some class with no positive or no negative examples, that class's curve is skipped
(or `roc_curve` becomes `{"note": "..."}` when this happens for every class).

### `POST /api/lab/models/{id}/explain`

Request: `{"dataset": "sales", "sample_size": 200, "row_index": 5, "seed": 42}` (`dataset` optional,
default the training dataset).

Response (SHAP, when installed):
```json
{
  "method": "shap",
  "global_importance": [{"feature": "price", "importance": 0.41}, "..."],
  "row_explanation": {
    "row_index": 5, "base_value": 100.2,
    "contributions": [{"feature": "price", "value": 55.0, "contribution": 18.4}, "..."]
  },
  "model_id": 7, "dataset": "sales"
}
```
Fallback (`method: "permutation_importance"`) additionally returns `partial_dependence` for the
top features, and a row explanation built by zeroing each feature to the sample mean in turn.

### `POST /api/lab/models/{id}/optimize`

Request:
```json
{"direction": "maximize", "bounds": {"price": [10, 100]}, "fixed": {"channel": "online"},
 "integer_features": [], "categorical_features": {}, "constraints": [{"coeffs": {"price": 1}, "le": 90}],
 "acquisition": "ei", "n_candidates": 3000, "batch_size": 5, "seed": 42, "write_to": "new_dataset"}
```
All fields optional; `bounds` defaults to each feature's observed min/max, `acquisition` is `"ei"`
(Expected Improvement) or `"ucb"`.

Response:
```json
{
  "direction": "maximize", "acquisition": "ei", "n_candidates_evaluated": 3000,
  "best_observed_in_training_data": 812.0,
  "best_in_search": {"inputs": {"price": 61.2, "quantity": 8}, "predicted_value": 940.1},
  "refined_best": {"inputs": {"price": 60.4, "quantity": 8}, "predicted_value": 941.6},
  "suggested_points": [
    {"inputs": {"price": 61.2, "quantity": 8}, "predicted_value": 940.1, "uncertainty": 4.2,
     "acquisition_score": 6.1},
    "... 4 more"
  ],
  "model_id": 7, "write_to": "new_dataset", "optimize_dataset": "sales__optimize_7"
}
```

### `POST /api/lab/pareto`

Request:
```json
{"model_ids": [7, 9], "directions": ["maximize", "minimize"], "n_candidates": 1000, "seed": 0,
 "write_to": "new_dataset"}
```
`directions` has one entry per `model_ids` (2 or 3 models = 2-3 objectives). Response:
```json
{
  "objectives": [{"target": "revenue", "direction": "maximize"}, {"target": "cost", "direction": "minimize"}],
  "n_candidates_evaluated": 1000, "n_front": 42,
  "front": [{"inputs": {"price": 40.0, "quantity": 5}, "objectives": [500.0, 120.0]}, "..."],
  "model_ids": [7, 9], "write_to": "new_dataset", "pareto_dataset": "pareto_7_9"
}
```

### `POST /api/lab/report`

Request: `{"dataset": "sales", "model_id": 7, "eval_dataset": null, "optimize": true, "optimize_params": {"direction": "maximize"}}`
(`model_id`/`optimize` optional — the report only renders sections it was given).

Response: `{"path": "/abs/path/to/data/exports/lab_report_sales_1234567.pdf", "log_id": "N-000050"}`

## 3. Visual pipeline (node editor)

### `GET /api/lab/datasets/{name}/pipeline`

The dataset's recorded recipe as a graph:
```json
{
  "dataset": "sales", "tip": "step_2",
  "nodes": [
    {"id": "v0", "kind": "source", "params": {}, "version": 0},
    {"id": "step_1", "kind": "filter", "params": {"expr": "amount > 0"}, "version": 1},
    {"id": "dataset:regions", "kind": "dataset_ref", "dataset": "regions"},
    {"id": "step_2", "kind": "join", "params": {"other_dataset": "regions", "on": [{"left": "region_id", "right": "id"}]}, "version": 2}
  ],
  "edges": [["v0", "step_1"], ["step_1", "step_2"], ["dataset:regions", "step_2"]]
}
```

### `POST /api/lab/datasets/{name}/pipeline/apply`

Request: `{"graph": {...same shape, edited...}, "dry_run": true}`.

`dry_run: true` validates and previews the effect (materializing steps into temporary tables that
are dropped immediately after) without touching the dataset's real version history:
```json
{"dry_run": true, "steps": [{"op": "filter", "params": {"expr": "amount > 0"}}],
 "row_count": 4820, "columns": [{"name": "amount", "type": "DOUBLE"}, "..."]}
```

`dry_run: false` actually applies it: the dataset branches off its `v0` (the original ingest is
never touched or rebuilt — only what comes after it), discarding whatever step history followed
`v0` before, and creates one new version per graph step, exactly as `data_transform` would one step
at a time. The response is the usual dataset summary (`current_version`, `version_count`,
`row_count`, `columns`, ...).

A graph must be one linear chain from a single `source` node to its tip, with `dataset_ref` nodes
(no incoming edges) feeding into `join`/`union` steps — branching or cycles are rejected with a
clear error before anything is touched.

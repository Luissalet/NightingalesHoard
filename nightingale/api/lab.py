"""/api/lab/* — the REST surface for the Lab package (EDA/quality/drift, the
model registry, diagnostics, tuning, explanations, optimization/Pareto, PDF
reports, and the visual pipeline graph). Every route runs with `source="ui"`
in the analysis log, same convention as `api/ui.py`. See `docs/LAB.md` for a
full request/response reference.
"""

from __future__ import annotations

from typing import Any, Optional

from fastapi import APIRouter, Request
from pydantic import BaseModel, Field

from .deps import api_errors, services

router = APIRouter(prefix="/api/lab")


# ---- EDA / quality / drift -------------------------------------------------

@router.get("/datasets/{name}/eda")
@api_errors
def eda(request: Request, name: str):
    return services(request).lab_eda_profile(name)


@router.get("/datasets/{name}/quality-score")
@api_errors
def quality_score(request: Request, name: str):
    return services(request).lab_quality_score(name)


class DriftBody(BaseModel):
    dataset: str
    other_dataset: str
    columns: Optional[list[str]] = None


@router.post("/drift")
@api_errors
def drift(request: Request, body: DriftBody):
    return services(request).lab_drift(body.dataset, body.other_dataset, body.columns)


class CompareCurvesBody(BaseModel):
    dataset: str
    x: str
    y: str
    group: Optional[str] = None


@router.post("/compare-curves")
@api_errors
def compare_curves(request: Request, body: CompareCurvesBody):
    return services(request).lab_compare_curves(body.dataset, body.x, body.y, body.group)


# ---- model registry ---------------------------------------------------------

@router.get("/backends")
@api_errors
def backends(request: Request, task: Optional[str] = None):
    return services(request).lab_backends(task)


class TrainBody(BaseModel):
    dataset: str
    target: str
    features: Optional[list[str]] = None
    task: Optional[str] = None
    backend: str = "random_forest"
    test_size: float = 0.2
    seed: int = 42
    write_to: str = "new_dataset"
    name: Optional[str] = None


@router.post("/models/train")
@api_errors
def train(request: Request, body: TrainBody):
    svc = services(request)
    return svc.lab_model_train(body.dataset, body.target, body.features, body.task, body.backend, body.test_size,
                                body.seed, body.write_to, body.name)


class TuneBody(BaseModel):
    dataset: str
    target: str
    features: Optional[list[str]] = None
    task: Optional[str] = None
    backend: str = "random_forest"
    param_space: Optional[dict[str, Any]] = None
    n_trials: int = 20
    timeout: Optional[float] = None
    cv: int = 5
    seed: int = 42
    test_size: float = 0.2
    name: Optional[str] = None


@router.post("/models/tune")
@api_errors
def tune(request: Request, body: TuneBody):
    svc = services(request)
    return svc.lab_model_tune(body.dataset, body.target, body.features, body.task, body.backend, body.param_space,
                               body.n_trials, body.timeout, body.cv, body.seed, body.test_size, body.name)


@router.get("/models")
@api_errors
def registry_list(request: Request, dataset: Optional[str] = None):
    return services(request).lab_registry_list(dataset)


@router.get("/models/{model_id}")
@api_errors
def registry_get(request: Request, model_id: int):
    return services(request).lab_registry_get(model_id)


class CompareBody(BaseModel):
    model_ids: list[int]


@router.post("/models/compare")
@api_errors
def registry_compare(request: Request, body: CompareBody):
    return services(request).lab_registry_compare(body.model_ids)


@router.delete("/models/{model_id}")
@api_errors
def registry_delete(request: Request, model_id: int):
    return services(request).lab_registry_delete(model_id)


class EvaluateBody(BaseModel):
    eval_dataset: Optional[str] = None
    date_col: Optional[str] = None
    group_col: Optional[str] = None


@router.post("/models/{model_id}/evaluate")
@api_errors
def evaluate(request: Request, model_id: int, body: EvaluateBody):
    svc = services(request)
    return svc.lab_model_evaluate(model_id, body.eval_dataset, body.date_col, body.group_col)


class ExplainBody(BaseModel):
    dataset: Optional[str] = None
    sample_size: int = 200
    row_index: Optional[int] = None
    seed: int = 42


@router.post("/models/{model_id}/explain")
@api_errors
def explain(request: Request, model_id: int, body: ExplainBody):
    svc = services(request)
    return svc.lab_model_explain(model_id, body.dataset, body.sample_size, body.row_index, body.seed)


class OptimizeBody(BaseModel):
    direction: str = "maximize"
    bounds: Optional[dict[str, Any]] = None
    fixed: Optional[dict[str, Any]] = None
    integer_features: Optional[list[str]] = None
    categorical_features: Optional[dict[str, Any]] = None
    constraints: Optional[list[dict[str, Any]]] = None
    acquisition: str = "ei"
    n_candidates: int = 3000
    batch_size: int = 5
    seed: int = 42
    write_to: str = "none"


@router.post("/models/{model_id}/optimize")
@api_errors
def optimize(request: Request, model_id: int, body: OptimizeBody):
    svc = services(request)
    return svc.lab_model_optimize(model_id, body.direction, body.bounds, body.fixed, body.integer_features,
                                   body.categorical_features, body.constraints, body.acquisition, body.n_candidates,
                                   body.batch_size, body.seed, body.write_to)


class ParetoBody(BaseModel):
    model_ids: list[int]
    directions: list[str]
    bounds: Optional[dict[str, Any]] = None
    fixed: Optional[dict[str, Any]] = None
    n_candidates: int = 1000
    seed: int = 42
    write_to: str = "none"


@router.post("/pareto")
@api_errors
def pareto(request: Request, body: ParetoBody):
    svc = services(request)
    return svc.lab_pareto(body.model_ids, body.directions, body.bounds, body.fixed, body.n_candidates, body.seed,
                           body.write_to)


class ReportBody(BaseModel):
    dataset: str
    model_id: Optional[int] = None
    eval_dataset: Optional[str] = None
    optimize: bool = False
    optimize_params: Optional[dict[str, Any]] = None


@router.post("/report")
@api_errors
def report(request: Request, body: ReportBody):
    svc = services(request)
    return svc.lab_report(body.dataset, body.model_id, body.eval_dataset, body.optimize, body.optimize_params)


# ---- visual pipeline ---------------------------------------------------------

@router.get("/datasets/{name}/pipeline")
@api_errors
def pipeline_graph(request: Request, name: str):
    return services(request).lab_pipeline_graph(name)


class PipelineApplyBody(BaseModel):
    graph: dict[str, Any]
    dry_run: bool = False


@router.post("/datasets/{name}/pipeline/apply")
@api_errors
def pipeline_apply(request: Request, name: str, body: PipelineApplyBody):
    return services(request).lab_pipeline_apply(name, body.graph, body.dry_run)

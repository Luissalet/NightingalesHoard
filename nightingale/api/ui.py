"""/api/* — the REST surface the React client calls. Every call here runs with
source="ui" in the analysis log (never "agent"), so "assistant activity" can
be told apart in the Log screen.
"""

from __future__ import annotations

from typing import Any, Optional

from fastapi import APIRouter, Request
from pydantic import BaseModel, Field

from .deps import api_errors, services

router = APIRouter(prefix="/api")


# ---- sources / ingestion ----------------------------------------------------

class IngestBody(BaseModel):
    kind: str = Field("file", pattern="^(file|folder|url|paste)$")
    path: Optional[str] = None
    url: Optional[str] = None
    text: Optional[str] = None
    name: Optional[str] = None
    fmt: str = Field("csv", pattern="^(csv|json)$")
    glob: str = "*.csv"
    options: dict[str, Any] = Field(default_factory=dict)


@router.post("/sources/ingest")
@api_errors
def ingest(request: Request, body: IngestBody):
    svc = services(request)
    if body.kind == "file":
        if not body.path:
            raise ValueError("path is required")
        return svc.ingest_file(body.path, body.name, body.options)
    if body.kind == "folder":
        if not body.path:
            raise ValueError("path is required")
        return svc.ingest_folder(body.path, body.name, body.glob)
    if body.kind == "url":
        if not body.url:
            raise ValueError("url is required")
        return svc.ingest_url(body.url, body.name, body.fmt, body.options)
    if not (body.text and body.name):
        raise ValueError("text and name are required")
    return svc.ingest_text(body.text, body.name, body.fmt, body.options)


@router.get("/sources")
@api_errors
def sources(request: Request):
    return {"sources": [dict(r) for r in services(request).meta.list_sources()]}


# ---- datasets ----------------------------------------------------------------

@router.get("/datasets")
@api_errors
def list_datasets(request: Request):
    return services(request).list_datasets()


@router.post("/datasets/{name}/refresh")
@api_errors
def refresh(request: Request, name: str):
    return services(request).refresh(name)


@router.get("/datasets/{name}/profile")
@api_errors
def profile(request: Request, name: str, version: Optional[int] = None):
    return services(request).profile(name, version)


@router.get("/datasets/{name}/correlation")
@api_errors
def correlation(request: Request, name: str):
    return services(request).correlation(name)


@router.get("/datasets/{name}/preview")
@api_errors
def preview(request: Request, name: str, version: Optional[int] = None, limit: int = 50):
    return services(request).preview(name, version, limit)


@router.get("/datasets/{name}/lineage")
@api_errors
def lineage(request: Request, name: str):
    return services(request).lineage(name)


@router.get("/datasets/{name}/recipe")
@api_errors
def recipe(request: Request, name: str, action: str = "show"):
    return services(request).recipe(name, action)


class TransformBody(BaseModel):
    op: str
    params: dict[str, Any] = Field(default_factory=dict)
    preview: bool = True


@router.post("/datasets/{name}/transform")
@api_errors
def transform(request: Request, name: str, body: TransformBody):
    svc = services(request)
    if body.preview:
        return svc.transform_preview(name, body.op, body.params)
    return svc.transform_apply(name, body.op, body.params)


class UndoBody(BaseModel):
    steps: int = 1


@router.post("/datasets/{name}/undo")
@api_errors
def undo(request: Request, name: str, body: UndoBody):
    return services(request).undo(name, body.steps)


@router.post("/datasets/{name}/redo")
@api_errors
def redo(request: Request, name: str, body: UndoBody):
    return services(request).redo(name, body.steps)


class JoinPreviewBody(BaseModel):
    other_dataset: str
    on: list[dict[str, str]]
    how: str = "left"


@router.post("/datasets/{name}/join-preview")
@api_errors
def join_preview(request: Request, name: str, body: JoinPreviewBody):
    return services(request).join_preview(name, body.other_dataset, body.on, body.how)


# ---- query ---------------------------------------------------------------

class QueryBody(BaseModel):
    sql: str
    limit: int = 200


@router.post("/query")
@api_errors
def query(request: Request, body: QueryBody):
    return services(request).query(body.sql, body.limit)


# ---- quality ---------------------------------------------------------------

class QualityDefineBody(BaseModel):
    name: str
    kind: str
    params: dict[str, Any] = Field(default_factory=dict)


@router.post("/datasets/{name}/quality")
@api_errors
def quality_define(request: Request, name: str, body: QualityDefineBody):
    return services(request).quality_define(name, body.name, body.kind, body.params)


@router.get("/datasets/{name}/quality")
@api_errors
def quality_report(request: Request, name: str):
    return services(request).quality_report(name)


class QualityRunBody(BaseModel):
    rule_id: Optional[int] = None


@router.post("/datasets/{name}/quality/run")
@api_errors
def quality_run(request: Request, name: str, body: QualityRunBody):
    return services(request).quality_run(name, body.rule_id)


@router.delete("/quality/{rule_id}")
@api_errors
def quality_delete(request: Request, rule_id: int):
    return services(request).quality_delete(rule_id)


# ---- charts / dashboards ---------------------------------------------------

class ChartBody(BaseModel):
    dataset: str
    kind: str
    x: Optional[str] = None
    y: Optional[str] = None
    agg: str = "sum"
    color: Optional[str] = None
    filter: Optional[str] = None
    title: str = ""
    name: Optional[str] = None
    image: bool = False
    lang: str = "en"


@router.post("/charts")
@api_errors
def chart_create(request: Request, body: ChartBody):
    svc = services(request)
    return svc.chart_create(body.dataset, body.kind, body.x, body.y, body.agg, body.color, body.filter,
                             body.title, body.name, body.image, lang=body.lang)


@router.get("/charts")
@api_errors
def chart_list(request: Request):
    return services(request).chart_list()


@router.get("/charts/{chart_id}")
@api_errors
def chart_get(request: Request, chart_id: int, image: bool = False, lang: str = "en"):
    return services(request).chart_get(chart_id, image, lang=lang)


@router.delete("/charts/{chart_id}")
@api_errors
def chart_delete(request: Request, chart_id: int):
    return services(request).chart_delete(chart_id)


class DashboardCreateBody(BaseModel):
    name: str
    items: list[dict[str, Any]] = Field(default_factory=list)
    filter: dict[str, Any] = Field(default_factory=dict)


@router.post("/dashboards")
@api_errors
def dashboard_create(request: Request, body: DashboardCreateBody):
    return services(request).dashboard_create(body.name, body.items, body.filter)


@router.get("/dashboards")
@api_errors
def dashboard_list(request: Request):
    return services(request).dashboard_list()


@router.get("/dashboards/{dashboard_id}")
@api_errors
def dashboard_get(request: Request, dashboard_id: int):
    return services(request).dashboard_get(dashboard_id)


class DashboardItemBody(BaseModel):
    item: dict[str, Any]


@router.post("/dashboards/{dashboard_id}/items")
@api_errors
def dashboard_add(request: Request, dashboard_id: int, body: DashboardItemBody):
    return services(request).dashboard_add(dashboard_id, body.item)


# ---- models --------------------------------------------------------------

class ModelTrainBody(BaseModel):
    dataset: str
    target: str
    features: Optional[list[str]] = None
    task: Optional[str] = None
    algorithm: Optional[str] = None
    test_size: float = 0.2
    seed: int = 42
    write_predictions: bool = True
    name: Optional[str] = None


@router.post("/models/train")
@api_errors
def model_train(request: Request, body: ModelTrainBody):
    svc = services(request)
    return svc.model_train(body.dataset, body.target, body.features, body.task, body.algorithm,
                            body.test_size, body.seed, body.write_predictions, body.name)


class ClusterBody(BaseModel):
    dataset: str
    features: list[str]
    k: Optional[int] = None
    seed: int = 42
    write_labels: bool = True
    name: Optional[str] = None


@router.post("/models/cluster")
@api_errors
def model_cluster(request: Request, body: ClusterBody):
    svc = services(request)
    return svc.model_cluster(body.dataset, body.features, body.k, body.seed, body.write_labels, body.name)


class PcaBody(BaseModel):
    dataset: str
    features: list[str]
    n_components: int = 2
    seed: int = 42


@router.post("/models/pca")
@api_errors
def model_pca(request: Request, body: PcaBody):
    return services(request).model_pca(body.dataset, body.features, body.n_components, body.seed)


class AnomalyBody(BaseModel):
    dataset: str
    features: list[str]
    contamination: float = 0.05
    seed: int = 42
    write_flags: bool = True
    name: Optional[str] = None


@router.post("/models/anomaly")
@api_errors
def model_anomaly(request: Request, body: AnomalyBody):
    svc = services(request)
    return svc.model_anomaly(body.dataset, body.features, body.contamination, body.seed, body.write_flags, body.name)


class ForecastBody(BaseModel):
    dataset: str
    date_col: str
    value_col: str
    horizon: int = 12
    seasonal_period: Optional[int] = None
    name: Optional[str] = None
    write_dataset: bool = True
    freq: str = "auto"


@router.post("/models/forecast")
@api_errors
def model_forecast(request: Request, body: ForecastBody):
    svc = services(request)
    return svc.model_forecast(body.dataset, body.date_col, body.value_col, body.horizon, body.seasonal_period,
                               body.name, body.write_dataset, freq=body.freq)


@router.get("/models")
@api_errors
def model_list(request: Request, dataset: Optional[str] = None):
    return services(request).model_list(dataset)


# ---- export ----------------------------------------------------------------

class ExportBody(BaseModel):
    dataset: str
    format: str = "csv"
    path: Optional[str] = None


@router.post("/export")
@api_errors
def export(request: Request, body: ExportBody):
    return services(request).export(body.dataset, body.format, body.path)


# ---- log ---------------------------------------------------------------

@router.get("/log")
@api_errors
def log_search(request: Request, q: Optional[str] = None, source: Optional[str] = None,
               dataset: Optional[str] = None, limit: int = 50):
    return services(request).log_search(q, source, dataset, limit)


@router.get("/log/{op_id}")
@api_errors
def log_get(request: Request, op_id: str):
    return services(request).log_get(op_id)


# ---- ask your data -------------------------------------------------------

class AskBody(BaseModel):
    question: str
    datasets: Optional[list[str]] = None


@router.post("/ask")
@api_errors
async def ask(request: Request, body: AskBody):
    return await services(request).ask(body.question, body.datasets)


@router.get("/ask/available")
@api_errors
def ask_available(request: Request):
    return services(request).ask_available()

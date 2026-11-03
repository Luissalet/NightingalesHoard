"""/api/scenarios/* — operations models simulated with a discrete-event engine (scenarios, not predictions)."""

from __future__ import annotations

from typing import Any, Optional

from fastapi import APIRouter, Request
from pydantic import BaseModel, Field

from .. import scenarios as sc
from .deps import api_errors, services

router = APIRouter(prefix="/api/scenarios")


class ModelBody(BaseModel):
    name: Optional[str] = None
    model: Optional[dict[str, Any]] = None
    replications: Optional[int] = Field(None, ge=1, le=200)
    seed: Optional[int] = None
    horizon_hours: Optional[float] = Field(None, gt=0)
    save_as: Optional[str] = None


class SaveBody(BaseModel):
    name: str
    model: dict[str, Any]


class CompareBody(ModelBody):
    variants: list[dict[str, Any]]


class SweepBody(ModelBody):
    param: str
    values: list[Any]


class CalibrateBody(BaseModel):
    dataset: str
    column: str
    kind: str = "service"
    unit: str = "hours"


@router.get("/template")
@api_errors
def template():
    return {"model": sc.default_model()}


@router.get("/models")
@api_errors
def models(request: Request):
    return sc.list_models(services(request))


@router.get("/models/{name}")
@api_errors
def get_model(request: Request, name: str):
    return sc.get_model(services(request), name)


@router.post("/models")
@api_errors
def save(request: Request, body: SaveBody):
    return sc.save_model(services(request), body.name, body.model)


@router.delete("/models/{name}")
@api_errors
def delete(request: Request, name: str):
    return sc.delete_model(services(request), name)


@router.post("/run")
@api_errors
def run(request: Request, body: ModelBody):
    return sc.run_scenario(services(request), body.name, body.model, body.replications, body.seed,
                           body.horizon_hours, body.save_as)


@router.post("/compare")
@api_errors
def compare(request: Request, body: CompareBody):
    return sc.compare_scenarios(services(request), body.name, body.model, body.variants, body.replications,
                                body.seed, body.save_as)


@router.post("/sweep")
@api_errors
def sweep(request: Request, body: SweepBody):
    return sc.sweep_scenario(services(request), body.name, body.model, body.param, body.values, body.replications,
                             body.seed, body.save_as)


@router.post("/calibrate")
@api_errors
def calibrate(request: Request, body: CalibrateBody):
    return sc.calibrate_scenario(services(request), body.dataset, body.column, body.kind, body.unit)

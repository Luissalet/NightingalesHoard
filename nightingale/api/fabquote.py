"""/api/fabquote/* — fabrication quotes by scenario (parameters with units and sources, formulas, actuals)."""

from __future__ import annotations

from typing import Any, Optional
from urllib.parse import quote as urlquote

from fastapi import APIRouter, Request, Response
from pydantic import BaseModel, Field

from .. import fabquote as fq
from .deps import api_errors, services

router = APIRouter(prefix="/api/fabquote")


class VulcanRef(BaseModel):
    model_id: Any
    base_url: Optional[str] = None
    target: Optional[str] = "fdm-print"


class CreateBody(BaseModel):
    name: str
    process: Optional[str] = None
    measurements: Optional[dict[str, Any]] = None
    parameters: Optional[dict[str, Any]] = None
    rate_card: Optional[dict[str, Any]] = None
    vulcan: Optional[VulcanRef] = None
    volume_only: Optional[str] = None
    currency: str = "EUR"
    spec: Optional[dict[str, Any]] = None
    replace: bool = False


class ParamsBody(BaseModel):
    parameters: dict[str, Any]


class OptionsBody(BaseModel):
    volume_only: str


class ScenarioBody(BaseModel):
    overrides: dict[str, Any]
    description: str = ""


class SensBody(BaseModel):
    params: Optional[list[str]] = None
    pct: float = Field(10.0, gt=0, lt=100)
    scenario: Optional[str] = None


class ActualBody(BaseModel):
    item: str
    value: float
    unit: Optional[str] = None
    ledger_ref: Optional[str] = None
    note: str = ""


class VulcanBody(BaseModel):
    model_id: Any
    base_url: Optional[str] = None
    target: Optional[str] = "fdm-print"


@router.get("/templates")
@api_errors
def templates():
    return fq.templates()


@router.post("/vulcan")
@api_errors
def vulcan(body: VulcanBody):
    return fq.fetch_vulcan(body.model_id, body.base_url, body.target or "fdm-print")


@router.get("/quotes")
@api_errors
def quotes(request: Request):
    return fq.list_quotes(services(request))


@router.post("/quotes")
@api_errors
def create(request: Request, body: CreateBody):
    return fq.create_quote(services(request), body.name, body.process, body.measurements, body.parameters,
                           body.rate_card, body.vulcan.model_dump() if body.vulcan else None, body.volume_only,
                           body.currency, body.spec, body.replace)


@router.get("/quotes/{name}")
@api_errors
def get(request: Request, name: str):
    return fq.get_quote(services(request), name)


@router.delete("/quotes/{name}")
@api_errors
def delete(request: Request, name: str):
    return fq.delete_quote(services(request), name)


@router.put("/quotes/{name}/parameters")
@api_errors
def set_params(request: Request, name: str, body: ParamsBody):
    return fq.set_parameters(services(request), name, body.parameters)


@router.put("/quotes/{name}/options")
@api_errors
def set_options(request: Request, name: str, body: OptionsBody):
    return fq.set_options(services(request), name, body.volume_only)


@router.put("/quotes/{name}/scenarios/{scenario}")
@api_errors
def save_scenario(request: Request, name: str, scenario: str, body: ScenarioBody):
    return fq.save_scenario(services(request), name, scenario, body.overrides, body.description)


@router.delete("/quotes/{name}/scenarios/{scenario}")
@api_errors
def delete_scenario(request: Request, name: str, scenario: str):
    return fq.delete_scenario(services(request), name, scenario)


@router.get("/quotes/{name}/compare")
@api_errors
def compare(request: Request, name: str, scenarios: Optional[str] = None):
    names = [s for s in (scenarios or "").split(",") if s] or None
    return fq.compare_scenarios(services(request), name, names)


@router.post("/quotes/{name}/sensitivity")
@api_errors
def sensitivity(request: Request, name: str, body: SensBody):
    return fq.run_sensitivity(services(request), name, body.params, body.pct, body.scenario)


@router.post("/quotes/{name}/actuals")
@api_errors
def add_actual(request: Request, name: str, body: ActualBody):
    return fq.record_actual(services(request), name, body.item, body.value, body.unit, body.ledger_ref, body.note)


@router.delete("/quotes/{name}/actuals/{actual_id}")
@api_errors
def delete_actual(request: Request, name: str, actual_id: int):
    return fq.delete_actual(services(request), name, actual_id)


@router.get("/quotes/{name}/actuals")
@api_errors
def actuals(request: Request, name: str, scenario: Optional[str] = None):
    return fq.compare_actuals(services(request), name, scenario)


@router.get("/quotes/{name}/export")
@api_errors
def export(request: Request, name: str, format: str = "json", scenario: Optional[str] = None, download: bool = False):
    out = fq.export_quote(services(request), name, format, scenario)
    if not download:
        return out
    return Response(out["content"], media_type=out["mime"] + "; charset=utf-8",
                    headers={"Content-Disposition": f"attachment; filename*=UTF-8''{urlquote(out['filename'])}"})

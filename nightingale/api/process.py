"""/api/process/* — discover a process from an event log, check it against a reference, rehearse changes;
profile the checked event log, calibrate a scenario model on a temporal holdout and export the recipe."""

from __future__ import annotations

from typing import Any, Optional

from fastapi import APIRouter, Request
from pydantic import BaseModel, Field

from .. import event_log as el
from .. import process_mining as pm
from .deps import api_errors, services

router = APIRouter(prefix="/api/process")


class SuggestBody(BaseModel):
    dataset: str


class LogBody(BaseModel):
    dataset: str
    mapping: dict[str, Optional[str]]
    save_as: Optional[str] = None


class DiscoverBody(LogBody):
    threshold: float = Field(0.5, ge=0, le=1)
    min_count: int = Field(1, ge=1)
    max_variants: int = Field(20, ge=1, le=500)


class ConformBody(LogBody):
    reference: dict[str, Any]
    max_examples: int = Field(5, ge=1, le=50)


class RehearseBody(LogBody):
    variants: list[dict[str, Any]] = Field(default_factory=list)
    replications: int = Field(20, ge=1, le=100)
    seed: int = 1
    tolerance: float = Field(0.15, gt=0, lt=1)
    model_name: Optional[str] = None


class EventLogBody(BaseModel):
    dataset: Optional[str] = None
    mapping: Optional[dict[str, Optional[str]]] = None
    log_options: Optional[dict[str, Any]] = None
    recipe: Optional[dict[str, Any]] = None


class ProfileBody(EventLogBody):
    threshold: float = Field(0.5, ge=0, le=1)
    min_count: int = Field(1, ge=1)
    max_variants: int = Field(20, ge=1, le=500)


class CalibrateBody(EventLogBody):
    holdout: float = Field(0.3, ge=0.05, le=0.8)
    split_at: Optional[str] = None
    replications: int = Field(20, ge=1, le=100)
    seed: int = 1
    tolerances: Optional[dict[str, float]] = None
    variants: list[dict[str, Any]] = Field(default_factory=list)
    model_name: Optional[str] = None


class ExportBody(CalibrateBody):
    with_calibration: bool = False


@router.post("/suggest")
@api_errors
def suggest(request: Request, body: SuggestBody):
    return pm.suggest_mapping(services(request), body.dataset)


@router.post("/discover")
@api_errors
def discover(request: Request, body: DiscoverBody):
    return pm.discover_process(services(request), body.dataset, body.mapping, body.threshold, body.min_count,
                               body.max_variants, body.save_as)


@router.post("/conform")
@api_errors
def conform(request: Request, body: ConformBody):
    return pm.conform_process(services(request), body.dataset, body.mapping, body.reference, body.max_examples,
                              body.save_as)


@router.post("/rehearse")
@api_errors
def rehearse(request: Request, body: RehearseBody):
    return pm.rehearse_process(services(request), body.dataset, body.mapping, body.variants, body.replications,
                               body.seed, body.tolerance, body.save_as, body.model_name)


@router.post("/profile")
@api_errors
def profile(request: Request, body: ProfileBody):
    return el.profile_process(services(request), body.dataset, body.mapping, body.log_options, body.threshold,
                              body.min_count, body.max_variants, body.recipe)


@router.post("/calibrate")
@api_errors
def calibrate(request: Request, body: CalibrateBody):
    return el.calibrate_process(services(request), body.dataset, body.mapping, body.log_options, body.holdout,
                                body.split_at, body.replications, body.seed, body.tolerances, body.variants,
                                body.model_name, body.recipe)


@router.post("/export")
@api_errors
def export(request: Request, body: ExportBody):
    return el.export_process(services(request), body.dataset, body.mapping, body.log_options, body.with_calibration,
                             body.holdout, body.split_at, body.replications, body.seed, body.tolerances,
                             body.variants)

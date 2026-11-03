"""/api/process/* — discover a process from an event log, check it against a reference, rehearse changes."""

from __future__ import annotations

from typing import Any, Optional

from fastapi import APIRouter, Request
from pydantic import BaseModel, Field

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

"""/api/reconcile/* — compare two datasets row by row without hiding lost, duplicated or missing rows."""

from __future__ import annotations

from typing import Any, Optional

from fastapi import APIRouter, Request
from fastapi.responses import Response
from pydantic import BaseModel, Field

from .. import reconcile as rec
from .deps import api_errors, services

router = APIRouter(prefix="/api/reconcile")


class Pair(BaseModel):
    left: str
    right: str


class SuggestBody(BaseModel):
    left: str
    right: str


class ReconcileBody(BaseModel):
    left: str
    right: str
    keys: list[Pair] = Field(..., min_length=1)
    compare: Optional[list[Pair]] = None
    options: dict[str, Any] = Field(default_factory=dict)
    sample: int = Field(10, ge=0, le=200)
    save_as: Optional[str] = None


@router.post("/suggest")
@api_errors
def suggest(request: Request, body: SuggestBody):
    return rec.suggest_datasets(services(request), body.left, body.right)


@router.post("/run")
@api_errors
def run(request: Request, body: ReconcileBody):
    compare = [p.model_dump() for p in body.compare] if body.compare is not None else None
    return rec.reconcile_datasets(services(request), body.left, body.right, [p.model_dump() for p in body.keys],
                                  compare, body.options, body.sample, body.save_as)


@router.post("/export.csv")
@api_errors
def export(request: Request, body: ReconcileBody):
    text = rec.export_csv(services(request), body.left, body.right, [p.model_dump() for p in body.keys], body.options)
    return Response(text, media_type="text/csv; charset=utf-8",
                    headers={"Content-Disposition": f'attachment; filename="reconcile-{body.left}-{body.right}.csv"'})

"""/api/dac/* — dashboards as code: the semantic layer and the YAML
dashboard spec compiled against it. See `docs/DASHBOARDS_AS_CODE.md`."""

from __future__ import annotations

from typing import Any, Optional

from fastapi import APIRouter, Request
from pydantic import BaseModel

from .deps import api_errors, services

router = APIRouter(prefix="/api/dac")


# ---- semantic layer -----------------------------------------------------------

@router.get("/semantic")
@api_errors
def semantic_get(request: Request):
    return services(request).dac_semantic_get()


class SemanticPutBody(BaseModel):
    text: str


@router.put("/semantic")
@api_errors
def semantic_put(request: Request, body: SemanticPutBody):
    return services(request).dac_semantic_put(body.text)


@router.post("/semantic/validate")
@api_errors
def semantic_validate(request: Request, body: SemanticPutBody):
    return services(request).dac_semantic_validate(body.text)


@router.get("/semantic/suggest")
@api_errors
def semantic_suggest(request: Request, dataset: str):
    return services(request).dac_semantic_suggest(dataset)


# ---- dashboards -----------------------------------------------------------------

@router.get("/dashboards")
@api_errors
def dashboards_list(request: Request):
    return services(request).dac_list()


class DashboardPutBody(BaseModel):
    text: str
    slug: Optional[str] = None


@router.post("/dashboards")
@api_errors
def dashboards_create(request: Request, body: DashboardPutBody):
    return services(request).dac_put(body.text, None)


@router.get("/dashboards/{slug}")
@api_errors
def dashboard_get(request: Request, slug: str):
    return services(request).dac_get(slug)


@router.put("/dashboards/{slug}")
@api_errors
def dashboard_put(request: Request, slug: str, body: DashboardPutBody):
    return services(request).dac_put(body.text, slug)


@router.delete("/dashboards/{slug}")
@api_errors
def dashboard_delete(request: Request, slug: str):
    services(request).dac_delete(slug)
    return {"ok": True}


class RenameBody(BaseModel):
    name: str


@router.post("/dashboards/{slug}/rename")
@api_errors
def dashboard_rename(request: Request, slug: str, body: RenameBody):
    return services(request).dac_rename(slug, body.name)


@router.post("/dashboards/{slug}/validate")
@api_errors
def dashboard_validate(request: Request, slug: str, body: Optional[DashboardPutBody] = None):
    return services(request).dac_validate(slug, body.text if body else None)


class RenderBody(BaseModel):
    filters: dict[str, Any] = {}


@router.post("/dashboards/{slug}/render")
@api_errors
def dashboard_render(request: Request, slug: str, body: RenderBody):
    return services(request).dac_render(slug, body.filters)


@router.get("/dashboards/{slug}/history")
@api_errors
def dashboard_history(request: Request, slug: str):
    return services(request).dac_history(slug)


@router.get("/dashboards/{slug}/diff")
@api_errors
def dashboard_diff(request: Request, slug: str, a: str, b: str = "current"):
    return services(request).dac_diff(slug, a, b)


@router.post("/dashboards/{slug}/export")
@api_errors
def dashboard_export(request: Request, slug: str, body: RenderBody):
    return services(request).dac_export(slug, body.filters)


class ImportBody(BaseModel):
    dashboard_id: int


@router.post("/dashboards/import")
@api_errors
def dashboard_import(request: Request, body: ImportBody):
    return services(request).dac_import(body.dashboard_id)

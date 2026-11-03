"""/api/migrate/* — rehearse a migration into a scratch SQLite file and report what would not survive."""

from __future__ import annotations

from typing import Any, Optional

from fastapi import APIRouter, HTTPException, Request
from fastapi.responses import FileResponse, Response
from pydantic import BaseModel, Field

from .. import migrate as mg
from .deps import api_errors, services

router = APIRouter(prefix="/api/migrate")


class SuggestBody(BaseModel):
    dataset: str
    table: Optional[str] = None
    name: Optional[str] = None


class RunBody(BaseModel):
    spec: Optional[dict[str, Any]] = None
    name: Optional[str] = None
    save_rejected: Optional[str] = None
    sample: int = Field(10, ge=0, le=200)


class DdlBody(BaseModel):
    spec: Optional[dict[str, Any]] = None
    name: Optional[str] = None


@router.post("/suggest")
@api_errors
def suggest(request: Request, body: SuggestBody):
    return mg.suggest_spec(services(request), body.dataset, body.table, body.name)


@router.get("/specs")
@api_errors
def specs(request: Request):
    return mg.list_specs(services(request))


@router.get("/specs/{name}")
@api_errors
def get_spec(request: Request, name: str):
    return mg.get_spec(services(request), name)


@router.put("/specs/{name}")
@api_errors
def put_spec(request: Request, name: str, spec: dict[str, Any]):
    return mg.save_spec(services(request), name, spec)


@router.delete("/specs/{name}")
@api_errors
def delete_spec(request: Request, name: str):
    return mg.delete_spec(services(request), name)


@router.post("/rehearse")
@api_errors
def rehearse(request: Request, body: RunBody):
    return mg.rehearse_spec(services(request), body.spec, body.name, body.save_rejected, body.sample)


@router.post("/ddl")
@api_errors
def ddl(request: Request, body: DdlBody):
    return mg.ddl(services(request), body.spec, body.name)


@router.post("/ddl.sql")
@api_errors
def ddl_sql(request: Request, body: DdlBody):
    out = mg.ddl(services(request), body.spec, body.name)
    return Response(out["ddl"], media_type="text/plain; charset=utf-8",
                    headers={"Content-Disposition": f'attachment; filename="{out["name"]}.sql"'})


@router.get("/file/{name}")
@api_errors
def rehearsal_file(request: Request, name: str):
    path = mg.rehearsal_path(services(request), name)
    if not path.exists():
        raise HTTPException(404, f"no rehearsal file for {name!r}; run the rehearsal first")
    return FileResponse(path, media_type="application/vnd.sqlite3", filename=f"{name}.sqlite")

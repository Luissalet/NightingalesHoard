"""/api/sheets/* — Excel workbooks compiled to an executable, verified, exportable calculation."""

from __future__ import annotations

import tempfile
from pathlib import Path
from typing import Any, Literal, Optional

from fastapi import APIRouter, File, Form, Request, UploadFile
from fastapi.responses import Response
from pydantic import BaseModel, Field

from ..sheets.store import SUFFIXES, WorkbookStore
from .deps import api_errors, services

router = APIRouter(prefix="/api/sheets")


def store(request: Request) -> WorkbookStore:
    return WorkbookStore(services(request).config.data_dir)


@router.get("")
@api_errors
def list_workbooks(request: Request):
    from ..sheets.reference import engines
    return {"workbooks": store(request).list(), "engines": engines()}


class CompileBody(BaseModel):
    path: str
    name: Optional[str] = None


@router.post("/compile")
@api_errors
def compile_path(request: Request, body: CompileBody):
    st = store(request)
    path = st.user_path(services(request).config.data_dir, body.path)
    return st.compile(path, body.name, original=str(path))


@router.post("/upload")
@api_errors
async def compile_upload(request: Request, file: UploadFile = File(...), name: Optional[str] = Form(None)):
    suffix = Path(file.filename or "libro.xlsx").suffix.lower()
    if suffix not in SUFFIXES:
        raise ValueError("solo se compilan libros .xlsx o .xlsm")
    data = await file.read()
    with tempfile.TemporaryDirectory(prefix="nightingale-upload-") as tmp:
        p = Path(tmp) / ("upload" + suffix)
        p.write_bytes(data)
        return store(request).compile(p, name or Path(file.filename or "libro").stem, original=file.filename)


@router.get("/{wid}")
@api_errors
def get_workbook(request: Request, wid: str):
    return store(request).get(wid)


@router.delete("/{wid}")
@api_errors
def delete_workbook(request: Request, wid: str):
    return store(request).delete(wid)


class EvaluateBody(BaseModel):
    inputs: dict[str, Any] = Field(default_factory=dict)
    as_of: Optional[str] = None


@router.post("/{wid}/evaluate")
@api_errors
def evaluate(request: Request, wid: str, body: EvaluateBody):
    return store(request).evaluate(wid, body.inputs, body.as_of)


@router.post("/{wid}/check")
@api_errors
def check(request: Request, wid: str):
    return store(request).check(wid)


class VerifyBody(BaseModel):
    engine: Optional[Literal["excel", "libreoffice"]] = None
    cases: Optional[list[dict[str, Any]]] = None
    n: int = Field(default=12, ge=1, le=200)
    seed: int = 7


@router.post("/{wid}/verify")
@api_errors
def verify(request: Request, wid: str, body: VerifyBody):
    return store(request).verify(wid, engine=body.engine, cases=body.cases, n=body.n, seed=body.seed)


@router.get("/{wid}/trace")
@api_errors
def trace(request: Request, wid: str, cell: str):
    return store(request).trace(wid, cell)


@router.get("/{wid}/export.py")
@api_errors
def export_py(request: Request, wid: str):
    filename, code = store(request).export_code(wid)
    return Response(code, media_type="text/x-python; charset=utf-8",
                    headers={"Content-Disposition": f'attachment; filename="{filename}"'})

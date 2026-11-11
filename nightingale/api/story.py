"""/api/story/* — the REST surface of "relato con los datos": build a story from
a dataset's current version, read it, reorder or drop findings, edit a
sentence, and export it as standalone HTML or as a presentation in Cicero's
Hoard. Every route runs with source="ui" in the analysis log, same convention
as `api/ui.py` and `api/lab.py`.
"""

from __future__ import annotations

from typing import Optional

from fastapi import APIRouter, Request
from fastapi.responses import FileResponse
from pydantic import BaseModel, Field

from .deps import api_errors, services

router = APIRouter(prefix="/api/story")


class BuildBody(BaseModel):
    dataset: str
    rewrite: bool = Field(False, description="Ask the family's model to reword the sentences; a sentence whose "
                                             "numbers are not the finding's own is discarded and the template stays.")


@router.post("/build")
@api_errors
def build(request: Request, body: BuildBody):
    return services(request).story_build(body.dataset, rewrite=body.rewrite)


@router.get("/stories")
@api_errors
def list_stories(request: Request, dataset: Optional[str] = None):
    return services(request).story_list(dataset)


@router.get("/stories/{story_id}")
@api_errors
def get_story(request: Request, story_id: int):
    return services(request).story_get(story_id)


class UpdateBody(BaseModel):
    order: Optional[list[str]] = Field(None, description="Every finding id, in the new order.")
    remove: Optional[list[str]] = Field(None, description="Finding ids to drop.")
    statements: Optional[dict[str, str]] = Field(None, description="{finding id: sentence}; an empty sentence "
                                                                    "restores the template.")
    title: Optional[str] = Field(None, max_length=200)


@router.put("/stories/{story_id}")
@api_errors
def update_story(request: Request, story_id: int, body: UpdateBody):
    return services(request).story_update(story_id, body.order, body.remove, body.statements, body.title)


@router.delete("/stories/{story_id}")
@api_errors
def delete_story(request: Request, story_id: int):
    return services(request).story_delete(story_id)


@router.post("/stories/{story_id}/export")
@api_errors
def export(request: Request, story_id: int, format: str = "html"):
    return services(request).story_export(story_id, format)


@router.get("/download")
@api_errors
def download(request: Request, path: str):
    file_path = services(request).story_export_path(path)
    return FileResponse(file_path, media_type="text/html", filename=file_path.name)
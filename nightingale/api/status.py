"""Health and status."""

from __future__ import annotations

from fastapi import APIRouter, Request

from .. import __version__
from .deps import services

router = APIRouter(prefix="/api")


@router.get("/health")
def health(request: Request):
    return {"service": "nightingale-hoard", "version": __version__,
            "dataDirConfigured": request.app.state.config.data_dir_configured}


@router.get("/status")
def status(request: Request):
    return services(request).status()

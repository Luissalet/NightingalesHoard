"""FastAPI application factory: request guard, API routers, static SPA."""

from __future__ import annotations

import logging
from contextlib import asynccontextmanager
from pathlib import Path

from fastapi import FastAPI

from . import __version__
from .api import ROUTERS
from .config import Config
from .hoard_link import family
from .hoard_link.guard import install_guard
from .hoard_link.service import health_router, install_error_handlers, install_pwa, install_spa
from .services import Services

SERVICE = "nightingale-hoard"
STATIC_DIR = Path(__file__).resolve().parent / "static"


def create_app(config: Config | None = None, services: Services | None = None) -> FastAPI:
    config = config or Config.from_env()

    @asynccontextmanager
    async def lifespan(app: FastAPI):
        svc = services or Services(config)
        app.state.services = svc
        family.configure("nightingale", str(config.data_dir), token_file=str(config.token_path))  # calls to sibling apps through the hub
        logging.getLogger("nightingale").info("Nightingale's Hoard %s - data in %s", __version__, config.data_dir)
        try:
            yield
        finally:
            svc.stop()

    app = FastAPI(title="Nightingale's Hoard", version=__version__, lifespan=lifespan, docs_url=None, redoc_url=None)
    app.state.config = config

    install_guard(app, port_getter=lambda: config.port, allowed_env="NIGHTINGALE_ALLOWED_HOSTS", allowed_hosts=config.allowed_hosts)
    install_error_handlers(app)  # one {"error", "code"} envelope: HTTP errors, validation, AppError, 500
    app.include_router(health_router(SERVICE, __version__, extra=lambda: {"dataDirConfigured": config.data_dir_configured, "demo": config.demo}))
    for router in ROUTERS:
        app.include_router(router)
    install_pwa(app, name="Nightingale's Hoard", short_name="Nightingale", theme="#7a1f4a", background="#fff6fa", cache="nightingale-hoard",
                lang="en", static_dir=STATIC_DIR, version=__version__)
    install_spa(app, STATIC_DIR)  # last: everything that is not an API route or a real file is the single page app
    return app

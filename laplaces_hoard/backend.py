"""App-side wiring for the shared model backend (vendored Hoard Link).

Laplace's Hoard uses exactly one shared-model capability, ``llm``, for the
"Ask your data" feature on the Data screen; every other feature (calc, math,
units, dates, SQL) never touches a model and keeps working with nothing
resolved. This module only loads/saves ``data/backend.json`` and builds the
one :class:`~laplaces_hoard.hoard_link.Link` the app uses — no FastAPI
imports, so it is usable from tests and from ``api.py`` alike.
"""
from __future__ import annotations

import json
import logging
import os
from pathlib import Path
from typing import Any, Mapping, Optional

import httpx

from .hoard_link import Link, LinkConfig
from .hoard_link.atomic import write_text_atomic, update_json

__all__ = [
    "USED_CAPABILITIES", "load_link", "config_error", "save_config", "saved_overrides", "token_set", "app_backends",
]

log = logging.getLogger("laplaces_hoard.backend")

BACKEND_FILE = "backend.json"
USED_CAPABILITIES = ("llm",)


def _backend_path(data_dir: Path) -> Path:
    return Path(data_dir) / BACKEND_FILE


def _read_raw(data_dir: Path) -> dict[str, Any]:
    path = _backend_path(data_dir)
    if not path.is_file():
        return {}
    try:
        raw = json.loads(path.read_text(encoding="utf-8-sig"))
    except ValueError:
        return {}
    return raw if isinstance(raw, dict) else {}


def config_error(data_dir: Path) -> Optional[str]:
    """Why `data/backend.json` cannot be used (hand-edited into invalid JSON, a bad field), or None."""
    path = _backend_path(data_dir)
    if not path.is_file():
        return None
    try:
        LinkConfig.load(path, env={}, app="laplace")
    except ValueError as exc:
        return str(exc)
    return None


def load_link(
    data_dir: Path,
    *,
    env: Optional[Mapping[str, str]] = None,
    client: Optional[httpx.AsyncClient] = None,
) -> Link:
    """Build the app's one `Link`, reading `data/backend.json` when it exists.

    A broken `backend.json` must never stop the app from starting (nothing
    but "Ask your data" needs a model): it is ignored with a warning, the
    environment overrides still apply, and `GET /api/backend` reports it.
    `env`/`client` exist for offline tests.
    """
    path = _backend_path(data_dir)
    env = os.environ if env is None else env
    try:
        config = LinkConfig.load(path if path.is_file() else None, env=env, app="laplace")
    except ValueError as exc:
        log.warning("ignoring %s: %s", path, exc)
        config = LinkConfig.load(None, env=env, app="laplace")
    return Link(config, client=client)


def save_config(
    data_dir: Path,
    *,
    faustus_url: Optional[str] = None,
    faustus_token: Optional[str] = None,
    only_resident: Optional[bool] = None,
    capabilities: Optional[dict[str, dict[str, Any]]] = None,
) -> None:
    """Merge overrides into `data/backend.json`, never discarding what is already there.

    `faustus_url`/`faustus_token` of `""` clears that field; `None` leaves it
    untouched. `capabilities` merges per-capability, e.g.
    `{"llm": {"url": "...", "model": "..."}}`; a value of `None` or `""`
    inside a capability's dict removes that key.
    """
    data_dir = Path(data_dir)
    data_dir.mkdir(parents=True, exist_ok=True)
    def merge(raw):
        raw = dict(raw) if isinstance(raw, dict) else {}
        return _merge_config(raw, faustus_url=faustus_url, faustus_token=faustus_token,
                             only_resident=only_resident, capabilities=capabilities)
    update_json(_backend_path(data_dir), merge, default={})


def _merge_config(raw, *, faustus_url, faustus_token, only_resident, capabilities):

    if only_resident is not None:
        raw["only_resident"] = bool(only_resident)

    if faustus_url is not None or faustus_token is not None:
        faustus = dict(raw.get("faustus") or {})
        if faustus_url is not None:
            if faustus_url:
                faustus["url"] = faustus_url
            else:
                faustus.pop("url", None)
        if faustus_token is not None:
            if faustus_token:
                faustus["token"] = faustus_token
            else:
                faustus.pop("token", None)
        if faustus:
            raw["faustus"] = faustus
        else:
            raw.pop("faustus", None)

    if capabilities:
        caps = dict(raw.get("capabilities") or {})
        for cap, override in capabilities.items():
            entry = dict(caps.get(cap) or {})
            for key, value in override.items():
                if value in (None, ""):
                    entry.pop(key, None)
                else:
                    entry[key] = value
            if entry:
                caps[cap] = entry
            else:
                caps.pop(cap, None)
        if caps:
            raw["capabilities"] = caps
        else:
            raw.pop("capabilities", None)

    # Atomic (temp file + rename, retried on Windows): a crash can never leave a truncated backend.json.
    return raw


def saved_overrides(data_dir: Path) -> dict[str, Any]:
    """The non-secret overrides stored in `data/backend.json`, so the Settings form can show and clear them."""
    raw = _read_raw(data_dir)
    faustus = raw.get("faustus") if isinstance(raw.get("faustus"), dict) else {}
    caps = raw.get("capabilities") if isinstance(raw.get("capabilities"), dict) else {}
    out_caps = {}
    for cap in USED_CAPABILITIES:
        entry = caps.get(cap) if isinstance(caps.get(cap), dict) else {}
        out_caps[cap] = {"url": entry.get("url") or None, "model": entry.get("model") or None}
    return {"faustus_url": faustus.get("url") or None, "capabilities": out_caps}


def token_set(data_dir: Path) -> bool:
    """Whether a Faustus token is stored — the API must never echo the token itself."""
    faustus = _read_raw(data_dir).get("faustus")
    return isinstance(faustus, dict) and bool(faustus.get("token"))


def app_backends() -> dict[str, Any]:
    """Laplace's own backends that need no shared model — always available, bundled with the app."""
    return {
        "sql_engine": {"name": "DuckDB", "bundled": True},
        "chart_renderer": {"name": "vl-convert-python", "bundled": True},
        "computation_worker": {"name": "SymPy / Pint timeout worker", "bundled": True},
    }

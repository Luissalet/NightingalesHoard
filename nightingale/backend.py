"""App-side wiring for the shared model backend (vendored Hoard Link).

Adapted from Laplace's Hoard's `backend.py` (same author, MIT). Nightingale
uses exactly one shared-model capability, ``llm``, for "Ask your data"; every
other feature (ingest, transform, quality, charts, models) never touches a
model and keeps working with nothing resolved.
"""

from __future__ import annotations

import json
import logging
import os
from pathlib import Path
from typing import Any, Mapping, Optional

import httpx

from .hoard_link import Link, LinkConfig

__all__ = ["USED_CAPABILITIES", "load_link", "config_error", "save_config", "saved_overrides", "app_backends"]

log = logging.getLogger("nightingale.backend")

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
    path = _backend_path(data_dir)
    if not path.is_file():
        return None
    try:
        LinkConfig.load(path, env={}, app="nightingale")
    except ValueError as exc:
        return str(exc)
    return None


def load_link(data_dir: Path, *, env: Optional[Mapping[str, str]] = None,
              client: Optional[httpx.AsyncClient] = None) -> Link:
    """A broken backend.json never stops the app: only 'Ask your data' needs a model."""
    path = _backend_path(data_dir)
    env = os.environ if env is None else env
    try:
        config = LinkConfig.load(path if path.is_file() else None, env=env, app="nightingale")
    except ValueError as exc:
        log.warning("ignoring %s: %s", path, exc)
        config = LinkConfig.load(None, env=env, app="nightingale")
    return Link(config, client=client)


def save_config(data_dir: Path, *, faustus_url: Optional[str] = None, faustus_token: Optional[str] = None,
                 only_resident: Optional[bool] = None, capabilities: Optional[dict[str, dict[str, Any]]] = None) -> None:
    data_dir = Path(data_dir)
    data_dir.mkdir(parents=True, exist_ok=True)
    raw = _read_raw(data_dir)
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
    _backend_path(data_dir).write_text(json.dumps(raw, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")


def saved_overrides(data_dir: Path) -> dict[str, Any]:
    raw = _read_raw(data_dir)
    faustus = raw.get("faustus") if isinstance(raw.get("faustus"), dict) else {}
    caps = raw.get("capabilities") if isinstance(raw.get("capabilities"), dict) else {}
    out_caps = {}
    for cap in USED_CAPABILITIES:
        entry = caps.get(cap) if isinstance(caps.get(cap), dict) else {}
        out_caps[cap] = {"url": entry.get("url") or None, "model": entry.get("model") or None}
    return {"faustus_url": faustus.get("url") or None, "capabilities": out_caps}


def app_backends() -> dict[str, Any]:
    import importlib.util

    return {
        "sql_engine": {"name": "DuckDB", "bundled": True},
        "chart_renderer": {"name": "matplotlib (Agg) + Vega-Lite", "bundled": True},
        "model_library": {"name": "scikit-learn", "bundled": True},
        "forecast_library": {"name": "statsmodels", "bundled": importlib.util.find_spec("statsmodels") is not None},
    }

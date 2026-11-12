"""The language the app is configured in, kept in the data folder.

The interface language is a choice of the person using the app (the client keeps it in ``localStorage`` and sends
``?lang=`` with every call). A caller that does not say which language it wants — an agent through MCP, a script
hitting the API, a story built from the command line — gets this one, so a story is never written in English just
because nobody asked. Spanish when nothing has been chosen yet: it is the language of the family.
"""

from __future__ import annotations

import json
import os
from pathlib import Path
from typing import Any, Optional

from .texts import LANGS, norm_lang

DEFAULT_LANG = "es"
FILE_NAME = "app-language.json"

_path: Optional[Path] = None
_lang: str = DEFAULT_LANG


def _read(path: Path) -> Optional[str]:
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return None
    value = data.get("language") if isinstance(data, dict) else None
    return value if isinstance(value, str) and norm_lang(value) in LANGS else None


def configure(data_dir: Path | str) -> str:
    """Read the language of the data folder (``NIGHTINGALE_LANG`` wins) and make it the default. Returns it."""
    global _path, _lang
    _path = Path(data_dir) / FILE_NAME
    env = os.environ.get("NIGHTINGALE_LANG")
    _lang = norm_lang(env) if env else (_read(_path) or DEFAULT_LANG)
    return _lang


def current() -> str:
    return _lang


def set_language(lang: Any) -> str:
    """Store the language the app is configured in (the interface calls this when the person switches)."""
    global _lang
    _lang = norm_lang(lang)
    if _path is not None:
        try:
            _path.parent.mkdir(parents=True, exist_ok=True)
            tmp = _path.with_suffix(".tmp")
            tmp.write_text(json.dumps({"language": _lang}), encoding="utf-8")
            tmp.replace(_path)
        except OSError:
            pass  # a read-only data folder keeps the choice for this run only
    return _lang

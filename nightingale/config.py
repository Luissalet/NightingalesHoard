"""Process-level configuration read from the environment (never from the database)."""

from __future__ import annotations

import os
from dataclasses import dataclass, field
from pathlib import Path

from .guard import parse_allowed_hosts

REPO_ROOT = Path(__file__).resolve().parent.parent
DEFAULT_PORT = 5189


def _env(name: str, default: str = "") -> str:
    return os.environ.get(name, default).strip()


@dataclass
class Config:
    """Everything the process needs before the workbench exists."""

    data_dir: Path = field(default_factory=lambda: REPO_ROOT / "data")
    port: int = DEFAULT_PORT
    port_strict: bool = False
    allowed_hosts: tuple[str, ...] = ()
    data_dir_configured: bool = False
    demo: bool = False
    export_dir: Path | None = None

    @property
    def duckdb_path(self) -> Path:
        return self.data_dir / "workbench.duckdb"

    @property
    def meta_path(self) -> Path:
        return self.data_dir / "nightingale-meta.sqlite"

    @property
    def token_path(self) -> Path:
        return self.data_dir / "mcp-token"

    @property
    def exports_dir(self) -> Path:
        return self.export_dir or (self.data_dir / "exports")

    @property
    def charts_dir(self) -> Path:
        return self.data_dir / "charts"

    @classmethod
    def from_env(cls, demo: bool = False) -> "Config":
        raw_dir = _env("NIGHTINGALE_DATA_DIR")
        port_raw = _env("NIGHTINGALE_PORT") or _env("PORT") or str(DEFAULT_PORT)
        try:
            port = int(port_raw)
        except ValueError:
            port = DEFAULT_PORT
        if not 1 <= port <= 65535:
            port = DEFAULT_PORT
        data_dir = Path(raw_dir).expanduser() if raw_dir else REPO_ROOT / ("data-demo" if demo else "data")
        return cls(
            data_dir=data_dir,
            port=port,
            port_strict=_env("PORT_STRICT") == "1",
            allowed_hosts=parse_allowed_hosts(_env("NIGHTINGALE_ALLOWED_HOSTS")),
            data_dir_configured=bool(raw_dir),
            demo=demo,
        )

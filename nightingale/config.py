"""Process-level configuration read from the environment (never from the database)."""

from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path

from .hoard_link.appconfig import AppPaths, env_flag, env_int, env_str
from .hoard_link.guard import parse_allowed_hosts

REPO_ROOT = Path(__file__).resolve().parent.parent
DEFAULT_PORT = 5189


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
    def paths(self) -> AppPaths:
        """The shared data-folder layout (``mcp-token``, ``url``, ``logs/``, ``backend.json``)."""
        return AppPaths("nightingale", REPO_ROOT, self.data_dir, self.data_dir_configured)

    @property
    def duckdb_path(self) -> Path:
        return self.data_dir / "workbench.duckdb"

    @property
    def meta_path(self) -> Path:
        return self.data_dir / "nightingale-meta.sqlite"

    @property
    def token_path(self) -> Path:
        return self.paths.token_path

    @property
    def url_path(self) -> Path:
        return self.paths.url_path

    @property
    def logs_dir(self) -> Path:
        return self.paths.logs_dir

    @property
    def exports_dir(self) -> Path:
        return self.export_dir or (self.data_dir / "exports")

    @property
    def charts_dir(self) -> Path:
        return self.data_dir / "charts"

    @property
    def lab_models_dir(self) -> Path:
        return self.data_dir / "lab_models"

    @classmethod
    def from_env(cls, demo: bool | None = None) -> "Config":
        """``demo`` is read from ``NIGHTINGALE_DEMO`` when it is not given (``python -m nightingale --demo`` sets it)."""
        if demo is None:
            demo = env_flag("NIGHTINGALE_DEMO")
        raw_dir = env_str("NIGHTINGALE_DATA_DIR") or ""
        port = env_int("NIGHTINGALE_PORT", "PORT", default=DEFAULT_PORT)
        if not 1 <= port <= 65535:
            port = DEFAULT_PORT
        data_dir = Path(raw_dir).expanduser() if raw_dir else REPO_ROOT / ("data-demo" if demo else "data")
        return cls(
            data_dir=data_dir,
            port=port,
            port_strict=env_flag("PORT_STRICT"),
            allowed_hosts=parse_allowed_hosts(env_str("NIGHTINGALE_ALLOWED_HOSTS")),
            data_dir_configured=bool(raw_dir),
            demo=demo,
        )

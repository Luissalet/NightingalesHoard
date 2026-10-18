"""Shared pytest fixtures: a live app on a free port, a data dir with fixtures."""
from __future__ import annotations

import csv
import shutil
import socket
import sys
import threading
import time
from pathlib import Path

import pytest
import uvicorn

REPO_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO_ROOT))


def free_port() -> int:
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
        s.bind(("127.0.0.1", 0))
        return s.getsockname()[1]


@pytest.fixture()
def data_dir(tmp_path: Path) -> Path:
    d = tmp_path / "data"
    d.mkdir()
    return d


@pytest.fixture()
def sample_csv(tmp_path: Path) -> Path:
    path = tmp_path / "sample.csv"
    with path.open("w", newline="", encoding="utf-8") as fh:
        writer = csv.writer(fh)
        writer.writerow(["region", "amount"])
        rows = [
            ("North", 100), ("North", 120), ("South", 80),
            ("South", 90), ("East", 200), ("West", 60),
        ]
        writer.writerows(rows)
    return path


class LiveApp:
    def __init__(self, base_url: str, data_dir: Path, port: int):
        self.base_url = base_url
        self.data_dir = data_dir
        self.port = port


@pytest.fixture()
def live_app(tmp_path: Path):
    """Run the real app (uvicorn.Server in a thread) on a free port for MCP/E2E tests."""
    from laplaces_hoard.api import create_app

    port = free_port()
    data_dir = tmp_path / "app-data"
    data_dir.mkdir()
    app = create_app(data_dir=data_dir, static_dir=None, port=port)
    config = uvicorn.Config(app, host="127.0.0.1", port=port, log_level="warning")
    server = uvicorn.Server(config)
    thread = threading.Thread(target=server.run, daemon=True)
    thread.start()
    deadline = time.monotonic() + 10
    import httpx
    while time.monotonic() < deadline:
        try:
            r = httpx.get(f"http://127.0.0.1:{port}/api/health", timeout=1)
            if r.status_code == 200:
                break
        except httpx.HTTPError:
            pass
        time.sleep(0.1)
    else:
        raise RuntimeError("live app did not become healthy in time")

    yield LiveApp(f"http://127.0.0.1:{port}", data_dir, port)

    server.should_exit = True
    thread.join(timeout=5)


def agent_headers(app) -> dict:
    """The headers the MCP adapter sends: the app requires its bearer token on every /api/agent/<tool> route."""
    token = (app.state.lh.data_dir / "mcp-token").read_text(encoding="utf-8").strip()
    return {"Authorization": f"Bearer {token}"}

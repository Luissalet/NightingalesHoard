"""The shared guard (hoard_link.guard) and the bearer token on the per-tool routes /api/agent/<tool>."""
from __future__ import annotations

import json
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from conftest import agent_headers
from laplaces_hoard import backend as backend_mod
from laplaces_hoard import db
from laplaces_hoard.api import create_app

PORT = 8812


@pytest.fixture()
def app(data_dir: Path):
    return create_app(data_dir=data_dir, static_dir=None, port=PORT)


@pytest.fixture()
def anonymous(app):
    return TestClient(app, base_url=f"http://127.0.0.1:{PORT}")


def test_agent_tool_routes_refuse_a_missing_or_wrong_token(anonymous):
    tools = [t["name"] for t in anonymous.get("/api/agent/tools").json()["tools"]]
    assert len(tools) == 20
    for name in tools:
        r = anonymous.post(f"/api/agent/{name}", json={})
        assert r.status_code == 401, name
        assert r.json()["error"] == "unauthorized"
    r = anonymous.post("/api/agent/calc", json={"expression": "1+1"}, headers={"Authorization": "Bearer nope"})
    assert r.status_code == 401


def test_agent_tool_routes_accept_the_token(app, anonymous):
    r = anonymous.post("/api/agent/calc", json={"expression": "2*21"}, headers=agent_headers(app))
    assert r.status_code == 200 and r.json()["exact"] == "42"
    lowercase = {"Authorization": agent_headers(app)["Authorization"].replace("Bearer", "bearer")}
    assert anonymous.post("/api/agent/calc", json={"expression": "1+1"}, headers=lowercase).status_code == 200


def test_the_ui_routes_stay_open_and_are_not_logged_as_the_assistant(app, anonymous):
    assert anonymous.post("/api/ui/calc", json={"expression": "6*7"}).status_code == 200
    assert anonymous.get("/api/health").status_code == 200
    assert anonymous.get("/api/agent/tools").status_code == 200


def test_the_token_survives_an_app_restart(data_dir: Path):
    first = agent_headers(create_app(data_dir=data_dir, static_dir=None, port=PORT))
    second = agent_headers(create_app(data_dir=data_dir, static_dir=None, port=PORT))
    assert first == second


def test_allowed_hosts_open_a_lan_name_but_ports_stay_strict(data_dir: Path, monkeypatch):
    monkeypatch.setenv("LAPLACE_ALLOWED_HOSTS", "laplace.lan, *.ts.net")
    app = create_app(data_dir=data_dir, static_dir=None, port=PORT)
    c = TestClient(app, base_url=f"http://127.0.0.1:{PORT}")
    assert c.get("/api/health", headers={"Host": "laplace.lan"}).status_code == 200
    assert c.get("/api/health", headers={"Host": "pc.tail9.ts.net"}).status_code == 200
    assert c.get("/api/health", headers={"Host": "127.0.0.1:9"}).status_code == 403
    assert c.get("/api/health", headers={"Host": "evil.example.com"}).status_code == 403


def test_a_page_of_another_site_cannot_frame_the_app(anonymous):
    frame = {"Sec-Fetch-Site": "cross-site", "Sec-Fetch-Mode": "navigate", "Sec-Fetch-Dest": "iframe"}
    assert anonymous.get("/api/health", headers=frame).status_code == 403


def test_the_database_waits_for_a_busy_lock(data_dir: Path):
    conn = db.connect(data_dir)
    assert conn.execute("PRAGMA busy_timeout").fetchone()[0] >= 15000


def test_backend_config_is_written_atomically(data_dir: Path):
    backend_mod.save_config(data_dir, faustus_url="http://127.0.0.1:7001", faustus_token="tok", only_resident=None, capabilities={})
    path = data_dir / "backend.json"
    assert json.loads(path.read_text(encoding="utf-8"))["faustus"]["url"] == "http://127.0.0.1:7001"
    assert [p.name for p in data_dir.iterdir() if p.name.startswith("backend.json")] == ["backend.json"]   # no temp files left


def test_the_bridge_ignores_proxy_environment_variables():
    import importlib.util
    import os

    spec = importlib.util.spec_from_file_location("laplace_bridge_under_test", Path(__file__).resolve().parents[2] / "laplaces_hoard" / "mcp_server.py")
    mod = importlib.util.module_from_spec(spec)
    old = os.environ.get("HTTP_PROXY")
    os.environ["HTTP_PROXY"] = "http://127.0.0.1:9"
    try:
        spec.loader.exec_module(mod)
        assert mod._client._trust_env is False
    finally:
        if old is None:
            os.environ.pop("HTTP_PROXY", None)
        else:
            os.environ["HTTP_PROXY"] = old

"""The shared model backend: GET/PUT /api/backend, recheck, and that the
token is never echoed back by the API even though it is stored on disk."""
from __future__ import annotations

import json
from pathlib import Path

import httpx
import pytest
from fastapi.testclient import TestClient

from laplaces_hoard import backend as backend_mod
from laplaces_hoard.api import create_app
from conftest import agent_headers


def offline_link_factory(data_dir: Path):
    """Links that read data/backend.json like the real app but whose every
    probe (Faustus, llama.cpp, Ollama...) gets a "nothing here" 404 from a
    MockTransport: never a real socket, never the runner's HOARD_* env."""
    def factory():
        client = httpx.AsyncClient(transport=httpx.MockTransport(lambda request: httpx.Response(404, json={})))
        return backend_mod.load_link(data_dir, env={}, client=client)
    return factory


@pytest.fixture()
def client(data_dir: Path) -> TestClient:
    app = create_app(data_dir=data_dir, static_dir=None, port=8812, link_factory=offline_link_factory(data_dir))
    return TestClient(app, base_url="http://127.0.0.1:8812", headers=agent_headers(app))


def test_backend_status_reports_only_the_capabilities_the_app_uses(client: TestClient):
    r = client.get("/api/backend")
    assert r.status_code == 200
    body = r.json()
    assert set(body["capabilities"]) == {"llm"}
    assert body["capabilities"]["llm"]["state"] == "unavailable"
    assert body["config"]["token_set"] is False
    assert "sql_engine" in body["app"]


def test_backend_config_persists_without_leaking_the_token(client: TestClient, data_dir: Path):
    r = client.put(
        "/api/backend/config",
        json={
            "faustus_url": "http://127.0.0.1:7999",
            "faustus_token": "ody_secret_value",
            "capabilities": {"llm": {"url": "http://127.0.0.1:9999/v1", "model": "test-model"}},
        },
    )
    assert r.status_code == 200
    body = r.json()
    assert body["ok"] is True
    assert body["token_set"] is True
    assert "ody_secret_value" not in json.dumps(body)

    on_disk = json.loads((data_dir / "backend.json").read_text(encoding="utf-8"))
    assert on_disk["faustus"]["token"] == "ody_secret_value"
    assert on_disk["faustus"]["url"] == "http://127.0.0.1:7999"
    assert on_disk["capabilities"]["llm"]["model"] == "test-model"

    status = client.get("/api/backend").json()
    assert status["config"]["token_set"] is True
    assert "ody_secret_value" not in json.dumps(status)
    # the new explicit capability config now resolves without any probing
    assert status["capabilities"]["llm"]["state"] == "resolved"
    assert status["capabilities"]["llm"]["model"] == "test-model"


def test_backend_config_clears_a_field_with_an_empty_string(client: TestClient, data_dir: Path):
    client.put("/api/backend/config", json={"faustus_token": "ody_secret"})
    assert client.get("/api/backend").json()["config"]["token_set"] is True
    client.put("/api/backend/config", json={"faustus_token": ""})
    assert client.get("/api/backend").json()["config"]["token_set"] is False
    on_disk = json.loads((data_dir / "backend.json").read_text(encoding="utf-8"))
    assert "token" not in on_disk.get("faustus", {})


def test_backend_recheck_returns_the_used_capabilities(client: TestClient):
    r = client.post("/api/backend/recheck")
    assert r.status_code == 200
    assert set(r.json()) == {"llm"}


def test_backend_status_shows_saved_overrides_so_they_can_be_cleared(client: TestClient):
    client.put("/api/backend/config", json={
        "faustus_url": "http://127.0.0.1:7999", "faustus_token": "ody_secret_value",
        "capabilities": {"llm": {"url": "http://127.0.0.1:9999/v1", "model": "test-model"}},
    })
    saved = client.get("/api/backend").json()["config"]["saved"]
    assert saved == {
        "faustus_url": "http://127.0.0.1:7999",
        "capabilities": {"llm": {"url": "http://127.0.0.1:9999/v1", "model": "test-model"}},
    }
    # "" clears an override; the llm then falls back to probing (and finds nothing)
    client.put("/api/backend/config", json={"capabilities": {"llm": {"url": "", "model": ""}}})
    status = client.get("/api/backend").json()
    assert status["config"]["saved"]["capabilities"]["llm"] == {"url": None, "model": None}
    assert status["capabilities"]["llm"]["state"] == "unavailable"
    assert "ody_secret_value" not in json.dumps(status)


@pytest.mark.parametrize("body", [
    {"capabilities": {"llm": {"command": "not-a-list"}}},  # would make backend.json unloadable
    {"capabilities": {"tts": {"command": ["anything"]}}},  # a capability this app does not use
    {"capabilities": {"llm": {"url": 5}}},
    {"unknown_field": True},
])
def test_backend_config_rejects_what_the_form_never_sends(client: TestClient, data_dir: Path, body: dict):
    r = client.put("/api/backend/config", json=body)
    assert r.status_code == 422
    assert r.json()["error"] == "invalid_arguments"
    assert not (data_dir / "backend.json").exists()


def test_a_broken_backend_json_does_not_stop_the_app(data_dir: Path):
    (data_dir / "backend.json").write_text("{ not json", encoding="utf-8")
    app = create_app(data_dir=data_dir, static_dir=None, port=8812, link_factory=offline_link_factory(data_dir))
    client = TestClient(app, base_url="http://127.0.0.1:8812", headers=agent_headers(app))
    assert client.get("/api/health").status_code == 200
    body = client.get("/api/backend").json()
    assert "not valid JSON" in body["config"]["error"]
    assert body["capabilities"]["llm"]["state"] == "unavailable"
    # saving from the Settings form replaces the broken file with a valid one
    assert client.put("/api/backend/config", json={"capabilities": {"llm": {"url": "http://127.0.0.1:9999/v1"}}}).status_code == 200
    body = client.get("/api/backend").json()
    assert body["config"]["error"] is None
    assert body["capabilities"]["llm"]["state"] == "resolved"


def test_load_link_ignores_a_broken_config_but_keeps_env_overrides(data_dir: Path):
    (data_dir / "backend.json").write_text('{"capabilities": {"llm": {"command": "x"}}}', encoding="utf-8")
    link = backend_mod.load_link(data_dir, env={"HOARD_LLM_URL": "http://127.0.0.1:9999/v1"})
    assert link.config.capability("llm").url == "http://127.0.0.1:9999/v1"
    assert "command must be a list" in backend_mod.config_error(data_dir)

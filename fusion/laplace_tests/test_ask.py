"""'Ask your data': schema goes to the `llm` capability, the SQL it answers
with runs through the normal read-only gate, one retry on a failing query,
and the feature stays honestly unavailable with no model resolved. Every
call here is offline: the Link is backed by `httpx.MockTransport`, never a
real socket."""
from __future__ import annotations

import json
from pathlib import Path

import httpx
import pytest
from fastapi.testclient import TestClient

from laplaces_hoard.api import create_app
from laplaces_hoard.hoard_link import CapabilityConfig, Link, LinkConfig
from conftest import agent_headers


def _openai_chat_response(content: str) -> httpx.Response:
    return httpx.Response(200, json={"choices": [{"message": {"content": content}}]})


def _mock_link(handler) -> Link:
    config = LinkConfig(
        app="laplace",
        capabilities={"llm": CapabilityConfig(url="http://fake-llm/v1/chat/completions", model="test-model", api="openai")},
    )
    client = httpx.AsyncClient(transport=httpx.MockTransport(handler))
    return Link(config, client=client)


@pytest.fixture()
def client_with_link(data_dir: Path):
    def make(handler):
        app = create_app(data_dir=data_dir, static_dir=None, port=8812, link_factory=lambda: _mock_link(handler))
        return TestClient(app, base_url="http://127.0.0.1:8812", headers=agent_headers(app))
    return make


@pytest.fixture()
def registered(data_dir: Path):
    # sample_csv from conftest.py needs a live client to register through;
    # duplicate the tiny registration call once a TestClient exists instead.
    def register(client: TestClient, csv_path: Path):
        r = client.post("/api/agent/data_register", json={"path": str(csv_path), "name": "sample"})
        assert r.status_code == 200, r.text
    return register


def test_ask_returns_query_result_and_chart_suggestion(client_with_link, registered, sample_csv: Path):
    def handler(request: httpx.Request) -> httpx.Response:
        return _openai_chat_response(
            "```sql\nSELECT region, SUM(amount) AS total FROM sample GROUP BY region\n```"
        )

    client = client_with_link(handler)
    registered(client, sample_csv)

    r = client.post("/api/ui/data_ask", json={"question": "total amount per region", "datasets": ["sample"]})
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["sql"].strip().upper().startswith("SELECT")
    assert body["model"] == "test-model"
    assert body["id"].startswith("L-")
    assert {c["name"] for c in body["columns"]} == {"region", "total"}
    assert body["chart_suggestion"] == {"kind": "bar", "x": "region", "y": "total"}

    # logged in the work log with engine="data", operation="ask"
    log_item = client.get(f"/api/log/{body['id']}").json()
    assert log_item["engine"] == "data"
    assert log_item["operation"] == "ask"
    assert log_item["source"] == "ui"


def test_ask_sends_schema_and_at_most_five_sample_rows(client_with_link, registered, sample_csv: Path):
    prompts: list[str] = []

    def handler(request: httpx.Request) -> httpx.Response:
        prompts.append(json.loads(request.content)["messages"][-1]["content"])
        return _openai_chat_response("```sql\nSELECT COUNT(*) AS n FROM sample\n```")

    client = client_with_link(handler)
    registered(client, sample_csv)
    assert client.post("/api/ui/data_ask", json={"question": "how many rows", "datasets": ["sample"]}).status_code == 200

    (prompt,) = prompts
    assert 'Dataset "sample" (6 rows)' in prompt
    assert "region: VARCHAR" in prompt and "amount: BIGINT" in prompt
    assert "Question: how many rows" in prompt
    # sample.csv has 6 rows; its 6th ("West | 60") must never reach the model
    assert "North | 100" in prompt and "West | 60" not in prompt


def test_ask_retries_once_after_a_failing_query(client_with_link, registered, sample_csv: Path):
    calls = {"n": 0}
    prompts: list[str] = []

    def handler(request: httpx.Request) -> httpx.Response:
        calls["n"] += 1
        prompts.append(json.loads(request.content)["messages"][-1]["content"])
        if calls["n"] == 1:
            return _openai_chat_response("```sql\nSELECT nonexistent_column FROM sample\n```")
        return _openai_chat_response("```sql\nSELECT COUNT(*) AS n FROM sample\n```")

    client = client_with_link(handler)
    registered(client, sample_csv)

    r = client.post("/api/ui/data_ask", json={"question": "how many rows", "datasets": ["sample"]})
    assert r.status_code == 200, r.text
    assert calls["n"] == 2
    assert r.json()["rows"][0]["n"] == 6
    # the retry tells the model which query failed and why
    assert "SELECT nonexistent_column FROM sample" in prompts[1]
    assert "nonexistent_column" in prompts[1].split("Error:", 1)[1]


def test_ask_reports_a_second_failure_after_the_retry(client_with_link, registered, sample_csv: Path):
    def handler(request: httpx.Request) -> httpx.Response:
        return _openai_chat_response("```sql\nSELECT nonexistent_column FROM sample\n```")

    client = client_with_link(handler)
    registered(client, sample_csv)

    r = client.post("/api/ui/data_ask", json={"question": "anything", "datasets": ["sample"]})
    assert r.status_code == 400
    assert "nonexistent_column" in r.json()["message"] or "column" in r.json()["message"].lower()


def test_ask_errors_clearly_when_the_model_does_not_answer_in_sql(client_with_link, registered, sample_csv: Path):
    def handler(request: httpx.Request) -> httpx.Response:
        return _openai_chat_response("I cannot help with that.")

    client = client_with_link(handler)
    registered(client, sample_csv)

    r = client.post("/api/ui/data_ask", json={"question": "??", "datasets": ["sample"]})
    assert r.status_code == 400
    assert r.json()["error"] == "ask"


def test_ask_is_honestly_unavailable_with_no_model_resolved(data_dir: Path, sample_csv: Path):
    def handler(request: httpx.Request) -> httpx.Response:
        # every probe (Faustus, llama.cpp, Ollama, another OpenAI-compatible
        # server...) gets a
        # uniform "nothing here" 404 - never a real socket, still offline.
        return httpx.Response(404, json={})

    def factory() -> Link:
        return Link(LinkConfig.load(None, env={}, app="laplace"), client=httpx.AsyncClient(transport=httpx.MockTransport(handler)))

    app = create_app(data_dir=data_dir, static_dir=None, port=8812, link_factory=factory)
    client = TestClient(app, base_url="http://127.0.0.1:8812", headers=agent_headers(app))
    r = client.post("/api/agent/data_register", json={"path": str(sample_csv), "name": "sample"})
    assert r.status_code == 200

    r = client.post("/api/ui/data_ask", json={"question": "anything", "datasets": ["sample"]})
    assert r.status_code == 400
    body = r.json()
    assert "no language model is available" in body["message"]


def test_ask_with_no_datasets_registered_is_a_clear_error(client_with_link):
    def handler(request: httpx.Request) -> httpx.Response:
        raise AssertionError("must not call the model before datasets exist")

    client = client_with_link(handler)
    r = client.post("/api/ui/data_ask", json={"question": "anything"})
    assert r.status_code == 400
    assert "no datasets" in r.json()["message"]

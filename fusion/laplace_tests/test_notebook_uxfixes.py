"""Regression from the first user walk (docs/USABILITY_REPORT.md): a failed
notebook cell used to come back as a raw, unhandled 500 (an uncaught
ValueError from parsing "order"), which the notebook UI could not show at
all - the cell simply never appeared, and reappeared empty after reload."""
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from laplaces_hoard.api import create_app
from conftest import agent_headers


@pytest.fixture()
def client(data_dir: Path) -> TestClient:
    app = create_app(data_dir=data_dir, static_dir=None, port=8812)
    return TestClient(app, base_url="http://127.0.0.1:8812", raise_server_exceptions=False, headers=agent_headers(app))


def test_a_bad_math_cell_never_comes_back_as_a_raw_500(client: TestClient):
    resp = client.post("/api/cells", json={"engine": "math", "input": "diff(x**2, x, dos)"})
    assert resp.status_code == 200
    body = resp.json()
    assert body["result"]["error"]
    assert "dos" in body["result"]["message"] or "whole number" in body["result"]["message"]
    # and it is visible on reload, not an empty card
    listed = client.get("/api/cells").json()["cells"]
    assert listed[-1]["result"] is not None


def test_a_good_math_cell_with_an_order_still_works(client: TestClient):
    resp = client.post("/api/cells", json={"engine": "math", "input": "diff(x**2, x, 2)"})
    assert resp.status_code == 200
    body = resp.json()
    assert "error" not in (body["result"] or {})


@pytest.mark.parametrize("engine,text,code,fragment", [
    ("calc", "3,5 + 2", "unsafe_expression", "use '.' instead of ','"),
    ("math", "x^2 - 5x + 6 = 0", "unsafe_expression", "5*x"),
    ("calc", "1/0", None, None),
])
def test_an_ordinary_engine_error_is_shown_as_itself_not_as_an_unexpected_failure(client, engine, text, code, fragment):
    # the second walk showed every calc/math mistake as "unexpected failure
    # evaluating this cell: HTTPException: 400: {...}" - the raw wrapper
    body = client.post("/api/cells", json={"engine": engine, "input": text}).json()
    result = body["result"]
    assert result["error"] != "internal_error"
    assert "unexpected failure" not in result["message"]
    assert "HTTPException" not in result["message"]
    if code:
        assert result["error"] == code
    if fragment:
        assert fragment in result["message"]

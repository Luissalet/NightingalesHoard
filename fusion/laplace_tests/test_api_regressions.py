"""Regressions found in review: static-file escape, error envelope, 500s."""
import http.client
import threading
import time
from pathlib import Path

import pytest
import uvicorn
from fastapi.testclient import TestClient

from laplaces_hoard.api import create_app
from conftest import agent_headers

from conftest import free_port


@pytest.fixture()
def client(data_dir: Path) -> TestClient:
    app = create_app(data_dir=data_dir, static_dir=None, port=8812)
    return TestClient(app, base_url="http://127.0.0.1:8812", raise_server_exceptions=False, headers=agent_headers(app))


@pytest.fixture()
def static_server(tmp_path: Path):
    """A real uvicorn server (raw paths must reach the router undecoded by a client)."""
    static = tmp_path / "dist"
    (static / "assets").mkdir(parents=True)
    (static / "index.html").write_text("INDEX", encoding="utf-8")
    (static / "favicon.svg").write_text("<svg/>", encoding="utf-8")
    (tmp_path / "secret.txt").write_text("SECRET", encoding="utf-8")
    port = free_port()
    app = create_app(data_dir=tmp_path / "data", static_dir=static, port=port)
    server = uvicorn.Server(uvicorn.Config(app, host="127.0.0.1", port=port, log_level="warning"))
    thread = threading.Thread(target=server.run, daemon=True)
    thread.start()
    deadline = time.monotonic() + 10
    while not server.started and time.monotonic() < deadline:
        time.sleep(0.05)
    yield port
    server.should_exit = True
    thread.join(timeout=5)


def _raw_get(port: int, path: str) -> tuple[int, bytes]:
    conn = http.client.HTTPConnection("127.0.0.1", port, timeout=5)
    conn.putrequest("GET", path, skip_host=True)
    conn.putheader("Host", f"127.0.0.1:{port}")
    conn.endheaders()
    r = conn.getresponse()
    return r.status, r.read()


@pytest.mark.parametrize(
    "path",
    ["/..%2fsecret.txt", "/%2e%2e/secret.txt", "//etc/hostname", "/%2Fetc%2Fhostname", "/..%5csecret.txt"],
)
def test_spa_fallback_never_serves_files_outside_the_frontend(static_server, path):
    status, body = _raw_get(static_server, path)
    assert b"SECRET" not in body
    assert status == 200 and body.startswith(b"INDEX")
    assert body.count(b"/api/copilot/launcher.js") == 1


def test_spa_still_serves_its_own_files(static_server):
    assert _raw_get(static_server, "/favicon.svg") == (200, b"<svg/>")
    assert _raw_get(static_server, "/data")[1].startswith(b"INDEX")
    assert _raw_get(static_server, "/data")[1].count(b"/api/copilot/launcher.js") == 1
    status, body = _raw_get(static_server, "/api/nope")
    assert status == 404 and b"not_found" in body


def test_engine_errors_use_the_flat_error_envelope(client):
    r = client.post("/api/agent/calc", json={"expression": "__import__('os')"})
    assert r.status_code == 400
    body = r.json()
    assert set(body) == {"error", "message"}


def test_validation_errors_use_the_flat_error_envelope(client):
    r = client.post("/api/agent/calc", json={"expr": "1"})
    assert r.status_code == 422
    body = r.json()
    assert body["error"] == "invalid_arguments"
    assert "expression" in body["message"]


def test_sql_error_is_a_400_with_the_duckdb_message(client, sample_csv):
    client.post("/api/agent/data_register", json={"path": str(sample_csv), "name": "sample"})
    r = client.post("/api/agent/data_query", json={"sql": "SELECT nope FROM sample"})
    assert r.status_code == 400
    assert "nope" in r.json()["message"]


def test_register_after_query_through_the_api(client, sample_csv, tmp_path):
    client.post("/api/agent/data_register", json={"path": str(sample_csv), "name": "sample"})
    client.post("/api/agent/data_query", json={"sql": "SELECT 1"})
    r = client.post("/api/agent/data_register", json={"path": str(sample_csv), "name": "sample_again"})
    assert r.status_code == 200, r.text


def test_app_writes_a_rotating_log(client, data_dir):
    client.post("/api/agent/calc", json={"expression": "1/0"})
    assert (data_dir / "logs" / "app.log").exists()


def test_calc_runs_in_the_worker_with_a_timeout(client):
    r = client.post("/api/agent/calc", json={"expression": "nextprime(10**3000)"})
    assert r.status_code == 400
    assert r.json()["error"] == "calc"
    assert "did not finish" in r.json()["message"]
    assert client.post("/api/agent/calc", json={"expression": "2^10"}).json()["exact"] == "1024"


def test_math_accepts_numeric_matrices_and_caps_timeout(client):
    r = client.post("/api/agent/math", json={"operation": "matrix", "matrix_op": "det", "matrix": [[1, 2], [3, 4.5]]})
    assert r.status_code == 200, r.text
    assert r.json()["result"] == "-3/2"
    r = client.post("/api/agent/math", json={"operation": "simplify", "expression": "x", "timeout": 1e9})
    assert r.status_code == 422


def test_math_infers_the_only_variable(client):
    r = client.post("/api/agent/math", json={"operation": "diff", "expression": "x**3"})
    assert r.status_code == 200, r.text
    assert r.json()["result"] == "3*x**2"
    r = client.post("/api/agent/math", json={"operation": "diff", "expression": "x*y"})
    assert r.status_code == 400 and "variable" in r.json()["message"]


def test_solve_reports_real_roots_as_floats(client):
    r = client.post("/api/agent/math", json={"operation": "solve", "expression": "x**3 - 3*x + 1 = 0"})
    body = r.json()
    assert body["verified"] is True and body["count"] == 3
    roots = sorted(sol["numeric"]["x"] for sol in body["result"])
    assert all(isinstance(v, float) for v in roots)
    assert roots[0] == pytest.approx(-1.879385241571817)


def test_notebook_math_cells_understand_operation_shorthand(client):
    cell = client.post("/api/cells", json={"engine": "math", "input": "factor(x**3 - x)"}).json()
    assert cell["result"]["result"] == "x*(x - 1)*(x + 1)"
    cell = client.post("/api/cells", json={"engine": "math", "input": "x**2 = 9"}).json()
    assert cell["result"]["count"] == 2
    cell = client.post("/api/cells", json={"engine": "units", "input": "5 ft 11 in to cm"}).json()
    assert cell["result"]["to_magnitude"] == pytest.approx(180.34)


def test_rerun_of_a_units_check_reruns_the_check(client):
    first = client.post("/api/units/check", json={"expression": "3 m/s * 2 s"}).json()
    again = client.post(f"/api/log/{first['id']}/rerun").json()
    assert again["dimensionality"] == "[length]"


def test_ui_routes_are_not_logged_as_the_assistant(client):
    # the web UI used to call /api/agent/*, so the human's own clicks showed up
    # under "Assistant activity"
    ui = client.post("/api/ui/calc", json={"expression": "1+1"}).json()
    agent = client.post("/api/agent/calc", json={"expression": "2+2"}).json()
    agent_ids = {i["id"] for i in client.get("/api/agent-calls").json()["items"]}
    assert agent["id"] in agent_ids and ui["id"] not in agent_ids
    assert client.get(f"/api/log/{ui['id']}").json()["source"] == "ui"


def test_agent_chart_result_is_compact_and_log_stays_valid(client, sample_csv):
    client.post("/api/agent/data_register", json={"path": str(sample_csv), "name": "sample"})
    body = {"sql": "SELECT region, SUM(amount) AS total FROM sample GROUP BY region", "kind": "bar",
            "x": "region", "y": "total"}
    agent = client.post("/api/agent/data_chart", json=body).json()
    # no image unless asked: an unrequested image can crash a text-only model
    assert "spec" not in agent and "png_base64" not in agent
    assert agent["spec_summary"]["encoding"] == {"x": "region", "y": "total"}
    with_image = client.post("/api/agent/data_chart", json={**body, "include_image": True}).json()
    assert with_image["png_base64"]
    ui = client.post("/api/ui/data_chart", json=body).json()
    assert ui["spec"]["data"]["values"]
    assert "png_base64" not in ui  # the UI renders the spec, it never needed the PNG either
    logged = client.get(f"/api/log/{agent['id']}").json()
    assert logged["output"] is not None and "png_base64" not in logged["output"]
    assert logged["chart_path"].endswith(".png")


def test_chart_image_is_servable_by_id_even_without_include_image(client, sample_csv):
    # the Work log can show the chart a model made even when the model call
    # itself did not ask for the image back
    client.post("/api/agent/data_register", json={"path": str(sample_csv), "name": "sample"})
    body = {"sql": "SELECT region, SUM(amount) AS total FROM sample GROUP BY region", "kind": "bar",
            "x": "region", "y": "total"}
    agent = client.post("/api/agent/data_chart", json=body).json()
    resp = client.get(f"/api/charts/{agent['id']}")
    assert resp.status_code == 200
    assert resp.headers["content-type"] == "image/png"
    assert resp.content[:8] == b"\x89PNG\r\n\x1a\n"

    missing = client.get("/api/charts/L-999999")
    assert missing.status_code == 404

    non_chart = client.post("/api/agent/calc", json={"expression": "1+1"}).json()
    assert client.get(f"/api/charts/{non_chart['id']}").status_code == 404


def test_saved_report_reruns_query_and_chart_on_current_data(client, sample_csv):
    client.post("/api/agent/data_register", json={"path": str(sample_csv), "name": "sample"})
    body = {
        "title": "Ventas por región", "question": "¿Qué región vendió más?",
        "sql": "SELECT region, SUM(amount) AS total FROM sample GROUP BY region ORDER BY total DESC",
        "chart": {"kind": "bar", "x": "region", "y": "total"},
    }
    created = client.post("/api/agent/data_report", json=body)
    assert created.status_code == 200, created.text
    original = created.json()
    assert original["rows"][0] == {"region": "North", "total": 220}
    assert client.get(f"/api/charts/{original['id']}").content[:8] == b"\x89PNG\r\n\x1a\n"
    logged = client.get(f"/api/log/{original['id']}").json()
    assert logged["engine"] == "report" and logged["input"]["sql"] == body["sql"]

    sample_csv.write_text("region,amount\nNorth,100\nSouth,400\n", encoding="utf-8")
    client.post("/api/agent/data_register", json={"path": str(sample_csv), "name": "sample"})
    refreshed = client.post("/api/agent/report_rerun", json={"id": original["id"]})
    assert refreshed.status_code == 200, refreshed.text
    current = refreshed.json()
    assert current["id"] != original["id"]
    assert current["rows"][0] == {"region": "South", "total": 400}
    assert client.get(f"/api/charts/{current['id']}").content[:8] == b"\x89PNG\r\n\x1a\n"
    assert client.get(f"/api/log/{original['id']}").json()["output"]["rows"][0]["total"] == 220


def test_data_list_for_the_agent_is_compact(client, sample_csv):
    client.post("/api/agent/data_register", json={"path": str(sample_csv), "name": "sample"})
    ds = client.post("/api/agent/data_list").json()["datasets"]
    assert ds == [{"name": "sample", "kind": "csv", "row_count": 6, "column_count": 2,
                   "columns": ["region", "amount"], "source_path": str(sample_csv.resolve())}]


def test_work_log_items_are_short_and_lookup_by_id_is_full(client):
    first = client.post("/api/agent/calc", json={"expression": "factorial(400)"}).json()
    log = client.post("/api/agent/work_log", json={"limit": 5}).json()
    item = log["items"][0]
    assert item["id"] == first["id"] and item["cite"] == first["cite"]
    assert len(item["result"]) <= 301
    full = client.post("/api/agent/work_log", json={"query": f"[{first['id']}]"}).json()
    assert full["count"] == 1 and len(full["items"][0]["result"]) > 800


def test_oversized_log_output_is_stored_as_valid_json(data_dir):
    from laplaces_hoard import db

    conn = db.connect(data_dir)
    cid = db.log_computation(conn, engine="x", operation="y", input_data={"a": 1},
                             output_data={"blob": "z" * 50000}, ok=True, error=None,
                             elapsed_ms=1.0, source="ui")
    item = db.get_computation(conn, cid)
    assert item["output"]["truncated"] is True


def test_csv_export_returns_the_whole_result(client, tmp_path):
    import csv as _csv

    path = tmp_path / "big.csv"
    with path.open("w", newline="", encoding="utf-8") as fh:
        w = _csv.writer(fh)
        w.writerow(["n", "city"])
        for i in range(1500):
            w.writerow([i, "Málaga"])
    client.post("/api/ui/data_register", json={"path": str(path)})
    r = client.post("/api/export/csv", json={"sql": "SELECT * FROM big ORDER BY n"})
    assert r.status_code == 200
    text = r.content.decode("utf-8-sig")
    lines = text.strip().split("\n")
    assert lines[0] == "n,city" and len(lines) == 1501 and lines[-1] == "1499,Málaga"
    bad = client.post("/api/export/csv", json={"sql": "COPY big TO 'x.csv'"})
    assert bad.status_code == 400 and bad.json()["error"] == "sql_gate"


def test_csv_export_uses_spanish_excel_conventions_when_asked(client, tmp_path):
    import csv as _csv

    path = tmp_path / "t.csv"
    with path.open("w", newline="", encoding="utf-8") as fh:
        w = _csv.writer(fh)
        w.writerow(["n", "amount"])
        w.writerow([1, 1234.5])
    client.post("/api/ui/data_register", json={"path": str(path)})
    r = client.post("/api/export/csv", json={"sql": "SELECT * FROM t", "lang": "es"})
    assert r.status_code == 200
    text = r.content.decode("utf-8-sig")
    lines = text.strip().split("\n")
    assert lines[0] == "n;amount"
    assert lines[1] == "1;1234,5"


def test_logged_inputs_skip_defaults_and_still_rerun(client):
    s = client.post("/api/agent/stats", json={"test": "describe", "data": [1, 2, 3, 4]}).json()
    d = client.post("/api/agent/date_calc", json={"operation": "business_days", "start": "2026-09-21",
                                                 "end": "2026-09-25"}).json()
    logged = client.get(f"/api/log/{d['id']}").json()["input"]
    assert logged == {"operation": "business_days", "start": "2026-09-21", "end": "2026-09-25"}
    assert client.post(f"/api/log/{s['id']}/rerun").json()["mean"] == 2.5
    assert client.post(f"/api/log/{d['id']}/rerun").json()["business_days"] == 5


def test_spanish_csv_export_also_writes_exact_decimals_with_a_comma(client, tmp_path):
    # a bank amount read as DECIMAL comes back as text "-1150.00"; the
    # Spanish export must still write it as a number Excel ES understands
    path = tmp_path / "banco.csv"
    path.write_text("Concepto;Importe\nALQUILER;-1.150,00\nCOMPRA;-51,05\n", encoding="utf-8")
    client.post("/api/ui/data_register", json={"path": str(path)})
    r = client.post("/api/export/csv", json={"sql": "SELECT * FROM banco ORDER BY Importe", "lang": "es"})
    assert r.status_code == 200
    lines = r.content.decode("utf-8-sig").strip().split("\n")
    assert lines == ["Concepto;Importe", "ALQUILER;-1150,00", "COMPRA;-51,05"]

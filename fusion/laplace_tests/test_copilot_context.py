import json
from concurrent.futures import ThreadPoolExecutor

import httpx
from fastapi.testclient import TestClient

from laplaces_hoard import backend, db
from laplaces_hoard.api import create_app
from conftest import agent_headers


def client_for(data_dir):
    def link():
        return backend.load_link(data_dir, env={}, client=httpx.AsyncClient(
            transport=httpx.MockTransport(lambda request: httpx.Response(404, json={}))))
    app = create_app(data_dir, port=8812, link_factory=link)
    return TestClient(app, base_url="http://127.0.0.1:8812", headers=agent_headers(app))


def test_context_missing_new_and_unknown_do_not_create_objects(data_dir):
    with client_for(data_dir) as client:
        for kind, object_id in [("cell", "new"), ("cell", "999999"), ("cell", "not-an-id"),
                                ("dataset", "missing"), ("computation", "L-missing"), ("unknown", "2")]:
            result = client.post("/api/ui/app_context", json={"kind": kind, "object_id": object_id})
            assert result.status_code == 200
            assert result.json()["active_objects"] == []
            assert result.json()["project"] is None
        assert client.get("/api/cells").json()["cells"] == []
        assert client.get("/api/datasets").json()["datasets"] == []


def test_native_cell_tools_and_context_preserve_other_cells(data_dir):
    with client_for(data_dir) as client:
        a = client.post("/api/agent/notebook_cell_create", json={"engine": "calc", "input": "1+2"}).json()
        b = client.post("/api/agent/notebook_cell_create", json={"engine": "calc", "input": "7+8"}).json()
        assert a["result"]["exact"] == "3" and b["result"]["exact"] == "15"
        changed = client.post("/api/agent/notebook_cell_patch", json={"id": a["id"], "patch": {"input": "8+9"}}).json()
        assert changed["result"]["exact"] == "17"
        assert client.post("/api/agent/notebook_cell_get", json={"id": b["id"]}).json()["input"] == "7+8"
        ctx = client.post("/api/ui/app_context", json={"kind": "cell", "object_id": str(a["id"])}).json()
        assert ctx["active_objects"][0]["object_id"] == str(a["id"])
        assert ctx["active_objects"][0]["kind"] == "cell"
        deleted = client.post("/api/agent/notebook_cell_delete", json={"id": a["id"]})
        assert deleted.json()["deleted"] is True
        assert [c["id"] for c in client.post("/api/ui/notebook_cells", json={}).json()["cells"]] == [b["id"]]


def test_patch_preserves_later_engine_and_stale_evaluation_cannot_replace_result(data_dir):
    conn = db.connect(data_dir)
    cell = db.add_cell(conn, engine="calc", input_text="old")
    agent_edit = db.patch_cell(conn, cell["id"], {"engine": "math"})
    user_edit = db.patch_cell(conn, cell["id"], {"input": "new"})
    assert user_edit["engine"] == "math" and user_edit["input"] == "new"
    db.finish_cell(conn, user_edit, {"exact": "current"})
    late = db.finish_cell(conn, agent_edit, {"exact": "stale"})
    assert late["result"] == {"exact": "current"}
    conn.close()


def test_invalid_cell_evaluation_keeps_returned_cell_id_and_error(data_dir):
    with client_for(data_dir) as client:
        result = client.post("/api/agent/notebook_cell_create", json={"engine": "math", "input": "?"})
        assert result.status_code == 200
        cell = result.json()
        assert isinstance(cell["id"], int) and cell["result"]["error"]
        saved = client.post("/api/ui/notebook_cell_get", json={"id": cell["id"]}).json()
        assert saved["result"] == cell["result"]


def test_backend_partial_merge_keeps_concurrent_sibling_fields_and_unrelated_config(data_dir):
    path = data_dir / "backend.json"
    path.write_text(json.dumps({"other": {"keep": 1}}), encoding="utf-8")
    with ThreadPoolExecutor(max_workers=2) as pool:
        futures = [
            pool.submit(backend.save_config, data_dir, capabilities={"llm": {"url": "http://127.0.0.1:1"}}),
            pool.submit(backend.save_config, data_dir, capabilities={"llm": {"model": "fixture-model"}}),
        ]
        for future in futures:
            future.result()
    raw = json.loads(path.read_text("utf-8"))
    assert raw["other"] == {"keep": 1}
    assert raw["capabilities"]["llm"] == {"url": "http://127.0.0.1:1", "model": "fixture-model"}


def test_computation_read_and_context_are_exact_without_rerunning(data_dir):
    with client_for(data_dir) as client:
        calc = client.post("/api/agent/calc", json={"expression": "0.1+0.2"}).json()
        result = client.post("/api/agent/computation_get", json={"id": calc["id"]}).json()
        assert result["output"]["exact"] == "3/10"
        context = client.post("/api/ui/app_context", json={"kind": "computation", "object_id": calc["id"]}).json()
        assert context["active_objects"][0]["object_id"] == calc["id"]
        assert client.get("/api/log/" + calc["id"]).json()["output"]["exact"] == "3/10"


def test_context_and_reads_do_not_change_count_history_or_last_computation(data_dir):
    with client_for(data_dir) as client:
        cell = client.post("/api/agent/notebook_cell_create", json={"engine": "calc", "input": "4+5"}).json()
        computation_id = cell["result"]["id"]
        before = client.get("/api/log").json()
        health_before = client.get("/api/health").json()["computations_logged"]
        for _ in range(2):
            for path, payload in [
                ("app_context", {"kind": "cell", "object_id": str(cell["id"])}),
                ("app_context", {"kind": "cell", "object_id": "missing"}),
                ("notebook_cells", {}),
                ("notebook_cell_get", {"id": cell["id"]}),
                ("computation_get", {"id": computation_id}),
            ]:
                assert client.post("/api/ui/" + path, json=payload).status_code == 200
                assert client.post("/api/agent/" + path, json=payload).status_code == 200
        assert client.get("/api/log").json() == before
        assert client.get("/api/health").json()["computations_logged"] == health_before

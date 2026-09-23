"""REST API smoke test: ingest -> transform -> quality -> chart -> export through HTTP."""


def test_full_rest_flow(client, tmp_path):
    csv_path = tmp_path / "api.csv"
    csv_path.write_text("a,b\n1,2\n3,4\n5,6\n", encoding="utf-8")

    r = client.post("/api/sources/ingest", json={"kind": "file", "path": str(csv_path), "name": "api_ds"})
    assert r.status_code == 200, r.text
    assert r.json()["row_count"] == 3

    r = client.get("/api/datasets")
    assert r.status_code == 200
    assert r.json()["datasets"][0]["name"] == "api_ds"

    r = client.get("/api/datasets/api_ds/profile")
    assert r.status_code == 200
    assert "a" in r.json()["profile"]

    r = client.post("/api/datasets/api_ds/transform", json={"op": "derive", "params": {"name": "c", "expr": "a + b"}, "preview": True})
    assert r.status_code == 200
    assert r.json()["row_count_after"] == 3

    r = client.post("/api/datasets/api_ds/transform", json={"op": "derive", "params": {"name": "c", "expr": "a + b"}, "preview": False})
    assert r.status_code == 200

    r = client.post("/api/datasets/api_ds/quality", json={"name": "a not null", "kind": "not_null", "params": {"column": "a"}})
    assert r.status_code == 200
    r = client.post("/api/datasets/api_ds/quality/run", json={})
    assert r.status_code == 200
    assert r.json()["results"][0]["passed"] is True

    r = client.post("/api/charts", json={"dataset": "api_ds", "kind": "bar", "x": "a", "y": "b"})
    assert r.status_code == 200
    chart_id = r.json()["chart_id"]
    r = client.get(f"/api/charts/{chart_id}")
    assert r.status_code == 200

    r = client.post("/api/export", json={"dataset": "api_ds", "format": "csv"})
    assert r.status_code == 200

    r = client.get("/api/log")
    assert r.status_code == 200
    assert len(r.json()["entries"]) >= 5

    r = client.post("/api/query", json={"sql": "SELECT COUNT(*) AS n FROM api_ds"})
    assert r.status_code == 200
    assert r.json()["rows"][0]["n"] == 3


def test_rest_errors_are_json(client):
    r = client.get("/api/datasets/nope/profile")
    assert r.status_code == 404
    assert "error" in r.json()

    r = client.post("/api/query", json={"sql": "DROP TABLE api_ds"})
    assert r.status_code == 400
    assert "error" in r.json()

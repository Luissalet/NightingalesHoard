"""PWA manifest and service worker: installable on the phone home screen."""


def test_manifest_has_required_fields(client):
    response = client.get("/manifest.webmanifest")
    assert response.status_code == 200
    assert response.headers["content-type"].startswith("application/manifest+json")
    body = response.json()
    assert body["name"] == "Nightingale's Hoard"
    assert body["start_url"] == "/"
    assert body["display"] == "standalone"
    sizes = {icon["sizes"] for icon in body["icons"]}
    assert "192x192" in sizes and "512x512" in sizes


def test_service_worker_is_js_and_installable_at_root(client):
    response = client.get("/sw.js")
    assert response.status_code == 200
    assert response.headers["content-type"].startswith("application/javascript")
    assert response.headers["service-worker-allowed"] == "/"
    body = response.text
    assert "skipWaiting" in body and "clients.claim" in body and "/api/" in body


def test_health_and_status(client):
    health = client.get("/api/health").json()
    assert health["service"] == "nightingale-hoard"
    status = client.get("/api/status").json()
    assert status["service"] == "nightingale-hoard"
    assert "backends" in status

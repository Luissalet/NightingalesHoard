"""Request guard: host allow-list, Origin rule and Fetch Metadata rules.

Adapted from Hypatia's Hoard's `test_guard.py` (same author, MIT) — the guard
is byte-identical across every Hoard app, only the app-under-test differs.
"""

from conftest import make_config
from fastapi import FastAPI
from fastapi.testclient import TestClient

from nightingale.guard import check_request, host_of, install_guard, is_allowed_host, parse_allowed_hosts
from nightingale.main import create_app

NAV = {"sec-fetch-site": "cross-site", "sec-fetch-mode": "navigate", "sec-fetch-dest": "document"}
CORS = {"sec-fetch-site": "cross-site", "sec-fetch-mode": "cors", "sec-fetch-dest": "empty"}
IFRAME = {"sec-fetch-site": "cross-site", "sec-fetch-mode": "navigate", "sec-fetch-dest": "iframe"}


def test_host_of_strips_scheme_path_port_and_case():
    assert host_of("LocalHost:5189") == "localhost"
    assert host_of("https://My-PC.ts.net:8443/x") == "my-pc.ts.net"
    assert host_of("[::1]:5189") == "[::1]"
    assert host_of("") == "" and host_of(None) == ""


def test_parse_allowed_hosts():
    assert parse_allowed_hosts(" pc.example , *.TS.net,, pc2.example:8443") == ("pc.example", "*.ts.net", "pc2.example")
    assert parse_allowed_hosts(None) == () and parse_allowed_hosts("*.") == ()


def test_is_allowed_host_exact_wildcard_unknown():
    allowed = parse_allowed_hosts("pc.example,*.ts.net")
    for host in ("localhost", "127.0.0.1", "[::1]", "pc.example", "my-pc.ts.net", "a.b.ts.net"):
        assert is_allowed_host(host, allowed), host
    for host in ("ts.net", "evil.example", "pc.example.evil", "", None):
        assert not is_allowed_host(host, allowed), host


def test_check_request_fetch_metadata_rules():
    local = {"host": "localhost:5189"}
    assert check_request("GET", local) is None
    assert check_request("POST", {"host": "127.0.0.1:5189"}) is None
    assert check_request("GET", {**local, **NAV}) is None
    assert check_request("GET", {**local, **CORS})
    assert check_request("GET", {**local, **IFRAME})
    assert check_request("POST", {**local, **NAV})
    assert check_request("GET", {"host": "evil.example"})


def test_middleware_navigation_reaches_root_but_not_embeds_or_fetches():
    app = FastAPI()
    install_guard(app, parse_allowed_hosts("*.ts.net"))

    @app.get("/")
    def home():
        return {"ok": True}

    with TestClient(app, base_url="http://127.0.0.1") as client:
        assert client.get("/", headers=NAV).status_code == 200
        assert client.get("/", headers={**NAV, "host": "my-pc.ts.net"}).status_code == 200
        assert client.get("/", headers=IFRAME).status_code == 403
        assert client.get("/", headers=CORS).status_code == 403
        assert client.get("/", headers={**NAV, "host": "other.example"}).status_code == 403


def test_app_without_allowed_hosts_is_local_only(client):
    assert client.get("/api/health", headers={"host": "my-pc.ts.net"}).status_code == 403
    assert client.get("/api/health", headers={"host": "[::1]:5189"}).status_code == 200


def test_app_host_and_origin_rules(tmp_path):
    app = create_app(make_config(tmp_path, allowed_hosts=parse_allowed_hosts("pc.example, *.ts.net")))
    with TestClient(app, base_url="http://127.0.0.1") as guarded:
        get = lambda **headers: guarded.get("/api/health", headers=headers).status_code  # noqa: E731
        assert get() == 200
        assert get(host="pc.example") == 200
        assert get(host="My-PC.ts.net:8443") == 200
        assert get(host="evil.example") == 403
        assert get(origin="https://my-pc.ts.net:8443") == 200
        assert get(origin="https://evil.example") == 403

"""Opt-in native browser regression; uses only a temporary Laplace database.

Build frontend first. Run with the app's .venv Python and Playwright installed
or pass --playwright-sitepackages pointing to an existing installation.
No Faustus server, model or shared user database is started.
"""
import argparse
import json
import os
import socket
import sys
import tempfile
import threading
import time
from pathlib import Path


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output", type=Path)
    parser.add_argument("--playwright-sitepackages", type=Path)
    args = parser.parse_args()
    if args.playwright_sitepackages:
        sys.path.append(str(args.playwright_sitepackages))
    from playwright.sync_api import sync_playwright
    import httpx
    import uvicorn

    root = Path(__file__).resolve().parents[1]
    sys.path.insert(0, str(root))
    with tempfile.TemporaryDirectory(prefix="laplace-context-") as private:
        data = Path(private)
        os.environ.update(LAPLACE_DATA_DIR=str(data), HOARD_EVENTS="0",
                          HOARD_HUB_AUTOSTART="0", HOARD_HUB_URL="http://127.0.0.1:1",
                          HOARD_HOME=str(data / "home"), LAPLACE_FAUSTUS_URL="http://127.0.0.1:1")
        from laplaces_hoard import backend
        from laplaces_hoard.api import create_app
        from laplaces_hoard.engines import sandbox

        with socket.socket() as probe:
            probe.bind(("127.0.0.1", 0))
            port = probe.getsockname()[1]

        def offline_link():
            return backend.load_link(data, env={}, client=httpx.AsyncClient(
                transport=httpx.MockTransport(lambda request: httpx.Response(404, json={}))))

        app = create_app(data, root / "frontend/dist", port=port, link_factory=offline_link)
        server = uvicorn.Server(uvicorn.Config(app, host="127.0.0.1", port=port, log_level="warning"))
        thread = threading.Thread(target=server.run, daemon=True)
        thread.start()
        base = f"http://127.0.0.1:{port}"
        try:
            for _ in range(100):
                try:
                    if httpx.get(base + "/api/health", timeout=1).status_code == 200:
                        break
                except httpx.HTTPError:
                    pass
                time.sleep(.05)
            else:
                raise RuntimeError("Private native Laplace did not start")
            cell = httpx.post(base + "/api/ui/notebook_cell_create",
                              json={"engine": "calc", "input": "1+2"}, timeout=30).json()
            before_log = httpx.get(base + "/api/log").json()
            with sync_playwright() as p:
                browser = p.chromium.launch(channel="msedge", headless=True)
                context = browser.new_context()
                try:
                    context.add_init_script("localStorage.setItem('lh_lang','en')")
                    page = context.new_page()
                    errors = []
                    page.on("pageerror", lambda error: errors.append(str(error)))
                    page.goto(base)
                    card = page.locator(f'.cell[data-copilot-id="{cell["id"]}"]')
                    card.get_by_role("button", name="Edit cell", exact=True).click()
                    card.locator("input[name=input]").fill("9+10")
                    initial = page.evaluate("window.hoardCopilot.context()")

                    def switch_view(route):
                        reply = route.fetch()  # Native response; only delivery is delayed.
                        page.locator(".sidebar").get_by_role("button", name="Settings", exact=True).click()
                        page.get_by_role("button", name="Español", exact=True).click()
                        route.fulfill(response=reply)

                    page.route("**/api/ui/app_context", switch_view)
                    pending = page.evaluate("window.hoardCopilot.context()")
                    page.unroute("**/api/ui/app_context", switch_view)
                    assert pending == initial, {"initial": initial, "pending": pending}
                    assert pending["view"]["section"] == "notebook"
                    assert pending["view"]["locale"] == "en"
                    assert pending["view"]["editor"]["engine"] == "calc"
                    assert any(field["name"] == "input" and field["value"] == "9+10"
                               for field in pending["view"]["editor"]["fields"])
                    subsequent = page.evaluate("window.hoardCopilot.context()")
                    assert subsequent["view"]["section"] == "settings"
                    assert subsequent["view"]["locale"] == "es"
                    assert subsequent["view"]["editor"]["kind"] == "settings"
                    assert subsequent["active_objects"] == []
                    assert httpx.get(base + "/api/log").json() == before_log
                    assert errors == [], errors
                    result = {"pending": pending, "subsequent": subsequent,
                              "page_errors": errors, "readonly_log_unchanged": True,
                              "native_response_delayed": True}
                    if args.output:
                        args.output.parent.mkdir(parents=True, exist_ok=True)
                        args.output.write_text(json.dumps(result, ensure_ascii=False, indent=2), encoding="utf8")
                    print("PASS: pending context preserves its snapshot; the next request sees Settings/es")
                finally:
                    context.close()
                    browser.close()
        finally:
            server.should_exit = True
            thread.join(10)
            sandbox.shutdown()
            app.state.lh.conn.close()


if __name__ == "__main__":
    main()

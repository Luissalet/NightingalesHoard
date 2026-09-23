"""PWA manifest and service worker. Adapted from Hypatia's Hoard (same author, MIT)."""

from __future__ import annotations

import json

from fastapi import APIRouter, Response

from .. import __version__

router = APIRouter()

_MANIFEST = {
    "name": "Nightingale's Hoard",
    "short_name": "Nightingale",
    "start_url": "/",
    "display": "standalone",
    "background_color": "#fff6fa",
    "theme_color": "#7a1f4a",
    "lang": "en",
    "icons": [
        {"src": "/icon-192.png", "sizes": "192x192", "type": "image/png", "purpose": "any"},
        {"src": "/icon-512.png", "sizes": "512x512", "type": "image/png", "purpose": "any maskable"},
    ],
}


def _service_worker() -> str:
    cache_name = json.dumps(f"nightingale-hoard-assets-v{__version__}")
    return f"""// Installability plus a cache-first strategy for built /assets/ files only.
// API calls are always network-only and never cached. No offline page.
const CACHE_NAME = {cache_name};

self.addEventListener("install", () => {{ self.skipWaiting(); }});

self.addEventListener("activate", (event) => {{
  event.waitUntil((async () => {{
    const names = await caches.keys();
    await Promise.all(names.filter((name) => name !== CACHE_NAME).map((name) => caches.delete(name)));
    await self.clients.claim();
  }})());
}});

self.addEventListener("fetch", (event) => {{
  if (event.request.method !== "GET") return;
  const url = new URL(event.request.url);
  if (url.pathname.startsWith("/api/")) return;
  if (!url.pathname.startsWith("/assets/")) return;
  event.respondWith((async () => {{
    const cache = await caches.open(CACHE_NAME);
    const cached = await cache.match(event.request);
    if (cached) return cached;
    const response = await fetch(event.request);
    if (response.ok) cache.put(event.request, response.clone());
    return response;
  }})());
}});
"""


@router.get("/manifest.webmanifest", include_in_schema=False)
def manifest() -> Response:
    return Response(content=json.dumps(_MANIFEST), media_type="application/manifest+json")


@router.get("/sw.js", include_in_schema=False)
def service_worker() -> Response:
    return Response(content=_service_worker(), media_type="application/javascript",
                     headers={"Service-Worker-Allowed": "/"})

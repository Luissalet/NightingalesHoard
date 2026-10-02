"""Stdio MCP bridge for Nightingale's Hoard.

It never opens the database: every tool call is proxied to the running app (`POST /api/agent/call`) with the Bearer token from
`<DATA_DIR>/mcp-token`. The tool list is fetched from `GET /api/agent/tools` (refreshed while the bridge runs), so the bridge and the app can
never disagree. When nothing answers, the bridge starts the app itself (`python -m nightingale`, detached, on the port of NIGHTINGALE_URL) and
waits for it; NIGHTINGALE_BRIDGE_AUTOSTART=0 turns that off. The bridge itself is the shared catalogue bridge of Hoard Link.
"""

from __future__ import annotations

import sys

from nightingale.hoard_link.bridge import CatalogBridge


def main() -> int:
    CatalogBridge(app="nightingale", service="nightingale-hoard", package="nightingale", default_port=5189, data_dir_env="NIGHTINGALE_DATA_DIR",
                  title="Nightingale's Hoard", root=__file__, default_timeout=120.0).run_bridge()
    return 0


if __name__ == "__main__":
    sys.exit(main())

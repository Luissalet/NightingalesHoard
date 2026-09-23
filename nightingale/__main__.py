"""`python -m nightingale` — run the app with uvicorn on 127.0.0.1.

`--demo` seeds invented demo data (a messy Spanish-format sales CSV, a
seasonal sensor time series with a few anomalies, and a customers table for
joins) into a separate `data-demo/` directory, so it never touches a real
`data/` folder.
"""

from __future__ import annotations

import logging
import sys

import uvicorn

from .config import Config
from .main import create_app
from .port import find_available_port


def main() -> None:
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(name)s %(levelname)s %(message)s")
    demo = "--demo" in sys.argv[1:]
    config = Config.from_env(demo=demo)
    port = config.port if config.port_strict else find_available_port(config.port)
    config.port = port
    app = create_app(config)
    print(f"Nightingale's Hoard listening on http://127.0.0.1:{port}{' (demo data)' if demo else ''}", flush=True)
    uvicorn.run(app, host="127.0.0.1", port=port, log_level="warning")


if __name__ == "__main__":
    main()

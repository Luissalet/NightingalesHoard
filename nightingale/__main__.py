"""`python -m nightingale` — run the app with uvicorn on 127.0.0.1 (the shared Hoard Link launcher).

`--demo` seeds invented demo data (a messy Spanish-format sales CSV, a
seasonal sensor time series with a few anomalies, and a customers table for
joins) into a separate `data-demo/` directory, so it never touches a real
`data/` folder. The other flags are the shared ones: `--port`, `--data-dir`,
`--host`, `--no-browser`, `--browser`.
"""

from __future__ import annotations

import os
import sys

from .config import REPO_ROOT
from .hoard_link.service import run_main


def main(argv: list[str] | None = None) -> int:
    args = list(sys.argv[1:] if argv is None else argv)
    demo = "--demo" in args
    args = [a for a in args if a != "--demo"]
    if demo:
        os.environ["NIGHTINGALE_DEMO"] = "1"  # create_app reads it through Config.from_env()
    return run_main(service="nightingale-hoard", package="nightingale", default_port=5189, app_factory="nightingale.main:create_app",
                    data_dir_env="NIGHTINGALE_DATA_DIR", port_env="NIGHTINGALE_PORT", open_browser_default=False, title="Nightingale's Hoard",
                    argv=args, default_data_dir=lambda: REPO_ROOT / ("data-demo" if demo else "data"))


if __name__ == "__main__":
    raise SystemExit(main())

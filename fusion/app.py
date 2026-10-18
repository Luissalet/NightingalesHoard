"""Nightingale's Hoard: one server with two independently owned lifetimes."""
from pathlib import Path

from .native import SPECS, factory
from .runtime import create_runtime


def create_app(config=None):
    from nightingale.config import Config
    config = config or Config.from_env()
    return create_runtime(name="Nightingale's Hoard", product="nightingale", root=Path(__file__).parent,
                          data_dir=config.data_dir, specs=SPECS, port=config.port,
                          factory=lambda key, path: factory(key, path, port=config.port))

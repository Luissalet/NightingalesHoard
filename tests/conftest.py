import sys
import warnings
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent
for entry in (str(ROOT), str(ROOT / "tests")):
    if entry not in sys.path:
        sys.path.insert(0, entry)

warnings.filterwarnings("ignore", category=DeprecationWarning)

from nightingale.config import Config  # noqa: E402
from nightingale.main import create_app  # noqa: E402
from nightingale.services import Services  # noqa: E402

FIXTURES = Path(__file__).resolve().parent / "fixtures"


def make_config(tmp_path: Path, **overrides) -> Config:
    base = dict(data_dir=tmp_path / "data", data_dir_configured=True)
    base.update(overrides)
    return Config(**base)


class FakeClock:
    def __init__(self, start: float = 1_700_000_000.0):
        self.value = start

    def __call__(self) -> float:
        return self.value

    def advance(self, seconds: float = 0) -> None:
        self.value += seconds


@pytest.fixture
def clock():
    return FakeClock()


@pytest.fixture
def services(tmp_path, clock):
    svc = Services(make_config(tmp_path), clock=clock)
    yield svc
    svc.stop()


@pytest.fixture
def client(tmp_path, clock):
    from fastapi.testclient import TestClient

    svc = Services(make_config(tmp_path), clock=clock)
    app = create_app(make_config(tmp_path), services=svc)
    with TestClient(app, base_url="http://127.0.0.1") as test_client:
        test_client.services = app.state.services
        test_client.clock = clock
        yield test_client

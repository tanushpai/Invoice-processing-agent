import pathlib

import pytest

from src.core.config import get_settings
from src.database.seed import seed_demo_data
from src.database.session import get_sessionmaker, init_db, make_engine
from src.workflow.graph import run_pipeline

SAMPLES = pathlib.Path(__file__).resolve().parents[1] / "data" / "sample_invoices"


@pytest.fixture()
def engine():
    e = make_engine("sqlite://")
    init_db(e)
    with get_sessionmaker(e)() as s:
        seed_demo_data(s)
    return e


@pytest.fixture()
def session(engine):
    with get_sessionmaker(engine)() as s:
        yield s


@pytest.fixture()
def run(session):
    from src.ai.offline import OfflineProvider

    def _run(data: bytes | str, provider=None, name="x.pdf"):
        if isinstance(data, str):
            name, data = data, (SAMPLES / data).read_bytes()
        final = None
        for kind, payload in run_pipeline(session, provider or OfflineProvider(), get_settings(), data, name):
            if kind == "done":
                final = payload
        return final

    return _run

import os
import pytest
import pytest_asyncio
import uvloop

from oban import Oban
from oban._config import Config
from oban.testing import reset_oban


def pytest_asyncio_loop_factories(config, item):
    return {"uvloop": uvloop.new_event_loop}


@pytest.fixture(scope="session")
def dsn_base():
    return os.getenv("DSN_BASE", "postgresql://postgres@localhost")


@pytest.fixture(scope="session")
def test_dsn(dsn_base):
    return f"{dsn_base}/oban_py_test"


@pytest_asyncio.fixture
async def oban_instance(request, test_dsn):
    mark = request.node.get_closest_marker("oban")
    mark_kwargs = mark.kwargs if mark else {}

    pool = await Config(
        dsn=test_dsn, pool_min_size=2, pool_max_size=10, pool_timeout=0.5
    ).create_pool()

    instances = []

    def create_instance(**overrides):
        params = {"pool": pool, "leadership": False, "stager": {"interval": 0.01}}
        oban = Oban(**{**params, **mark_kwargs, **overrides})

        instances.append(oban)

        return oban

    yield create_instance

    for oban in instances:
        await reset_oban(oban)

    await pool.close()

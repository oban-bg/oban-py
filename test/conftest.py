import os
import pytest
import pytest_asyncio
import psycopg
import uvloop

from oban import Oban
from oban.config import Config
from oban.schema import install
from oban.testing import reset_oban

DB_URL_BASE = os.getenv("DB_URL_BASE", "postgresql://postgres@localhost")


@pytest.fixture(scope="session")
def event_loop_policy():
    return uvloop.EventLoopPolicy()


@pytest_asyncio.fixture(scope="session")
async def test_database(request):
    worker_id = getattr(request.config, "workerinput", {}).get("workerid", "master")

    if worker_id == "master":
        worker_idx = 0
    else:
        worker_idx = int(worker_id.replace("gw", ""))

    dbname = f"oban_py_test_{worker_idx}"
    db_url = f"{DB_URL_BASE}/{dbname}"

    with psycopg.connect(f"{DB_URL_BASE}/postgres", autocommit=True) as conn:
        exists = conn.execute(
            "SELECT 1 FROM pg_database WHERE datname = %s", (dbname,)
        ).fetchone()

        if not exists:
            conn.execute(f'CREATE DATABASE "{dbname}"')

            async with Config(dsn=db_url, pool_max_size=1).create_pool() as pool:
                await install(pool)

    yield db_url


@pytest_asyncio.fixture
async def oban_instance(request, test_database):
    mark = request.node.get_closest_marker("oban")
    mark_kwargs = mark.kwargs if mark else {}

    pool = await Config(dsn=test_database, pool_min_size=2, pool_max_size=10).create_pool()

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

import asyncio
import os
import pytest
import pytest_asyncio
import psycopg
import uvloop

from oban import Oban
from oban.schema import install

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
            await install(db_url)

    yield db_url


@pytest_asyncio.fixture
async def db_url(test_database):
    yield test_database

    with psycopg.connect(test_database) as conn:
        conn.execute("TRUNCATE TABLE oban_jobs, oban_peers RESTART IDENTITY CASCADE")
        conn.commit()


@pytest_asyncio.fixture
def oban_instance(request, db_url):
    mark = request.node.get_closest_marker("oban")
    mark_kwargs = mark.kwargs if mark else {}

    def _create_instance(**overrides):
        params = {"pool": {"url": db_url}, "stage_interval": 0.01}
        return Oban(**{**params, **mark_kwargs, **overrides})

    return _create_instance

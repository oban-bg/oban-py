import psycopg
import pytest
import pytest_asyncio

from oban._config import Config
from oban.schema import install, install_sql, uninstall, uninstall_sql


@pytest.fixture
def postgres_conn(dsn_base):
    def _conn():
        return psycopg.connect(f"{dsn_base}/postgres", autocommit=True)

    return _conn


async def list_tables(pool, prefix="public"):
    async with pool.connection() as conn:
        result = await conn.execute(
            f"""SELECT table_name FROM information_schema.tables
            WHERE table_schema = '{prefix}'
            ORDER BY table_name
            """
        )

        return [row[0] for row in await result.fetchall()]


@pytest_asyncio.fixture
async def isolated_db(postgres_conn, dsn_base):
    test_db = "oban_schema_test_temp"

    with postgres_conn() as conn:
        conn.execute(f'DROP DATABASE IF EXISTS "{test_db}"')
        conn.execute(f'CREATE DATABASE "{test_db}"')

    dsn = f"{dsn_base}/{test_db}"

    pool = await Config(dsn=dsn, pool_min_size=1, pool_max_size=2).create_pool()

    try:
        yield pool
    finally:
        await pool.close()

    with postgres_conn() as conn:
        conn.execute(f'DROP DATABASE IF EXISTS "{test_db}"')


class TestInstallSql:
    def test_contains_expected_schema_elements(self):
        sql = install_sql()

        assert "public.oban_job_state" in sql
        assert "public.oban_jobs" in sql
        assert "public.oban_leaders" in sql
        assert "public.oban_producers" in sql

    def test_scoping_elements_to_the_prefix(self):
        sql = install_sql(prefix="isolated")

        assert "isolated.oban_job_state" in sql
        assert "isolated.oban_jobs" in sql
        assert "isolated.oban_leaders" in sql
        assert "isolated.oban_producers" in sql


class TestUninstallSql:
    def test_contains_expected_schema_elements(self):
        sql = uninstall_sql()

        assert "DROP TABLE" in sql
        assert "oban_jobs" in sql
        assert "oban_leaders" in sql
        assert "oban_producers" in sql
        assert "DROP TYPE" in sql
        assert "oban_job_state" in sql


class TestInstall:
    async def test_creates_schema_in_database(self, isolated_db):
        await install(isolated_db)

        tables = await list_tables(isolated_db)

        assert "oban_jobs" in tables
        assert "oban_leaders" in tables
        assert "oban_producers" in tables

    async def test_creates_schema_in_database_using_prefix(self, isolated_db):
        async with isolated_db.connection() as conn:
            await conn.execute("CREATE SCHEMA IF NOT EXISTS isolated")

        await install(isolated_db, prefix="isolated")

        tables = await list_tables(isolated_db, prefix="isolated")

        assert "oban_jobs" in tables
        assert "oban_leaders" in tables
        assert "oban_producers" in tables

    async def test_installing_repeatedly_using_prefix(self, isolated_db):
        async with isolated_db.connection() as conn:
            await conn.execute("CREATE SCHEMA IF NOT EXISTS isolated")

        await install(isolated_db, prefix="isolated")
        await install(isolated_db, prefix="isolated")

        tables = await list_tables(isolated_db, prefix="isolated")

        assert "oban_jobs" in tables


class TestUninstall:
    async def test_removes_schema_from_database(self, isolated_db):
        await install(isolated_db)
        await uninstall(isolated_db)

        tables = await list_tables(isolated_db)

        assert "oban_jobs" not in tables
        assert "oban_leaders" not in tables
        assert "oban_producers" not in tables

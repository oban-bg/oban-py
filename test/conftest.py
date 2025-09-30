import os, uuid
import pytest
import psycopg

DB_URL_BASE = os.getenv("PG_URL_BASE", "postgresql://postgres@localhost")
TEMPLATE_DB = os.getenv("OBAN_TEMPLATE_DB", "oban_test_template")


@pytest.fixture(scope="function")
def db_url():
    dbname = f"oban_test_{uuid.uuid4().hex[:8]}"
    db_url = f"{DB_URL_BASE}/{dbname}"

    with psycopg.connect(f"{DB_URL_BASE}/postgres", autocommit=True) as conn:
        conn.execute(f'CREATE DATABASE "{dbname}" TEMPLATE "{TEMPLATE_DB}"')

    try:
        yield db_url
    finally:
        with psycopg.connect(f"{DB_URL_BASE}/postgres", autocommit=True) as conn:
            conn.execute(f'DROP DATABASE "{dbname}" WITH (FORCE)')

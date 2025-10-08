import os
import psycopg

from oban.schema import install

ADMIN_URL = os.getenv("PG_ADMIN_URL", "postgresql://postgres@localhost/postgres")
TEMPLATE_DB = os.getenv("OBAN_TEMPLATE_DB", "oban_test_template")

TEMPLATE_URL = ADMIN_URL.rsplit("/", 1)[0] + f"/{TEMPLATE_DB}"

with psycopg.connect(ADMIN_URL, autocommit=True) as conn:
    result = conn.execute(
        "SELECT 1 FROM pg_database WHERE datname = %s", (TEMPLATE_DB,)
    ).fetchone()

    if result:
        conn.execute(f'ALTER DATABASE "{TEMPLATE_DB}" IS_TEMPLATE false')

    conn.execute(f'DROP DATABASE IF EXISTS "{TEMPLATE_DB}" WITH (FORCE)')
    conn.execute(f'CREATE DATABASE "{TEMPLATE_DB}"')

install(TEMPLATE_URL)

with psycopg.connect(ADMIN_URL, autocommit=True) as conn:
    conn.execute(f'ALTER DATABASE "{TEMPLATE_DB}" IS_TEMPLATE true')
    conn.execute(f'ALTER DATABASE "{TEMPLATE_DB}" ALLOW_CONNECTIONS false')

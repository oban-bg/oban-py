import os
import psycopg
from alembic.config import Config
from alembic import command

ADMIN_URL = os.getenv("PG_ADMIN_URL", "postgresql://postgres@localhost/postgres")
TEMPLATE_DB = os.getenv("OBAN_TEMPLATE_DB", "oban_test_template")

with psycopg.connect(ADMIN_URL, autocommit=True) as conn:
    conn.execute(f'ALTER DATABASE "{TEMPLATE_DB}" IS_TEMPLATE false')
    conn.execute(f'DROP DATABASE IF EXISTS "{TEMPLATE_DB}" WITH (FORCE)')
    conn.execute(f'CREATE DATABASE "{TEMPLATE_DB}"')

config = Config("alembic.ini")
config.set_main_option(
    "sqlalchemy.url", ADMIN_URL.rsplit("/", 1)[0] + f"/{TEMPLATE_DB}"
)
command.upgrade(config, "head")

with psycopg.connect(ADMIN_URL, autocommit=True) as conn:
    conn.execute(f'ALTER DATABASE "{TEMPLATE_DB}" IS_TEMPLATE true')
    conn.execute(f'ALTER DATABASE "{TEMPLATE_DB}" ALLOW_CONNECTIONS false')

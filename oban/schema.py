"""Database schema installation for Oban."""

from typing import Any

from ._query import Query


def install_sql(prefix: str = "public") -> str:
    """Get the SQL for installing Oban.

    Returns the raw SQL statements for creating Oban types, tables, and indexes.
    This is intended for integration with migration frameworks like Django or Alembic.

    Args:
        prefix: PostgreSQL schema where Oban tables will be located (default: "public")

    Returns:
        SQL string for schema installation

    Example (Alembic):
        >>> from alembic import op
        >>> from oban.schema import install_sql
        >>>
        >>> def upgrade():
        ...     op.execute(install_sql())

    Example (Django):
        >>> from django.db import migrations
        >>> from oban.schema import install_sql
        >>>
        >>> class Migration(migrations.Migration):
        ...     operations = [
        ...         migrations.RunSQL(install_sql()),
        ...     ]
    """
    return Query._load_file("install.sql", prefix)


def uninstall_sql(prefix: str = "public") -> str:
    """Get the SQL for uninstalling Oban.

    Returns the raw SQL statements for dropping Oban tables and types.
    Useful for integration with migration frameworks like Alembic or Django.

    Args:
        prefix: PostgreSQL schema where Oban tables are located (default: "public")

    Returns:
        SQL string for schema uninstallation

    Example (Alembic):
        >>> from alembic import op
        >>> from oban.schema import uninstall_sql
        >>>
        >>> def downgrade():
        ...     op.execute(uninstall_sql())

    Example (Django):
        >>> from django.db import migrations
        >>> from oban.schema import uninstall_sql
        >>>
        >>> class Migration(migrations.Migration):
        ...     operations = [
        ...         migrations.RunSQL(uninstall_sql()),
        ...     ]
    """
    return Query._load_file("uninstall.sql", prefix)


async def install(conn_or_pool: Any, prefix: str = "public") -> None:
    """Install Oban in the specified database.

    Creates all necessary types, tables, and indexes for Oban to function. The
    installation is wrapped in a DDL transaction to ensure the operation is
    atomic.

    Args:
        conn_or_pool: A database connection or pool
        prefix: PostgreSQL schema where Oban tables will be located (default: "public")

    Example:
        >>> from psycopg import AsyncConnection
        >>> from oban.schema import install
        >>>
        >>> async with await AsyncConnection.connect(DATABASE_URL) as conn:
        ...     await install(conn)
    """
    query = Query(conn_or_pool, prefix)
    await query.install()


async def uninstall(conn_or_pool: Any, prefix: str = "public") -> None:
    """Uninstall Oban from the specified database.

    Drops all Oban tables and types. The uninstallation is wrapped in a DDL
    transaction to ensure the operation is atomic.

    Args:
        conn_or_pool: A database connection or pool
        prefix: PostgreSQL schema where Oban tables are located (default: "public")

    Example:
        >>> from psycopg import AsyncConnection
        >>> from oban.schema import uninstall
        >>>
        >>> async with await AsyncConnection.connect(DATABASE_URL) as conn:
        ...     await uninstall(conn)
    """
    query = Query(conn_or_pool, prefix)
    await query.uninstall()

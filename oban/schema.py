"""Database schema installation for Oban."""

from . import _query

def install(database_url: str) -> None:
    """Install the Oban schema in the specified database.

    Creates all necessary types, tables, and indexes for Oban to function.

    Args:
        database_url: PostgreSQL connection URL

    Example:
        >>> from oban.schema import install
        >>>
        >>> install("postgresql://user:pass@localhost/mydb")
    """
    import psycopg

    with psycopg.connect(database_url) as conn:
        _query.install(conn)

        conn.commit()

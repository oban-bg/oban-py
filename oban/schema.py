"""Database schema installation for Oban."""

from . import _query


async def install(database_url: str) -> None:
    """Install the Oban schema in the specified database.

    Creates all necessary types, tables, and indexes for Oban to function.

    Args:
        database_url: PostgreSQL connection URL

    Example:
        >>> from oban.schema import install
        >>>
        >>> await install("postgresql://user:pass@localhost/mydb")
    """
    import psycopg

    async with await psycopg.AsyncConnection.connect(database_url) as conn:
        await _query.install(conn)
        await conn.commit()

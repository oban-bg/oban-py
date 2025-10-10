from __future__ import annotations

from contextlib import asynccontextmanager
from typing import TYPE_CHECKING, Any, AsyncIterator, Protocol, runtime_checkable

if TYPE_CHECKING:
    from psycopg import AsyncConnection
    from psycopg_pool import AsyncConnectionPool


@runtime_checkable
class Driver(Protocol):
    """Protocol for database drivers."""

    def connection(self) -> Any:
        """Return an async context manager that yields a connection.

        Returns:
            An async context manager that yields a connection object.
        """
        ...


class PsycopgPoolDriver:
    def __init__(self, pool: AsyncConnectionPool) -> None:
        self._pool = pool

    def connection(self) -> Any:
        return self._pool.connection()


class PsycopgConnDriver:
    def __init__(self, conn: AsyncConnection) -> None:
        self._conn = conn

    @asynccontextmanager
    async def connection(self) -> AsyncIterator[AsyncConnection]:
        yield self._conn


def wrap_conn(conn_or_pool: Any) -> Driver:
    try:
        from psycopg_pool import AsyncConnectionPool

        if isinstance(conn_or_pool, AsyncConnectionPool):
            return PsycopgPoolDriver(conn_or_pool)
    except ImportError:
        pass

    try:
        from psycopg import AsyncConnection

        if isinstance(conn_or_pool, AsyncConnection):
            return PsycopgConnDriver(conn_or_pool)
    except ImportError:
        pass

    raise TypeError(
        f"Unsupported connection or pool provided: {type(conn_or_pool).__name__}"
    )

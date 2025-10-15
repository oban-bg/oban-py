from __future__ import annotations

import asyncio
from collections import defaultdict
from typing import TYPE_CHECKING, Any, Callable, Protocol, runtime_checkable
from uuid import uuid4

from psycopg import AsyncConnection, OperationalError

from ._backoff import jittery_exponential

if TYPE_CHECKING:
    from ._query import Query


@runtime_checkable
class Notifier(Protocol):
    """Protocol for pub/sub notification systems.

    Notifiers enable real-time communication between Oban components using
    channels. The default implementation uses PostgreSQL LISTEN/NOTIFY.
    """

    async def start(self) -> None:
        """Start the notifier and establish necessary connections."""
        ...

    async def stop(self) -> None:
        """Stop the notifier and clean up resources."""
        ...

    async def listen(self, channel: str, callback: Callable[[str, str], Any]) -> str:
        """Register a callback for a channel.

        Args:
            channels: Channel name to listen on
            callback: Async function called when notification received.
                     Receives (channel, payload) as arguments.

        Returns:
            Token (UUID string) used to unregister this subscription

        Example:
            >>> async def handler(channel: str, payload: str):
            ...     print(f"Received on {channel}: {payload}")
            ...
            >>> token = await notifier.listen("insert", handler)
        """
        ...

    async def unlisten(self, token: str) -> None:
        """Unregister a subscription by token.

        Args:
            token: The token returned from listen()

        Example:
            >>> token = await notifier.listen("insert", handler)
            >>> await notifier.unlisten(token)
        """
        ...

    async def notify(self, channel: str, payload: str = "") -> None:
        """Send a notification to a channel.

        Args:
            channel: Channel name to send notification on
            payload: Optional payload string (default: empty string)

        Example:
            >>> await notifier.notify("insert", "")
        """
        ...


class PostgresNotifier:
    """PostgreSQL-based notifier using LISTEN/NOTIFY.

    Maintains a dedicated connection for receiving notifications and dispatches
    them to registered callbacks. Automatically reconnects on connection loss.
    """

    def __init__(
        self,
        *,
        query: Query,
        beat_interval: float = 30.0,
        notify_timeout: float = 0.1,
    ) -> None:
        self._query = query
        self._beat_interval = beat_interval
        self._notify_timeout = notify_timeout

        self._pending_listen = set()
        self._pending_unlisten = set()
        self._listen_events = {}
        self._subscriptions = defaultdict(dict)
        self._tokens = {}

        self._conn = None
        self._loop_task = None
        self._beat_task = None
        self._reconnect_attempts = 0

    async def start(self) -> None:
        await self._connect()

    async def stop(self) -> None:
        self._loop_task.cancel()
        self._beat_task.cancel()

        await asyncio.gather(self._loop_task, self._beat_task, return_exceptions=True)

        try:
            await self._conn.close()
        except Exception:
            pass

    async def listen(self, channel: str, callback: Callable[[str, str], Any]) -> str:
        token = str(uuid4())

        if channel not in self._subscriptions:
            self._pending_listen.add(channel)
            self._listen_events[channel] = asyncio.Event()

        self._tokens[token] = channel
        self._subscriptions[channel][token] = callback

        # The event will block the caller until LISTEN completes, primarily to prevent race
        # conditions during testing.
        if channel in self._listen_events:
            await self._listen_events[channel].wait()

        return token

    async def unlisten(self, token: str) -> None:
        if token not in self._tokens:
            return

        channel = self._tokens.pop(token)

        if channel in self._subscriptions:
            self._subscriptions[channel].pop(token, None)

            if len(self._subscriptions[channel]) == 0:
                del self._subscriptions[channel]
                self._pending_unlisten.add(channel)

    async def notify(self, channel: str, payload: str = "") -> None:
        await self._query.notify(channel, payload)

    async def _connect(self) -> None:
        async with self._query._driver.connection() as temp_conn:
            conninfo = temp_conn.info.dsn

        self._conn = await asyncio.wait_for(
            AsyncConnection.connect(conninfo, autocommit=True), timeout=5.0
        )

        await asyncio.wait_for(self._conn.execute("SELECT 1"), timeout=2.0)

        for channel in list(self._subscriptions.keys()):
            self._pending_listen.add(channel)

        self._loop_task = asyncio.create_task(self._loop(), name="oban-notifier-loop")
        self._beat_task = asyncio.create_task(self._beat(), name="oban-notifier-beat")

        self._reconnect_attempts = 0

    async def _reconnect(self) -> None:
        self._reconnect_attempts += 1

        delay = jittery_exponential(self._reconnect_attempts, max_pow=4)

        await asyncio.sleep(delay)

        try:
            await self._connect()
        except (OSError, OperationalError, asyncio.TimeoutError):
            asyncio.create_task(self._reconnect())

    async def _loop(self) -> None:
        if not self._conn:
            return

        while True:
            try:
                await self._process_pending()
                await self._process_notifications()
            except asyncio.CancelledError:
                raise
            except (OSError, OperationalError):
                self._conn = None
                asyncio.create_task(self._reconnect())
                break

    async def _process_pending(self) -> None:
        for channel in list(self._pending_listen):
            await self._conn.execute(f"LISTEN {channel}")
            self._pending_listen.discard(channel)

            if channel in self._listen_events:
                self._listen_events[channel].set()
                del self._listen_events[channel]

        for channel in list(self._pending_unlisten):
            await self._conn.execute(f"UNLISTEN {channel}")
            self._pending_unlisten.discard(channel)

    async def _process_notifications(self) -> None:
        gen = self._conn.notifies(timeout=self._notify_timeout)

        async for notify in gen:
            await self._dispatch(notify)

    async def _dispatch(self, notify) -> None:
        channel = notify.channel
        payload = notify.payload

        if channel in self._subscriptions:
            for callback in self._subscriptions[channel].values():
                try:
                    result = callback(channel, payload)
                    if asyncio.iscoroutine(result):
                        await result
                except Exception:
                    pass

    async def _beat(self) -> None:
        while True:
            try:
                await asyncio.sleep(self._beat_interval)

                if self._conn and not self._conn.closed:
                    await self._conn.execute("SELECT 1")

            except asyncio.CancelledError:
                raise
            except (OSError, OperationalError):
                pass

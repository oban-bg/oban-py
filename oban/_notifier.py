from __future__ import annotations

import asyncio
import base64
import gzip
import inspect
import logging
from collections import defaultdict
from collections.abc import Callable
from typing import TYPE_CHECKING, Any, Protocol, runtime_checkable
from uuid import uuid4

import orjson
from psycopg import AsyncConnection, InterfaceError, OperationalError, sql

from ._backoff import jittery_exponential

logger = logging.getLogger(__name__)

if TYPE_CHECKING:
    from ._query import Query


def encode_payload(payload: dict) -> str:
    """Encode a dict payload to an efficient format for publishing.

    Args:
        payload: Dict to encode

    Returns:
        Base64-encoded gzipped JSON string
    """
    dumped = orjson.dumps(payload, option=orjson.OPT_NON_STR_KEYS)
    zipped = gzip.compress(dumped)

    return base64.b64encode(zipped).decode("ascii")


def decode_payload(payload: str) -> dict:
    """Decode a payload string to a dict.

    Handles both compressed (base64+gzip) and plain JSON payloads.

    Args:
        payload: Payload string to decode

    Returns:
        Decoded dict
    """
    # Payloads created by SQL queries won't be encoded or compressed.
    if payload.startswith("{"):
        return orjson.loads(payload)
    else:
        decoded = base64.b64decode(payload)
        unzipped = gzip.decompress(decoded)

        return orjson.loads(unzipped.decode("utf-8"))


def build_notifier(spec: dict[str, Any], query: Query) -> Notifier:
    """Build the notifier described by an Oban ``notifier`` config.

    The default for the ``notifier.build`` extension. The ``type`` key picks the
    implementation and the remaining keys are passed through as options.
    """
    options = {key: value for key, value in spec.items() if key != "type"}

    if (kind := spec.get("type", "postgres")) != "postgres":
        raise ValueError(f"unknown notifier type: {kind!r}")

    return PostgresNotifier(query=query, prefix=query._prefix, **options)


@runtime_checkable
class Notifier(Protocol):
    """Protocol for pub/sub notification systems.

    Notifiers enable real-time communication between Oban components using
    channels. The default implementation uses PostgreSQL LISTEN/NOTIFY.
    """

    async def start(self) -> None:
        """Start the notifier and establish necessary connections.

        Subscriptions registered with listen() before starting must be established by the time
        this returns, so no notifications are missed once Oban has started.
        """
        ...

    async def stop(self) -> None:
        """Stop the notifier and clean up resources."""
        ...

    async def listen(
        self,
        channel: str,
        callback: Callable[[str, dict], Any],
        wait: bool = True,
        timeout: float | None = None,
    ) -> str:
        """Register a callback for a channel.

        This may be called before start(), in which case the subscription is established when
        the notifier starts.

        Args:
            channel: Channel name to listen on
            callback: Sync or async function called when notification received.
                      Receives (channel, payload) as arguments where payload is a dict.
                      Async callbacks are executed in the background without blocking.
            wait: If True, blocks until the subscription is fully established and ready to
                  receive notifications. If False, returns immediately after registering.
                  Defaults to True for test reliability.
            timeout: Maximum time to wait when wait=True. None means no timeout.

        Returns:
            Token (UUID string) used to unregister this subscription

        Example:
            >>> async def handler(channel: str, payload: dict):
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

    async def notify(self, channel: str, payloads: dict | list[dict]) -> None:
        """Send one or more notifications to a channel.

        Args:
            channel: Channel name to send notification on
            payloads: Payload dict or list of payload dicts to send

        Example:
            >>> await notifier.notify("insert", {"queue": "default"})
            >>> await notifier.notify("insert", [{"queue": "default"}, {"queue": "mailers"}])
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
        prefix: str = "public",
        beat_interval: float = 30.0,
        connect_timeout: float = 5.0,
        notify_timeout: float = 0.1,
    ) -> None:
        self._query = query
        self._prefix = prefix
        self._beat_interval = beat_interval
        self._connect_timeout = connect_timeout
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

    def _to_full_channel(self, channel: str) -> str:
        return f"{self._prefix}.oban_{channel}"

    def _from_full_channel(self, full_channel: str) -> str:
        (_prefix, channel) = full_channel.split(".", 1)

        return channel[5:]

    async def start(self) -> None:
        await self._connect()

        pending = [event.wait() for event in self._listen_events.values()]

        await asyncio.wait_for(asyncio.gather(*pending), timeout=self._connect_timeout)

    async def stop(self) -> None:
        if not self._loop_task or not self._beat_task or not self._conn:
            return

        self._loop_task.cancel()
        self._beat_task.cancel()

        await asyncio.gather(self._loop_task, self._beat_task, return_exceptions=True)

        try:
            await self._conn.close()
        except Exception:  # noqa: S110
            # Best-effort close during teardown; nothing actionable on failure.
            pass

    async def listen(
        self,
        channel: str,
        callback: Callable[[str, dict], Any],
        wait: bool = True,
        timeout: float | None = None,
    ) -> str:
        token = str(uuid4())

        if channel not in self._subscriptions:
            self._pending_listen.add(channel)
            self._listen_events[channel] = asyncio.Event()

        self._tokens[token] = channel
        self._subscriptions[channel][token] = callback

        if wait and channel in self._listen_events:
            await asyncio.wait_for(self._listen_events[channel].wait(), timeout=timeout)

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

    async def notify(self, channel: str, payloads: dict | list[dict]) -> None:
        if isinstance(payloads, dict):
            payloads = [payloads]

        channel = self._to_full_channel(channel)
        encoded = [encode_payload(payload) for payload in payloads]

        await self._query.notify(channel, encoded)

    async def _connect(self) -> None:
        dsn = await self._query.dsn()

        self._conn = await asyncio.wait_for(
            AsyncConnection.connect(dsn, autocommit=True),
            timeout=self._connect_timeout,
        )

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
        except (TimeoutError, OSError, OperationalError, InterfaceError):
            asyncio.create_task(self._reconnect())

    async def _loop(self) -> None:
        if not self._conn:
            return

        while True:
            try:
                if self._conn.closed:
                    raise InterfaceError("connection is closed")

                await self._process_pending()
                await self._process_notifications()
            except asyncio.CancelledError:
                raise
            except (OSError, OperationalError, InterfaceError):
                self._conn = None
                asyncio.create_task(self._reconnect())
                break

    async def _process_pending(self) -> None:
        if not self._conn:
            return

        for channel in list(self._pending_listen):
            full_channel = self._to_full_channel(channel)
            await self._conn.execute(
                sql.SQL("LISTEN {}").format(sql.Identifier(full_channel))
            )

            self._pending_listen.discard(channel)

            if channel in self._listen_events:
                self._listen_events[channel].set()
                del self._listen_events[channel]

        for channel in list(self._pending_unlisten):
            full_channel = self._to_full_channel(channel)
            await self._conn.execute(
                sql.SQL("UNLISTEN {}").format(sql.Identifier(full_channel))
            )

            self._pending_unlisten.discard(channel)

    async def _process_notifications(self) -> None:
        if not self._conn:
            return

        gen = self._conn.notifies(timeout=self._notify_timeout)

        async for notify in gen:
            await self._dispatch(notify)

    async def _dispatch(self, notify) -> None:
        channel = self._from_full_channel(notify.channel)
        payload = decode_payload(notify.payload)

        if channel in self._subscriptions:
            for callback in self._subscriptions[channel].values():
                try:
                    if inspect.iscoroutinefunction(callback):
                        asyncio.create_task(callback(channel, payload))
                    else:
                        callback(channel, payload)
                except Exception:
                    logger.exception(
                        "Error in notifier callback for channel %s with payload %s",
                        channel,
                        payload,
                    )

    async def _beat(self) -> None:
        while True:
            try:
                await asyncio.sleep(self._beat_interval)

                if self._conn and not self._conn.closed:
                    await self._conn.execute("SELECT 1")

            except asyncio.CancelledError:
                raise
            except (OSError, OperationalError, InterfaceError):
                if self._conn:
                    self._conn = None
                    asyncio.create_task(self._reconnect())
                break

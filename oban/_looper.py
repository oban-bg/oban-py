from __future__ import annotations

import asyncio
from contextlib import suppress
from typing import Protocol, runtime_checkable


@runtime_checkable
class Looper(Protocol):
    """Protocol for background components with a start/stop lifecycle driving a periodic loop.

    Implementations spawn an asyncio task in ``start`` that runs ``_loop`` until
    ``stop`` signals it to exit. Examples include Stager, Pruner, Scheduler, Refresher,
    Lifeline, Leader, Metrics, and Producer.
    """

    async def start(self) -> None:
        """Spawn the background loop task and register any listeners."""
        ...

    async def stop(self) -> None:
        """Stop the background loop task and clean up listeners."""
        ...

    async def _loop(self) -> None:
        """Run the periodic work until stopped."""
        ...


# Long enough for an in-flight query to finish, short enough not to hang shutdown when the
# database is unreachable.
STOP_TIMEOUT = 5.0


async def sleep_until_stopped(stopping: asyncio.Event, seconds: float) -> None:
    with suppress(TimeoutError):
        await asyncio.wait_for(stopping.wait(), timeout=seconds)


async def stop_gracefully(task: asyncio.Task | None, stopping: asyncio.Event) -> None:
    # Cancelling a task mid-query can strand the connection inside a transaction, so loops are
    # asked to exit at their next safe point and only cancelled when that takes too long.
    stopping.set()

    if task is None:
        return

    with suppress(TimeoutError, asyncio.CancelledError):
        await asyncio.wait_for(task, timeout=STOP_TIMEOUT)

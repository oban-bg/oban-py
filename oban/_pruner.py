from __future__ import annotations

import asyncio
import logging
from typing import TYPE_CHECKING

from . import telemetry

logger = logging.getLogger(__name__)

if TYPE_CHECKING:
    from ._leader import Leader
    from ._query import Query


class Pruner:
    """Manages periodic deletion of completed, cancelled, and discarded jobs.

    This class is managed internally by Oban and shouldn't be constructed directly.
    Instead, configure pruning via the Oban constructor:

        >>> async with Oban(
        ...     conn=conn,
        ...     queues={"default": 10},
        ...     pruner={"max_age": 86_400, "interval": 60.0, "limit": 20_000}
        ... ) as oban:
        ...     # Pruner runs automatically in the background
    """

    def __init__(
        self,
        *,
        query: Query,
        leader: Leader,
        max_age: int = 86_400,
        interval: float = 60.0,
        limit: int = 20_000,
    ) -> None:
        self._leader = leader
        self._max_age = max_age
        self._interval = interval
        self._limit = limit
        self._query = query

        self._loop_task = None

        self._validate(max_age=max_age, interval=interval, limit=limit)

    @staticmethod
    def _validate(*, max_age: int, interval: float, limit: int) -> None:
        if not isinstance(max_age, int):
            raise TypeError(f"max_age must be an integer, got {max_age}")
        if max_age <= 0:
            raise ValueError(f"max_age must be positive, got {max_age}")

        if not isinstance(interval, (int, float)):
            raise TypeError(f"interval must be a number, got {interval}")
        if interval <= 0:
            raise ValueError(f"interval must be positive, got {interval}")

        if not isinstance(limit, int):
            raise TypeError(f"limit must be an integer, got {limit}")
        if limit <= 0:
            raise ValueError(f"limit must be positive, got {limit}")

    async def start(self) -> None:
        self._loop_task = asyncio.create_task(self._loop(), name="oban-pruner")

    async def stop(self) -> None:
        if not self._loop_task:
            return

        self._loop_task.cancel()

        try:
            await self._loop_task
        except asyncio.CancelledError:
            pass

    async def _loop(self) -> None:
        while True:
            try:
                await asyncio.sleep(self._interval)

                if self._leader.is_leader:
                    await self._prune()
            except asyncio.CancelledError:
                break
            except Exception:
                logger.exception("Error in pruner")

    async def _prune(self) -> None:
        with telemetry.span("oban.pruner.prune", {}) as context:
            pruned = await self._query.prune_jobs(self._max_age, self._limit)

            context.add({"pruned_count": pruned})

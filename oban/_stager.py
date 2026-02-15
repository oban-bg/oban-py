from __future__ import annotations

import asyncio
import logging
from typing import TYPE_CHECKING

from . import telemetry
from ._extensions import use_ext

logger = logging.getLogger(__name__)

if TYPE_CHECKING:
    from ._notifier import Notifier
    from ._producer import Producer
    from ._query import Query


class Stager:
    """Manages moving jobs to the 'available' state and notifying queues.

    This class is managed internally by Oban and shouldn't be constructed directly.
    Instead, configure staging via the Oban constructor:

        >>> async with Oban(
        ...     conn=conn,
        ...     queues={"default": 10},
        ...     stager={"interval": 1.0, "limit": 20_000}
        ... ) as oban:
        ...     # Stager runs automatically in the background
    """

    def __init__(
        self,
        *,
        query: Query,
        notifier: Notifier,
        producers: dict[str, Producer],
        interval: float = 1.0,
        limit: int = 20_000,
    ) -> None:
        self._query = query
        self._notifier = notifier
        self._producers = producers
        self._interval = interval
        self._limit = limit

        self._loop_task = None
        self._listen_token = None

        self._validate(interval=interval, limit=limit)

    @staticmethod
    def _validate(*, interval: float, limit: int) -> None:
        if not isinstance(interval, (int, float)):
            raise TypeError(f"interval must be a number, got {interval}")
        if interval <= 0:
            raise ValueError(f"interval must be positive, got {interval}")

        if not isinstance(limit, int):
            raise TypeError(f"limit must be an integer, got {limit}")
        if limit <= 0:
            raise ValueError(f"limit must be positive, got {limit}")

    async def start(self) -> None:
        self._listen_token = await self._notifier.listen(
            "insert", self._on_notification, wait=False
        )
        self._loop_task = asyncio.create_task(self._loop(), name="oban-stager")

    async def stop(self) -> None:
        if self._listen_token:
            await self._notifier.unlisten(self._listen_token)

        if self._loop_task:
            self._loop_task.cancel()
            try:
                await self._loop_task
            except asyncio.CancelledError:
                pass

    async def _loop(self) -> None:
        while True:
            try:
                await self._stage()
            except asyncio.CancelledError:
                break
            except Exception as error:
                logger.warning("Stager failed to stage jobs: %s", error, exc_info=True)

            await asyncio.sleep(self._interval)

    async def _on_notification(self, channel: str, payload: dict) -> None:
        queue = payload["queue"]

        if queue in self._producers:
            self._producers[queue].notify()

    async def _noop(self, _query) -> None:
        pass

    async def _stage(self) -> None:
        await use_ext("stager.before_stage", self._noop, self._query)

        with telemetry.span("oban.stager.stage", {}) as context:
            queues = list(self._producers.keys())

            (staged, active) = await self._query.stage_jobs(self._limit, queues)

            context.add({"staged_count": staged, "available_queues": active})

            for queue in active:
                self._producers[queue].notify()

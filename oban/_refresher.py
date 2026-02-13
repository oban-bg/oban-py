from __future__ import annotations

import asyncio
import logging
from typing import TYPE_CHECKING

from . import telemetry

logger = logging.getLogger(__name__)

if TYPE_CHECKING:
    from ._producer import Producer
    from ._leader import Leader
    from ._query import Query


class Refresher:
    def __init__(
        self,
        *,
        query: Query,
        leader: Leader,
        producers: dict[str, Producer],
        interval: float = 15.0,
        max_age: float = 60.0,
    ) -> None:
        self._leader = leader
        self._interval = interval
        self._max_age = max_age
        self._producers = producers
        self._query = query

        self._loop_task = None

        self._validate(interval=interval, max_age=max_age)

    @staticmethod
    def _validate(*, interval: float, max_age: float) -> None:
        if not isinstance(interval, (int, float)):
            raise TypeError(f"interval must be a number, got {interval}")
        if interval <= 0:
            raise ValueError(f"interval must be positive, got {interval}")

        if not isinstance(max_age, (int, float)):
            raise TypeError(f"max_age must be a number, got {max_age}")
        if max_age <= 0:
            raise ValueError(f"max_age must be positive, got {max_age}")

    async def start(self) -> None:
        self._loop_task = asyncio.create_task(self._loop(), name="oban-refresher")

    async def stop(self) -> None:
        if self._loop_task:
            self._loop_task.cancel()

            try:
                await self._loop_task
            except asyncio.CancelledError:
                pass

    async def _loop(self) -> None:
        while True:
            try:
                await asyncio.sleep(self._interval)

                await self._refresh()
                await self._cleanup()
            except asyncio.CancelledError:
                break
            except Exception:
                logger.exception("Error in refresher")

    async def _refresh(self) -> None:
        with telemetry.span("oban.refresher.refresh", {}) as context:
            uuids = [producer._uuid for producer in self._producers.values()]

            if uuids:
                refreshed = await self._query.refresh_producers(uuids)
            else:
                refreshed = 0

            context.add({"refreshed_count": refreshed})

    async def _cleanup(self) -> None:
        if not self._leader.is_leader:
            return

        with telemetry.span("oban.refresher.cleanup", {}) as context:
            cleaned_up = await self._query.cleanup_expired_producers(self._max_age)

            context.add({"cleanup_count": cleaned_up})

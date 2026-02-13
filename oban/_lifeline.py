from __future__ import annotations

import asyncio
import logging
from typing import TYPE_CHECKING

from . import telemetry
from ._extensions import use_ext

logger = logging.getLogger(__name__)

if TYPE_CHECKING:
    from ._leader import Leader
    from ._query import Query


async def _rescue(query: Query, rescue_after: float) -> None:
    with telemetry.span("oban.lifeline.rescue", {}) as context:
        rescued = await query.rescue_jobs(rescue_after)

        context.add({"rescued_count": rescued})


class Lifeline:
    def __init__(
        self,
        *,
        query: Query,
        leader: Leader,
        interval: float = 60.0,
        rescue_after: float = 300.0,
    ) -> None:
        self._leader = leader
        self._interval = interval
        self._rescue_after = rescue_after
        self._query = query

        self._loop_task = None

        self._validate(interval=interval, rescue_after=rescue_after)

    @staticmethod
    def _validate(*, interval: float, rescue_after: float) -> None:
        if not isinstance(interval, (int, float)):
            raise TypeError(f"interval must be a number, got {interval}")
        if interval <= 0:
            raise ValueError(f"interval must be positive, got {interval}")
        if not isinstance(rescue_after, (int, float)):
            raise TypeError(f"rescue_after must be a number, got {rescue_after}")
        if rescue_after <= 0:
            raise ValueError(f"rescue_after must be positive, got {rescue_after}")

    async def start(self) -> None:
        self._loop_task = asyncio.create_task(self._loop(), name="oban-lifeline")

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

                await self._rescue()
            except asyncio.CancelledError:
                break
            except Exception:
                logger.exception("Error in lifeline")

    async def _rescue(self) -> None:
        if not self._leader.is_leader:
            return

        await use_ext("lifeline.rescue", _rescue, self._query, self._rescue_after)

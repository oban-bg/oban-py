from __future__ import annotations

import asyncio
from typing import TYPE_CHECKING

from . import telemetry
from ._extensions import use_ext

if TYPE_CHECKING:
    from ._leader import Leader
    from ._query import Query


async def _noop(_query: Query) -> None:
    pass


class Lifeline:
    def __init__(
        self,
        *,
        query: Query,
        leader: Leader,
        interval: float = 60.0,
    ) -> None:
        self._leader = leader
        self._interval = interval
        self._query = query

        self._loop_task = None

        self._validate(interval=interval)

    @staticmethod
    def _validate(*, interval: float) -> None:
        if not isinstance(interval, (int, float)):
            raise TypeError(f"interval must be a number, got {interval}")
        if interval <= 0:
            raise ValueError(f"interval must be positive, got {interval}")

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
                pass

    async def _rescue(self) -> None:
        if not self._leader.is_leader:
            return

        with telemetry.span("oban.lifeline.rescue", {}) as context:
            rescued = await self._query.rescue_jobs()

            context.add({"rescued_count": rescued})

        await use_ext("lifeline.rescue", _noop, self._query)

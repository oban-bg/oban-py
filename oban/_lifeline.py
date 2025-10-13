from __future__ import annotations

import asyncio
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from .leader import Leader
    from ._query import Query


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
                # Like the pruner, we sleep first before rescuing
                await asyncio.sleep(self._interval)

                await self._rescue()
            except asyncio.CancelledError:
                break
            except Exception:
                pass

    async def _rescue(self) -> None:
        if not self._leader.is_leader:
            return

        await self._query.rescue_jobs()

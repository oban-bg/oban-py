from __future__ import annotations

import asyncio
from typing import TYPE_CHECKING

from . import _query

if TYPE_CHECKING:
    from .oban import Oban
    from ._runner import Runner


class Stager:
    def __init__(
        self,
        *,
        oban: Oban,
        runners: dict[str, Runner],
        stage_interval: float = 1.0,
        stage_limit: int = 20_000,
    ) -> None:
        self._oban = oban
        self._runners = runners
        self._stage_interval = stage_interval
        self._stage_limit = stage_limit
        self._task: asyncio.Task | None = None

    async def start(self) -> None:
        self._task = asyncio.create_task(self._loop(), name="oban-stager")

    async def stop(self) -> None:
        if self._task:
            self._task.cancel()
            try:
                await self._task
            except asyncio.CancelledError:
                pass

    async def _loop(self) -> None:
        while True:
            try:
                await self._stage()
            except asyncio.CancelledError:
                break
            except Exception:
                pass

            await asyncio.sleep(self._stage_interval)

    async def _stage(self) -> None:
        async with self._oban.get_connection() as conn:
            await _query.stage_jobs(conn, self._stage_limit)

            available = await _query.check_available_queues(conn)

        for queue in available:
            if queue in self._runners:
                await self._runners[queue].notify()

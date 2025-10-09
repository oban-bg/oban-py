from __future__ import annotations

import asyncio
from typing import TYPE_CHECKING, Any

from . import _query
from ._backoff import jittery_clamped
from ._worker import resolve_worker
from .job import Job
from .types import Cancel, Snooze

if TYPE_CHECKING:
    from .oban import Oban


class Runner:
    def __init__(
        self,
        *,
        oban: Oban,
        queue: str = "default",
        limit: int = 10,
        uuid: str,
    ) -> None:
        self._oban = oban
        self._queue = queue
        self._limit = limit
        self._uuid = uuid

        self._task: asyncio.Task | None = None
        self._work_available = asyncio.Event()
        self._running_jobs: set[asyncio.Task] = set()

    async def start(self) -> None:
        self._task = asyncio.create_task(
            self._loop(), name=f"oban-runner-{self._queue}"
        )

    async def stop(self) -> None:
        if self._task:
            self._task.cancel()
            try:
                await self._task
            except asyncio.CancelledError:
                pass

        # TODO: Support a grace period before cancelling running jobs
        for job_task in self._running_jobs:
            job_task.cancel()

        # Wait for all jobs to finish
        if self._running_jobs:
            await asyncio.gather(*self._running_jobs, return_exceptions=True)

    async def notify(self) -> None:
        self._work_available.set()

    async def _loop(self) -> None:
        while True:
            try:
                # TODO: Shorten this timeout based on configuration, the timeout changes whether we
                # cleanly break on a stop event
                await asyncio.wait_for(self._work_available.wait(), timeout=1.0)
            except asyncio.TimeoutError:
                continue
            except asyncio.CancelledError:
                break

            self._work_available.clear()

            try:
                demand = self._limit - len(self._running_jobs)

                if demand <= 0:
                    continue

                jobs = await self._fetch_jobs(demand)

                for job in jobs:
                    task = asyncio.create_task(self._execute(self._oban, job))
                    task.add_done_callback(self._running_jobs.discard)

                    self._running_jobs.add(task)

            except asyncio.CancelledError:
                break
            except Exception:
                pass

    async def _fetch_jobs(self, demand: int):
        async with self._oban.get_connection() as conn:
            return await _query.fetch_jobs(
                conn,
                queue=self._queue,
                demand=demand,
                node=self._oban._node,
                uuid=self._uuid,
            )

    async def _execute(self, oban: Oban, job: Job) -> None:
        worker = resolve_worker(job.worker)()

        try:
            result = worker.process(job)
        except Exception as error:
            result = error

        async with oban.get_connection() as conn:
            match result:
                case Exception() as error:
                    backoff = self._backoff(worker, job)

                    await _query.error_job(conn, job, error, backoff)
                case Snooze(seconds=seconds):
                    await _query.snooze_job(conn, job, seconds)
                case Cancel(reason=reason):
                    await _query.cancel_job(conn, job, reason)
                case _:
                    await _query.complete_job(conn, job)

    def _backoff(self, worker: Any, job: Job) -> int:
        if hasattr(worker, "backoff"):
            return worker.backoff(job)
        else:
            return jittery_clamped(job.attempt, job.max_attempts)

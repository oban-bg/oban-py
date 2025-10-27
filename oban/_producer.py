from __future__ import annotations

import asyncio

from datetime import datetime, timezone
from typing import TYPE_CHECKING
from uuid import uuid4

from ._executor import Executor
from .job import Job
from .types import QueueInfo

if TYPE_CHECKING:
    from ._notifier import Notifier
    from ._query import Query


class Producer:
    def __init__(
        self,
        *,
        debounce_interval: float = 0.005,
        limit: int = 10,
        paused: bool = False,
        queue: str = "default",
        name: str,
        node: str,
        notifier: Notifier,
        query: Query,
    ) -> None:
        if limit < 1:
            raise ValueError(f"Queue '{queue}' limit must be positive")

        self._debounce_interval = debounce_interval
        self._limit = limit
        self._name = name
        self._node = node
        self._notifier = notifier
        self._paused = paused
        self._query = query
        self._queue = queue

        self._init_lock = asyncio.Lock()
        self._last_fetch_time = 0.0
        self._listen_token = None
        self._loop_task = None
        self._notified = asyncio.Event()
        self._running_jobs = {}
        self._started_at = None
        self._uuid = str(uuid4())

    async def start(self) -> None:
        async with self._init_lock:
            self._started_at = datetime.now(timezone.utc)

            await self._query.insert_producer(
                uuid=self._uuid,
                name=self._name,
                node=self._node,
                queue=self._queue,
                meta={"local_limit": self._limit, "paused": self._paused},
            )

            self._listen_token = await self._notifier.listen(
                "signal", self._on_signal, wait=False
            )

            self._loop_task = asyncio.create_task(
                self._loop(), name=f"oban-producer-{self._queue}"
            )

    async def stop(self) -> None:
        async with self._init_lock:
            if self._listen_token:
                await self._notifier.unlisten(self._listen_token)

            if self._loop_task:
                self._loop_task.cancel()

                running_tasks = [task for (_job, task) in self._running_jobs.values()]

                await asyncio.gather(
                    self._loop_task,
                    *running_tasks,
                    return_exceptions=True,
                )

            await self._query.delete_producer(self._uuid)

    def notify(self) -> None:
        self._notified.set()

    async def pause(self) -> None:
        self._paused = True

        await self._query.update_producer(uuid=self._uuid, meta={"paused": True})

    async def resume(self) -> None:
        self._paused = False

        await self._query.update_producer(uuid=self._uuid, meta={"paused": False})

        self.notify()

    async def scale(self, limit: int) -> None:
        # TODO: Extract this into a _validate method
        if limit < 1:
            raise ValueError(f"Queue '{self._queue}' limit must be positive")

        self._limit = limit

        await self._query.update_producer(uuid=self._uuid, meta={"local_limit": limit})

        self.notify()

    def check(self) -> QueueInfo:
        """Get the current state of this producer.

        Returns a QueueInfo with the producer's configuration and runtime state.
        """
        return QueueInfo(
            limit=self._limit,
            node=self._node,
            paused=self._paused,
            queue=self._queue,
            running=list(self._running_jobs.keys()),
            started_at=self._started_at,
        )

    async def _loop(self) -> None:
        while True:
            try:
                await asyncio.wait_for(self._notified.wait(), timeout=1.0)
            except asyncio.TimeoutError:
                continue
            except asyncio.CancelledError:
                break

            self._notified.clear()

            try:
                await self._debounce_fetch()

                if self._paused:
                    continue

                demand = self._limit - len(self._running_jobs)

                if demand <= 0:
                    continue

                jobs = await self._fetch_jobs(demand)

                self._last_fetch_time = asyncio.get_event_loop().time()

                for job in jobs:
                    task = asyncio.create_task(self._execute(job))
                    task.add_done_callback(
                        lambda _, job_id=job.id: self._on_job_complete(job_id)
                    )

                    self._running_jobs[job.id] = (job, task)

            except asyncio.CancelledError:
                break
            except Exception:
                pass

    async def _debounce_fetch(self) -> None:
        elapsed = asyncio.get_event_loop().time() - self._last_fetch_time

        if elapsed < self._debounce_interval:
            await asyncio.sleep(self._debounce_interval - elapsed)

    def _on_job_complete(self, job_id: int) -> None:
        self._running_jobs.pop(job_id, None)

        self.notify()

    async def _fetch_jobs(self, demand: int):
        return await self._query.fetch_jobs(
            demand=demand,
            queue=self._queue,
            node=self._node,
            uuid=self._uuid,
        )

    async def _execute(self, job: Job) -> None:
        job._cancellation = asyncio.Event()

        await Executor(job=job, safe=True).execute()

    async def _on_signal(self, _channel: str, payload: dict) -> None:
        ident = payload.get("ident", "any")
        queue = payload.get("queue", "*")

        if queue != "*" and queue != self._queue:
            return

        if ident != "any" and ident != f"{self._name}.{self._node}":
            return

        match payload.get("action"):
            case "pause":
                await self.pause()
            case "resume":
                await self.resume()
            case "pkill":
                job_id = payload["job_id"]

                print("HERE")

                if job_id in self._running_jobs:
                    (job, _task) = self._running_jobs[job_id]

                    job._cancellation.set()

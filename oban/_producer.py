from __future__ import annotations

import asyncio
import logging
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import TYPE_CHECKING, Any
from uuid import uuid4

from . import telemetry
from ._executor import Executor
from ._extensions import use_ext
from .job import Job

if TYPE_CHECKING:
    from ._notifier import Notifier
    from ._query import Query

logger = logging.getLogger(__name__)


def _init(producer: Producer) -> dict:
    return {"local_limit": producer._limit, "paused": producer._paused}


def _pause(producer: Producer) -> dict:
    producer._paused = True

    return {"paused": True}


def _resume(producer: Producer) -> dict:
    producer._paused = False

    return {"paused": False}


def _scale(producer: Producer, **kwargs: Any) -> dict:
    if "limit" in kwargs:
        producer._limit = kwargs["limit"]

    return {"local_limit": producer._limit}


def _validate(*, queue: str, limit: int, **extra) -> None:
    if not isinstance(queue, str):
        raise TypeError(f"queue must be a string, got {queue}")
    if not queue.strip():
        raise ValueError("queue must not be blank")

    if limit < 1:
        raise ValueError(f"Queue '{queue}' limit must be positive")


async def _get_jobs(producer: Producer) -> list[Job]:
    demand = producer._limit - len(producer._running_jobs)

    if demand > 0:
        return await producer._query.fetch_jobs(
            demand=demand,
            queue=producer._queue,
            node=producer._node,
            uuid=producer._uuid,
        )
    else:
        return []


class LocalDispatcher:
    def dispatch(self, producer: Producer, job: Job) -> asyncio.Task:
        return asyncio.create_task(producer._execute(job))


@dataclass(frozen=True, slots=True)
class QueueInfo:
    """Information about a queue's runtime state."""

    limit: int
    """The concurrency limit for this queue."""

    meta: dict[str, Any]
    """Queue metadata including extension options (global_limit, partition, etc.)."""

    node: str
    """The node name where this queue is running."""

    paused: bool
    """Whether the queue is currently paused."""

    queue: str
    """The queue name."""

    running: list[int]
    """List of currently executing job IDs."""

    started_at: datetime | None
    """When the queue was started."""


class Producer:
    def __init__(
        self,
        *,
        debounce_interval: float = 0.005,
        dispatcher: Any = None,
        limit: int = 10,
        paused: bool = False,
        queue: str = "default",
        name: str,
        node: str,
        notifier: Notifier,
        query: Query,
        **extra,
    ) -> None:
        self._debounce_interval = debounce_interval
        self._dispatcher = dispatcher or LocalDispatcher()
        self._extra = extra
        self._limit = limit
        self._name = name
        self._node = node
        self._notifier = notifier
        self._paused = paused
        self._query = query
        self._queue = queue

        self._validate()

        self._init_lock = asyncio.Lock()
        self._last_fetch_time = 0.0
        self._listen_token = None
        self._loop_task = None
        self._notified = asyncio.Event()
        self._pending_acks = []
        self._running_jobs = {}
        self._started_at = None
        self._uuid = str(uuid4())

    def _validate(self, **opts) -> None:
        params = {"queue": self._queue, "limit": self._limit, **self._extra}
        merged = {**params, **opts}

        use_ext("producer.validate", _validate, **merged)

    async def start(self) -> None:
        async with self._init_lock:
            self._started_at = datetime.now(timezone.utc)

            await self._query.insert_producer(
                uuid=self._uuid,
                name=self._name,
                node=self._node,
                queue=self._queue,
                meta=use_ext("producer.init", _init, self),
            )

            self._listen_token = await self._notifier.listen(
                "signal", self._on_signal, wait=False
            )

            self._loop_task = asyncio.create_task(
                self._loop(), name=f"oban-producer-{self._queue}"
            )

    async def stop(self) -> None:
        async with self._init_lock:
            if not self._listen_token or not self._loop_task:
                return

            try:
                await self._ack_jobs()
            except Exception:
                logger.debug(
                    "Failed to flush pending ACKs for producer %s during shutdown",
                    self._uuid,
                )

            self._loop_task.cancel()

            await self._notifier.unlisten(self._listen_token)

            running_tasks = [task for (_job, task) in self._running_jobs.values()]

            if running_tasks:
                try:
                    now = datetime.now(timezone.utc).isoformat()
                    await self._query.update_producer(
                        uuid=self._uuid,
                        meta={"paused": True, "shutdown_started_at": now},
                    )
                except Exception:
                    logger.debug(
                        "Failed to update producer %s during shutdown", self._uuid
                    )

            await asyncio.gather(
                self._loop_task,
                *running_tasks,
                return_exceptions=True,
            )

            try:
                await self._query.delete_producer(self._uuid)
            except Exception:
                logger.debug(
                    "Failed to delete producer %s during shutdown, will be cleaned up asynchronously",
                    self._uuid,
                )

    def notify(self) -> None:
        self._notified.set()

    async def pause(self) -> None:
        meta = use_ext("producer.pause", _pause, self)

        await self._query.update_producer(uuid=self._uuid, meta=meta)

    async def resume(self) -> None:
        meta = use_ext("producer.resume", _resume, self)

        await self._query.update_producer(uuid=self._uuid, meta=meta)

        self.notify()

    async def scale(self, **kwargs: Any) -> None:
        self._validate(**kwargs)

        meta = use_ext("producer.scale", _scale, self, **kwargs)

        await self._query.update_producer(uuid=self._uuid, meta=meta)

        self.notify()

    def check(self) -> QueueInfo:
        return QueueInfo(
            limit=self._limit,
            meta=self._extra,
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
                await self._debounce()
                await self._produce()
            except asyncio.CancelledError:
                break
            except Exception:
                logger.exception("Error in producer for queue %s", self._queue)

    async def _debounce(self) -> None:
        now = asyncio.get_event_loop().time()
        elapsed = now - self._last_fetch_time

        if elapsed < self._debounce_interval:
            await asyncio.sleep(self._debounce_interval - elapsed)

        self._last_fetch_time = asyncio.get_event_loop().time()

    async def _produce(self) -> None:

        if self._paused or (self._limit - len(self._running_jobs)) <= 0:
            return

        jobs = await self._get_jobs()

        for job in jobs:
            task = self._dispatcher.dispatch(self, job)
            task.add_done_callback(
                lambda _, job_id=job.id: self._on_job_complete(job_id)
            )

            self._running_jobs[job.id] = (job, task)

    async def _ack_jobs(self):
        with telemetry.span("oban.producer.ack", {"queue": self._queue}) as context:
            if self._pending_acks:
                acked_ids = await self._query.ack_jobs(self._pending_acks)
                acked_set = set(acked_ids)

                self._pending_acks = [
                    ack for ack in self._pending_acks if ack.id not in acked_set
                ]
            else:
                acked_ids = []

            context.add({"count": len(acked_ids)})

    async def _get_jobs(self):
        with telemetry.span("oban.producer.get", {"queue": self._queue}) as context:
            jobs = await use_ext("producer.get_jobs", _get_jobs, self)

            context.add({"count": len(jobs)})

            return jobs

    async def _execute(self, job: Job) -> None:
        job._cancellation = asyncio.Event()

        executor = await Executor(job=job, safe=True).execute()

        self._pending_acks.append(executor.action)

    def _on_job_complete(self, job_id: int) -> None:
        self._running_jobs.pop(job_id, None)

        self.notify()

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

                if job_id in self._running_jobs:
                    (job, _task) = self._running_jobs[job_id]

                    job._cancellation.set()

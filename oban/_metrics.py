from __future__ import annotations

import asyncio
import math
import time
from collections import defaultdict
from threading import Lock
from typing import TYPE_CHECKING, Any

from . import telemetry
from .job import JobState
from ._scheduler import ScheduledEntry, scheduled_entries
from ._worker import worker_name

if TYPE_CHECKING:
    from ._leader import Leader
    from ._notifier import Notifier
    from ._producer import Producer, QueueInfo
    from ._query import Query


# DDSketch with 2% relative error constants. This error rate matches what the `Sketch`
# module in `oban_met` uses and must be kept in sync.
ERROR = 0.02
GAMMA = (1 + ERROR) / (1 - ERROR)
INV_LOG_GAMMA = 1.0 / math.log(GAMMA)


def _validate(*, interval: float) -> None:
    if interval <= 0:
        raise ValueError("interval must be positive")


def _build_gauge(values: list[int]) -> dict[str, Any]:
    return {"data": [sum(values)]}


def _compute_bin(value: int) -> int:
    return math.ceil(math.log(value) * INV_LOG_GAMMA)


def _build_sketch(values: list[int]) -> dict[str, Any]:
    bins = defaultdict(int)

    for value in values:
        clamped = max(1, abs(value))
        bins[_compute_bin(clamped)] += 1

    return {"data": dict(bins.items()), "size": len(values)}


ALL_STATES = list(JobState)


class Metrics:
    def __init__(
        self,
        *,
        leader: Leader,
        name: str,
        node: str,
        notifier: Notifier,
        producers: dict[str, Producer],
        query: Query,
        cronitor_interval: float = 30.0,
        estimate_limit: int = 50_000,
        interval: float = 1.0,
    ) -> None:
        self._leader = leader
        self._name = name
        self._node = node
        self._notifier = notifier
        self._producers = producers
        self._query = query
        self._cronitor_interval = cronitor_interval
        self._estimate_limit = estimate_limit
        self._interval = interval

        self._buffer = defaultdict(list)
        self._buffer_lock = Lock()
        self._counts = []
        self._cronitor_counter = 0
        self._previous_counts = {}
        self._handler_id = f"oban-metrics-{name}"
        self._loop_task = None

        _validate(interval=interval)

    async def start(self) -> None:
        telemetry.attach(
            self._handler_id,
            ["oban.job.stop", "oban.job.exception"],
            self._handle_job_event,
        )

        self._loop_task = asyncio.create_task(self._loop(), name="oban-metrics")

    async def stop(self) -> None:
        telemetry.detach(self._handler_id)

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
                await self.broadcast()
                await asyncio.sleep(self._interval)
            except asyncio.CancelledError:
                break
            except Exception:
                pass

    async def broadcast(self) -> None:
        await self._gather_counts()
        await self._broadcast_checks()
        await self._broadcast_metrics()

        self._cronitor_counter += 1
        if self._cronitor_counter >= self._cronitor_interval / self._interval:
            await self._broadcast_crontab()
            self._cronitor_counter = 0

    def _handle_job_event(self, _name: str, meta: dict[str, Any]) -> None:
        job = meta["job"]
        state = meta["state"]
        queue = job.queue
        worker = job.worker

        exec_time = meta["duration"]
        wait_time = meta["queue_time"]

        with self._buffer_lock:
            self._buffer[("exec_time", state, queue, worker)].append(exec_time)
            self._buffer[("wait_time", state, queue, worker)].append(wait_time)
            self._buffer[("exec_count", state, queue, worker)].append(1)

    async def _gather_counts(self) -> None:
        if not self._leader.is_leader:
            return

        exact_states = []
        estimate_states = []

        for state in ALL_STATES:
            if self._previous_counts.get(state, 0) < self._estimate_limit:
                exact_states.append(state)
            else:
                estimate_states.append(state)

        exact_counts = []
        estimated_counts = []

        if exact_states:
            exact_counts = await self._query.count_jobs(exact_states)

        if estimate_states:
            estimated_counts = await self._query.estimate_counts(estimate_states)

        all_counts = exact_counts + estimated_counts

        self._previous_counts = {}
        for state, _queue, count in all_counts:
            self._previous_counts[state] = self._previous_counts.get(state, 0) + count

        self._counts = all_counts

    async def _broadcast_checks(self) -> None:
        checks = [
            self._check_to_dict(prod.check()) for prod in self._producers.values()
        ]

        if checks:
            await self._notifier.notify("gossip", {"checks": checks})

    async def _broadcast_metrics(self) -> None:
        with self._buffer_lock:
            buffer = self._buffer
            counts = self._counts

            self._buffer = defaultdict(list)
            self._counts = []

        if not buffer and not counts:
            return

        metrics = []

        for (series, state, queue, worker), values in buffer.items():
            if series == "exec_count":
                value = _build_gauge(values)
            else:
                value = _build_sketch(values)

            metrics.append(
                {
                    "series": series,
                    "state": state,
                    "queue": queue,
                    "worker": worker,
                    "value": value,
                }
            )

        for state, queue, count in counts:
            metrics.append(
                {
                    "series": "full_count",
                    "state": state,
                    "queue": queue,
                    "value": _build_gauge([count]),
                }
            )

        await self._notifier.notify(
            "metrics",
            {
                "metrics": metrics,
                "name": self._name,
                "node": self._node,
                "time": int(time.time()),
            },
        )

    async def _broadcast_crontab(self) -> None:
        entries = scheduled_entries()

        if not entries:
            return

        crontab = [self._entry_to_list(entry) for entry in entries]

        await self._notifier.notify(
            "cronitor",
            {"crontab": crontab, "name": self._name, "node": self._node},
        )

    def _check_to_dict(self, check: QueueInfo) -> dict[str, Any]:
        started_at = check.started_at.isoformat() if check.started_at else None

        return {
            "node": check.node,
            "name": self._name,
            "queue": check.queue,
            "local_limit": check.limit,
            "paused": check.paused,
            "running": check.running,
            "started_at": started_at,
        }

    def _entry_to_list(self, entry: ScheduledEntry) -> list[Any]:
        opts = {}

        if entry.timezone:
            opts["timezone"] = str(entry.timezone)

        return [entry.expression.input, worker_name(entry.worker_cls), opts]

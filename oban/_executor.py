from __future__ import annotations

import time
import traceback

from typing import TYPE_CHECKING

from . import telemetry
from ._backoff import jittery_clamped
from ._worker import resolve_worker
from .types import Cancel, Snooze

if TYPE_CHECKING:
    from .job import Job


class Executor:
    def __init__(self, job: Job, safe: bool = True):
        self.job = job
        self.safe = safe

        self.action = None
        self.result = None
        self.status = None
        self.worker = None

        self._start_time = None
        self._traceback = None

    async def execute(self) -> Executor:
        self._report_started()
        await self._process()
        self._record_stopped()
        self._report_stopped()
        self._reraise_unsafe()

        return self

    def _report_started(self) -> None:
        self._start_time = time.monotonic_ns()

        telemetry.execute(
            "oban.job.start",
            {"job": self.job, "monotonic_time": self._start_time},
        )

    async def _process(self) -> None:
        try:
            self.worker = resolve_worker(self.job.worker)()
            self.result = await self.worker.process(self.job)
        except Exception as error:
            self.result = error
            self._traceback = traceback.format_exc()

    def _record_stopped(self) -> None:
        match self.result:
            case Exception() as error:
                if self.job.attempt >= self.job.max_attempts:
                    self.action = ("discard", error)
                    self.status = "discarded"
                else:
                    self.action = ("error", error, self._retry_backoff())
                    self.status = "retryable"

            case Snooze(seconds=seconds):
                self.action = ("snooze", seconds)
                self.status = "scheduled"

            case Cancel(reason=reason):
                self.action = ("cancel", reason)
                self.status = "cancelled"

            case _:
                self.action = "complete"
                self.status = "completed"

    def _report_stopped(self) -> None:
        stop_time = time.monotonic_ns()

        meta = {
            "monotonic_time": stop_time,
            "duration": stop_time - self._start_time,
            "queue_time": self._queue_time(),
            "job": self.job,
            "state": self.status,
        }

        if self.status in ("retryable", "discarded"):
            error_meta = {
                "error_message": str(self.result),
                "error_type": type(self.result).__name__,
                "traceback": self._traceback,
            }

            telemetry.execute("oban.job.exception", {**meta, **error_meta})
        else:
            telemetry.execute("oban.job.stop", meta)

    def _reraise_unsafe(self) -> None:
        if not self.safe and self.status in ("retryable", "discarded"):
            raise self.result

    def _retry_backoff(self) -> int:
        if hasattr(self.worker, "backoff"):
            return self.worker.backoff(self.job)
        else:
            return jittery_clamped(self.job.attempt, self.job.max_attempts)

    def _queue_time(self) -> int:
        attempted_at = self.job.attempted_at
        scheduled_at = self.job.scheduled_at

        if attempted_at and scheduled_at:
            delta = (attempted_at - scheduled_at).total_seconds()

            return int(delta * 1_000_000_000)
        else:
            return 0

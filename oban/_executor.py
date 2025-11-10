from __future__ import annotations

import time
import traceback

from dataclasses import dataclass
from datetime import datetime, timezone
from typing import TYPE_CHECKING

from . import telemetry
from ._backoff import jittery_clamped
from ._worker import resolve_worker
from .types import Cancel, JobState, Snooze

if TYPE_CHECKING:
    from .job import Job


@dataclass
class AckAction:
    id: int
    state: str
    attempt_change: int | None = None
    error: dict | None = None
    meta: dict | None = None
    schedule_in: int | None = None


class Executor:
    def __init__(self, job: Job, safe: bool = True):
        self.job = job
        self.safe = safe

        self.action = None
        self.result = None
        self.worker = None

        self._start_time = time.monotonic_ns()
        self._traceback = None

    async def execute(self) -> Executor:
        self._report_started()
        await self._process()
        self._record_stopped()
        self._report_stopped()
        self._reraise_unsafe()

        return self

    @property
    def status(self) -> JobState | None:
        if self.action:
            return self.action.state

    def _report_started(self) -> None:
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
        # This is largely for type checking, as executing an unpersisted job wouldn't happen
        # during actual job processing.
        if self.job.id is None:
            self.job.id = 0

        match self.result:
            case Exception() as error:
                if self.job.attempt >= self.job.max_attempts:
                    self.action = AckAction(
                        id=self.job.id,
                        state="discarded",
                        error=self._format_error(error),
                    )
                else:
                    self.action = AckAction(
                        id=self.job.id,
                        state="retryable",
                        error=self._format_error(error),
                        schedule_in=self._retry_backoff(),
                    )

            case Snooze(seconds=seconds):
                self.action = AckAction(
                    id=self.job.id,
                    attempt_change=-1,
                    state="scheduled",
                    schedule_in=seconds,
                    meta={"snoozed": self.job.meta.get("snoozed", 0) + 1},
                )

            case Cancel(reason=reason):
                self.action = AckAction(
                    id=self.job.id, state="cancelled", error=self._format_error(reason)
                )

            case _:
                self.action = AckAction(id=self.job.id, state="completed")

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
        if not self.safe and isinstance(self.result, BaseException):
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

    def _format_error(self, error: Exception | str) -> dict:
        if isinstance(error, str):
            error_str = error
        else:
            error_str = repr(error)

        return {
            "attempt": self.job.attempt,
            "at": datetime.now(timezone.utc).isoformat(),
            "error": error_str,
        }

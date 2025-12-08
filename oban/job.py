"""Job dataclass representing a unit of work to be processed.

Jobs hold all the information needed to execute a task: the worker to run, arguments
to pass, scheduling options, and metadata for tracking execution state.
"""

from __future__ import annotations

import asyncio
from dataclasses import dataclass, field, replace
from datetime import datetime, timedelta, timezone
from enum import StrEnum
from typing import Any, TypeVar

import orjson

from ._extensions import use_ext
from ._recorded import encode_recorded


class JobState(StrEnum):
    """Lifecycle state of a job.

    - AVAILABLE: Ready to be executed
    - CANCELLED: Explicitly cancelled
    - COMPLETED: Successfully finished
    - DISCARDED: Exceeded max attempts
    - EXECUTING: Currently running
    - RETRYABLE: Failed but will be retried
    - SCHEDULED: Scheduled to run in the future
    - SUSPENDED: Not currently runnable
    """

    AVAILABLE = "available"
    CANCELLED = "cancelled"
    COMPLETED = "completed"
    DISCARDED = "discarded"
    EXECUTING = "executing"
    RETRYABLE = "retryable"
    SCHEDULED = "scheduled"
    SUSPENDED = "suspended"


T = TypeVar("T")


@dataclass(frozen=True, slots=True)
class Snooze:
    """Reschedule a job to run again after a delay.

    Return this from a worker's process method to put the job back in the queue
    with a delayed scheduled_at time.

    Example:
        >>> async def process(self, job):
        ...     if not ready_to_process():
        ...         return Snooze(60)  # Try again in 60 seconds
    """

    seconds: int


@dataclass(frozen=True, slots=True)
class Cancel:
    """Cancel a job and stop processing.

    Return this from a worker's process method to mark the job as cancelled.
    The reason is stored in the job's errors list.

    Example:
        >>> async def process(self, job):
        ...     if job.cancelled():
        ...         return Cancel("Job was cancelled by user")
    """

    reason: str


RECORDED_LIMIT = 64_000_000  # 64MB default limit


@dataclass(slots=True)
class Record:
    """Record a value to be stored with the completed job.

    Return this from a worker's process method to store a value in the job's
    meta field. The value is encoded using Erlang term format for compatibility
    with Oban Pro.

    Args:
        value: The value to record. Must be serializable by erlpack.
        limit: Maximum size in bytes for the encoded value. Defaults to 64MB.

    Raises:
        ValueError: If the encoded value exceeds the size limit.

    Example:
        >>> async def process(self, job):
        ...     result = await compute_something()
        ...     return Record(result)

        >>> # With custom size limit
        >>> return Record(large_result, limit=32_000_000)
    """

    value: Any
    limit: int = RECORDED_LIMIT
    encoded: str = field(init=False, repr=False)

    def __post_init__(self):
        encoded = encode_recorded(self.value)

        if len(encoded) > self.limit:
            raise ValueError(
                f"recorded value is {len(encoded)} bytes, exceeds limit of {self.limit}"
            )

        setattr(self, "encoded", encoded)


type Result[T] = Cancel | Snooze | Record | T | None


TIMESTAMP_FIELDS = [
    "inserted_at",
    "attempted_at",
    "cancelled_at",
    "completed_at",
    "discarded_at",
    "scheduled_at",
]


@dataclass(slots=True)
class Job:
    worker: str
    id: int | None = None
    state: JobState = JobState.AVAILABLE
    queue: str = "default"
    attempt: int = 0
    max_attempts: int = 20
    priority: int = 0
    args: dict[str, Any] = field(default_factory=dict)
    meta: dict[str, Any] = field(default_factory=dict)
    errors: list[str] = field(default_factory=list)
    tags: list[str] = field(default_factory=list)
    attempted_by: list[str] = field(default_factory=list)
    inserted_at: datetime | None = None
    attempted_at: datetime | None = None
    cancelled_at: datetime | None = None
    completed_at: datetime | None = None
    discarded_at: datetime | None = None
    scheduled_at: datetime | None = None

    # Virtual fields for runtime state (not persisted to database)
    extra: dict[str, Any] = field(default_factory=dict, repr=False)
    _cancellation: asyncio.Event | None = field(default=None, init=False, repr=False)

    @staticmethod
    def _handle_schedule_in(params: dict[str, Any]) -> None:
        if "schedule_in" in params:
            schedule_in = params.pop("schedule_in")

            if isinstance(schedule_in, (int, float)):
                schedule_in = timedelta(seconds=schedule_in)

            params["scheduled_at"] = datetime.now(timezone.utc) + schedule_in

    def __post_init__(self):
        # Timestamps returned from the database are naive, which prevents comparison against
        # timezone aware datetime instances.
        for key in TIMESTAMP_FIELDS:
            value = getattr(self, key)
            if value is not None and value.tzinfo is None:
                setattr(self, key, value.replace(tzinfo=timezone.utc))

    def __str__(self) -> str:
        worker_parts = self.worker.split(".")
        worker_name = worker_parts[-1] if worker_parts else self.worker

        parts = [
            f"id={self.id}",
            f"worker={worker_name}",
            f"args={orjson.dumps(self.args)}",
            f"queue={self.queue}",
            f"state={self.state}",
        ]

        return f"Job({', '.join(parts)})"

    @classmethod
    def new(cls, **params) -> Job:
        """Create a new job with validation and normalization.

        This is a low-level method for manually constructing jobs. In most cases,
        you should use the `@worker` or `@job` decorators instead, which provide
        a more convenient API via `Worker.new()` and `Worker.enqueue()`.

        Jobs returned from the database are constructed directly and skip
        validation/normalization.

        Args:
            **params: Job field values including:
                - worker: Required. Fully qualified worker class path
                - args: Job arguments (default: {})
                - queue: Queue name (default: "default")
                - priority: Priority 0-9 (default: 0)
                - max_attempts: Maximum retry attempts (default: 20)
                - scheduled_at: When to run the job (default: now)
                - schedule_in: Alternative to scheduled_at. Timedelta or seconds from now
                - tags: List of tags for grouping
                - meta: Arbitrary metadata dictionary

        Returns:
            A validated and normalized Job instance

        Example:
            Manual job creation (not recommended for typical use):

            >>> job = Job.new(
            ...     worker="myapp.workers.EmailWorker",
            ...     args={"to": "user@example.com"},
            ...     queue="mailers",
            ...     schedule_in=60  # Run in 60 seconds
            ... )

            Preferred approach using decorators:

            >>> from oban import worker
            >>>
            >>> @worker(queue="mailers")
            ... class EmailWorker:
            ...     async def process(self, job):
            ...         pass
            >>>
            >>> job = EmailWorker.new({"to": "user@example.com"}, schedule_in=60)
        """
        cls._handle_schedule_in(params)

        job = cls(**params)
        job._normalize_tags()
        job._validate()

        return use_ext("job.after_new", (lambda _job: _job), job)

    def update(self, changes: dict[str, Any]) -> Job:
        """Update this job with the given changes, applying validation and normalization.

        This method creates a new Job instance with the changes applied, then validates
        and normalizes the result. It's used internally by Oban's update_job methods.

        Args:
            changes: Dictionary of field changes. Supports:
                - args: Job arguments
                - max_attempts: Maximum retry attempts
                - meta: Arbitrary metadata dictionary
                - priority: Priority 0-9
                - queue: Queue name
                - scheduled_at: When to run the job
                - schedule_in: Alternative to scheduled_at. Timedelta or seconds from now
                - tags: List of tags for filtering/grouping
                - worker: Fully qualified worker class path

        Returns:
            A new Job instance with changes applied and validated

        Example:
            >>> job.update({"priority": 0, "tags": ["urgent"]})
        """
        self._handle_schedule_in(changes)

        job = replace(self, **changes)
        job._normalize_tags()
        job._validate()

        return job

    def cancelled(self) -> bool:
        """Check if cancellation has been requested for this job.

        Workers can call this method at safe points during execution to check
        if the job should stop processing and return early.

        Returns:
            True if cancellation has been requested, False otherwise

        Example:
            >>> async def process(self, job):
            ...     for item in large_dataset:
            ...         if job.cancelled():
            ...             return Cancel("Job was cancelled")
            ...         await process_item(item)
        """
        if self._cancellation is None:
            return False

        return self._cancellation.is_set()

    def _normalize_tags(self) -> None:
        self.tags = sorted(
            {str(tag).strip().lower() for tag in self.tags if tag and str(tag).strip()}
        )

    def _validate(self) -> None:
        if not self.queue.strip():
            raise ValueError("queue must not be blank")

        if not self.worker.strip():
            raise ValueError("worker must not be blank")

        if self.max_attempts <= 0:
            raise ValueError("max_attempts must be greater than 0")

        if not (0 <= self.priority <= 9):
            raise ValueError("priority must be between 0 and 9")

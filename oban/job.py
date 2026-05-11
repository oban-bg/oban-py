"""Job class representing a unit of work to be processed.

Jobs hold all the information needed to execute a task: the worker to run, arguments
to pass, scheduling options, and metadata for tracking execution state.
"""

from __future__ import annotations

import asyncio
from dataclasses import dataclass, field
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

    An optional `meta` dict is merged into the job's meta on snooze, alongside
    the standard `snoozed` counter.

    Example:
        >>> async def process(self, job):
        ...     if not ready_to_process():
        ...         return Snooze(60)  # Try again in 60 seconds
    """

    seconds: int
    meta: dict | None = None


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


class Job:
    """A unit of work to be processed by a worker.

    Jobs can be created directly via `Job(worker="...")` with validation, or loaded
    from the database via `Job.from_row()` without validation.
    """

    __slots__ = (
        "worker",
        "id",
        "state",
        "queue",
        "attempt",
        "max_attempts",
        "priority",
        "args",
        "meta",
        "errors",
        "tags",
        "attempted_by",
        "inserted_at",
        "attempted_at",
        "cancelled_at",
        "completed_at",
        "discarded_at",
        "scheduled_at",
        "extra",
        "_cancellation",
    )

    def __init__(
        self,
        worker: str,
        *,
        id: int | None = None,
        state: JobState = JobState.AVAILABLE,
        queue: str = "default",
        attempt: int = 0,
        max_attempts: int = 20,
        priority: int = 0,
        args: dict[str, Any] | None = None,
        meta: dict[str, Any] | None = None,
        errors: list[str] | None = None,
        tags: list[str] | None = None,
        attempted_by: list[str] | None = None,
        inserted_at: datetime | None = None,
        attempted_at: datetime | None = None,
        cancelled_at: datetime | None = None,
        completed_at: datetime | None = None,
        discarded_at: datetime | None = None,
        scheduled_at: datetime | None = None,
        schedule_in: timedelta | int | float | None = None,
        extra: dict[str, Any] | None = None,
        _validate: bool = True,
    ) -> None:
        """Create a new job with validation and normalization.

        In most cases, you should use the `@worker` or `@job` decorators instead,
        which provide a more convenient API via `Worker.new()` and `Worker.enqueue()`.

        Args:
            worker: Required. Fully qualified worker class path
            id: Job ID (assigned by database)
            state: Job state (default: AVAILABLE)
            queue: Queue name (default: "default")
            attempt: Current attempt number (default: 0)
            max_attempts: Maximum retry attempts (default: 20)
            priority: Priority 0-9 (default: 0)
            args: Job arguments (default: {})
            meta: Arbitrary metadata dictionary (default: {})
            errors: List of error messages from failed attempts
            tags: List of tags for grouping
            attempted_by: List of node names that attempted this job
            inserted_at: When the job was inserted
            attempted_at: When the job was last attempted
            cancelled_at: When the job was cancelled
            completed_at: When the job completed
            discarded_at: When the job was discarded
            scheduled_at: When to run the job
            schedule_in: Alternative to scheduled_at. Timedelta or seconds from now
            extra: Extra data for runtime use (not persisted)
            _validate: Whether to validate and normalize (default: True)

        Example:
            Manual job creation (not recommended for typical use):

            >>> job = Job(
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
        self.worker = worker
        self.id = id
        self.state = state
        self.queue = queue
        self.attempt = attempt
        self.max_attempts = max_attempts
        self.priority = priority
        self.args = args if args is not None else {}
        self.meta = meta if meta is not None else {}
        self.errors = errors if errors is not None else []
        self.tags = tags if tags is not None else []
        self.attempted_by = attempted_by if attempted_by is not None else []
        self.extra = extra if extra is not None else {}
        self._cancellation: asyncio.Event | None = None

        if schedule_in is not None:
            if isinstance(schedule_in, (int, float)):
                schedule_in = timedelta(seconds=schedule_in)
            scheduled_at = datetime.now(timezone.utc) + schedule_in

        self.inserted_at = inserted_at
        self.attempted_at = attempted_at
        self.cancelled_at = cancelled_at
        self.completed_at = completed_at
        self.discarded_at = discarded_at
        self.scheduled_at = scheduled_at

        # Timestamps from database are naive, ensure they're timezone-aware
        for key in TIMESTAMP_FIELDS:
            value = getattr(self, key)
            if value is not None and value.tzinfo is None:
                setattr(self, key, value.replace(tzinfo=timezone.utc))

        if _validate:
            self._normalize_tags()
            self._do_validate()
            use_ext("job.after_new", lambda _job: None, self)

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

    def update(self, changes: dict[str, Any]) -> Job:
        """Update this job in place with the given changes.

        Applies validation and normalization after updating.

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
            This job instance (for method chaining)

        Example:
            >>> job.update({"priority": 0, "tags": ["urgent"]})
        """
        # Handle schedule_in -> scheduled_at conversion
        if "schedule_in" in changes:
            schedule_in = changes.pop("schedule_in")
            if isinstance(schedule_in, (int, float)):
                schedule_in = timedelta(seconds=schedule_in)
            changes["scheduled_at"] = datetime.now(timezone.utc) + schedule_in

        for key, value in changes.items():
            setattr(self, key, value)

        self._normalize_tags()
        self._do_validate()

        return self

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

    def _do_validate(self) -> None:
        if not self.queue.strip():
            raise ValueError("queue must not be blank")

        if not self.worker.strip():
            raise ValueError("worker must not be blank")

        if self.max_attempts <= 0:
            raise ValueError("max_attempts must be greater than 0")

        if not (0 <= self.priority <= 9):
            raise ValueError("priority must be between 0 and 9")

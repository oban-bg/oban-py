from __future__ import annotations

import asyncio
import json

from dataclasses import asdict, dataclass, field, replace
from datetime import datetime, timedelta, timezone
from typing import Any

from .types import JobState

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
    state: JobState = "available"
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
            f"args={json.dumps(self.args)}",
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
                - tags: List of tags for filtering/grouping
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

        return job

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

    def to_dict(self) -> dict:
        """Convert the job to a dictionary suitable for database storage.

        This method serializes the Job instance into a dictionary format that can be
        stored in the database. It handles JSON encoding of complex fields and normalizes
        timestamps to UTC without timezone information.

        Returns:
            A dictionary with all job fields, where:
                - args, meta, errors, and tags are JSON-encoded strings
                - Timestamps are converted to naive UTC datetimes
                - All other fields are preserved as-is

        Note:
            This is primarily used internally by Oban for database operations.
            The database uses `TIMESTAMP WITHOUT TIME ZONE`, so timezone-aware
            datetimes are converted to UTC and stripped of timezone info to prevent
            implicit timezone conversion by the database.

        Example:
            >>> job = Job.new(
            ...     worker="myapp.workers.EmailWorker",
            ...     args={"to": "user@example.com"},
            ...     queue="mailers"
            ... )
            >>> job_dict = job.to_dict()
            >>> job_dict["args"]  # JSON string
            '{"to": "user@example.com"}'
        """
        data = asdict(self)

        data["args"] = json.dumps(data["args"])
        data["meta"] = json.dumps(data["meta"])
        data["errors"] = json.dumps(data["errors"])
        data["tags"] = json.dumps(data["tags"])
        data["attempted_by"] = data["attempted_by"]

        # Ensure timestamps are written as UTC rather than being implicitly cast to the current
        # timezone. The database uses `TIMESTAMP WITHOUT TIME ZONE` and the value is automatically
        # shifted when the zone is present.
        for key in TIMESTAMP_FIELDS:
            if data[key] is not None and data[key].tzinfo is not None:
                data[key] = data[key].astimezone(timezone.utc).replace(tzinfo=None)

        return data

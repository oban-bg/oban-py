from __future__ import annotations

import json

from dataclasses import asdict, dataclass, field
from datetime import datetime, timezone
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

    def __post_init__(self):
        # Timestamps returned from the database are naive, which prevents comparison against
        # timezone aware datetime instances.
        for key in TIMESTAMP_FIELDS:
            value = getattr(self, key)
            if value is not None and value.tzinfo is None:
                setattr(self, key, value.replace(tzinfo=timezone.utc))

    @classmethod
    def new(cls, **kwargs) -> Job:
        """Create a new job with validation and normalization.

        Use this for creating new jobs. Jobs returned from the database
        are constructed directly and skip validation/normalization.

        Args:
            **kwargs: Job field values

        Returns:
            A validated and normalized Job instance

        Example:
            >>> job = Job.new(worker="myapp.workers.EmailWorker", args={"to": "user@example.com"})
        """
        job = cls(**kwargs)
        job._normalize_tags()
        job._validate()

        return job

    def _normalize_tags(self) -> None:
        self.tags = sorted(
            {str(tag).strip().lower() for tag in self.tags if tag and str(tag).strip()}
        )

    def _validate(self) -> None:
        if not self.worker:
            raise ValueError("worker is required")

        if not (1 <= len(self.queue) <= 128):
            raise ValueError("queue must be between 1 and 128 characters")

        if not (1 <= len(self.worker) <= 128):
            raise ValueError("worker must be between 1 and 128 characters")

        if self.max_attempts <= 0:
            raise ValueError("max_attempts must be greater than 0")

        if not (0 <= self.priority <= 9):
            raise ValueError("priority must be between 0 and 9")

    def to_dict(self) -> dict:
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

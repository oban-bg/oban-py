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
        if self.state == "available" and self.scheduled_at is not None:
            self.state = "scheduled"

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

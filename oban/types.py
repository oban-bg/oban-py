from dataclasses import dataclass
from datetime import datetime
from enum import StrEnum
from typing import TypeVar

T = TypeVar("T")


class JobState(StrEnum):
    """Represents the lifecycle state of a job.

    - AVAILABLE: ready to be executed
    - CANCELLED: explicitly cancelled
    - COMPLETED: successfully finished
    - DISCARDED: exceeded max attempts
    - EXECUTING: currently executing
    - RETRYABLE: failed but will be retried
    - SCHEDULED: scheduled to run in the future
    - SUSPENDED: not available to run currently
    """

    AVAILABLE = "available"
    CANCELLED = "cancelled"
    COMPLETED = "completed"
    DISCARDED = "discarded"
    EXECUTING = "executing"
    RETRYABLE = "retryable"
    SCHEDULED = "scheduled"
    SUSPENDED = "suspended"


@dataclass(frozen=True, slots=True)
class QueueInfo:
    """Information about a queue's runtime state."""

    limit: int
    """The concurrency limit for this queue"""

    node: str
    """The node name where this queue is running"""

    paused: bool
    """Whether the queue is currently paused"""

    queue: str
    """The queue name"""

    running: list[int]
    """List of currently executing job IDs"""

    started_at: datetime
    """When the queue was started"""


# Return Types


@dataclass(frozen=True, slots=True)
class Snooze:
    seconds: int


@dataclass(frozen=True, slots=True)
class Cancel:
    reason: str


type Result[T] = Snooze | Cancel | None

from dataclasses import dataclass
from datetime import datetime
from enum import StrEnum
from typing import Literal, NotRequired, TypedDict, TypeVar

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

    started_at: datetime | None
    """When the queue was started"""


# Uniqueness


class UniqueField(StrEnum):
    """Fields that can be used to determine job uniqueness.

    When checking if a job is unique, these fields determine what aspects of
    the job are compared. Multiple fields can be combined.

    - WORKER: The worker class name
    - QUEUE: The queue
    - ARGS: The job's arguments
    - META: The job's metadata
    """

    WORKER = "worker"
    QUEUE = "queue"
    ARGS = "args"
    META = "meta"


class UniqueGroup(StrEnum):
    """State groups to consider when checking for duplicate jobs.

    Determines which job states are checked when evaluating uniqueness.
    For example, "incomplete" will check against all unfinished jobs.

    - ALL: Check against jobs in any state
    - INCOMPLETE: Check against available, scheduled, executing, and retryable jobs
    - SCHEDULED: Check against only scheduled jobs
    - SUCCESSFUL: Check against only incomplete and completed jobs
    """

    ALL = "all"
    INCOMPLETE = "incomplete"
    SCHEDULED = "scheduled"
    SUCCESSFUL = "successful"


class UniqueOptions(TypedDict, total=False):
    """
    Uniqueness prevents duplicate jobs from being enqueued based on the specified
    criteria. Jobs are considered duplicates if they match on the configured fields
    within the specified period and states.
    
    All fields are optional and have sensible defaults applied when not specified.
    
    Attributes:
        period: Time window in seconds to check for duplicates. Use None for unlimited,
                the default
        fields: List of job fields to compare when checking uniqueness. Defaults to
                "queue", "worker", "args".
        keys: List of specific keys within args or meta to check. If provided, only
              these keys are compared instead of the full args/meta dicts. Defaults
              to None (check all keys).
        group: Which job states to check for duplicates. Defaults to "all".
    
    Examples:
        Simple uniqueness with defaults (infinite period, all states):
            unique=True
    
        Prevent duplicate jobs with same args in a 5 minute window:
            unique={"period": 300}
    
        Only check specific arg keys:
            unique={"period": 60, "fields": ["args"], "keys": ["user_id"]}
    """

    fields: list[UniqueField]
    group: UniqueGroup
    keys: list[str] | None
    period: int | None


# Return Types


@dataclass(frozen=True, slots=True)
class Snooze:
    seconds: int


@dataclass(frozen=True, slots=True)
class Cancel:
    reason: str


type Result[T] = Snooze | Cancel | None

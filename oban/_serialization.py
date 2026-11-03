from __future__ import annotations

import copy
import pickle
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from ._executor import AckAction
    from .job import Job


def serialize_job(job: Job) -> bytes:
    # The `_cancellation` event can't be pickled, so we nullify it at the barrier point.
    job_copy = copy.copy(job)
    job_copy._cancellation = None

    return pickle.dumps(("run", job_copy))


def serialize_cancel(generation: str, job_id: int) -> bytes:
    return pickle.dumps(("cancel", generation, job_id))


def deserialize_command(data: bytes) -> tuple:
    # Data originates from our own worker pool and database, never untrusted input.
    return pickle.loads(data)  # noqa: S301


def serialize_result(generation: str, job_id: int, action: None | AckAction) -> bytes:
    return pickle.dumps((generation, job_id, action))


def deserialize_result(data: bytes) -> tuple[str, int, AckAction]:
    # Data originates from our own worker pool and database, never untrusted input.
    return pickle.loads(data)  # noqa: S301

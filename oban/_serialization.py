from __future__ import annotations

import copy
import pickle
from dataclasses import replace
from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    from ._executor import AckAction
    from .job import Job


def _portable(job: Job) -> Job:
    job_copy = copy.copy(job)
    job_copy._cancellation = None
    job_copy._inbox = None

    return job_copy


def serialize_job(job: Job) -> bytes:
    return pickle.dumps(("run", _portable(job)))


def serialize_cancel(generation: str, job_id: int | None) -> bytes:
    return pickle.dumps(("cancel", generation, job_id))


def serialize_send(generation: str, job_id: int | None, message: Any) -> bytes:
    return pickle.dumps(("send", generation, job_id, message))


def deserialize_command(data: bytes) -> tuple:
    # Data originates from our own worker pool and database, never untrusted input.
    return pickle.loads(data)  # noqa: S301


def serialize_result(generation: str, job_id: int, action: None | AckAction) -> bytes:
    if action is not None:
        action = replace(action, job=_portable(action.job))

    return pickle.dumps((generation, job_id, action))


def deserialize_result(data: bytes) -> tuple[str, int, AckAction]:
    # Data originates from our own worker pool and database, never untrusted input.
    return pickle.loads(data)  # noqa: S301

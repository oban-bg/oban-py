"""Testing helpers for Oban workers and queues.

This module provides utilities for unit testing workers without database interaction.
"""

from __future__ import annotations

import json

from contextlib import contextmanager
from contextvars import ContextVar
from datetime import datetime, timezone
from typing import TYPE_CHECKING

from .job import Job
from .oban import get_instance
from ._worker import resolve_worker, worker_name

if TYPE_CHECKING:
    from .oban import Oban

_testing_mode: ContextVar[str | None] = ContextVar("oban_testing_mode", default=None)


@contextmanager
def mode(testing_mode: str):
    """Temporarily set the testing mode for Oban instances.

    This context manager allows you to override the testing mode for all Oban
    instances within a specific context. Useful for switching modes in individual
    tests without affecting the entire test suite.

    Args:
        testing_mode: The mode to set ("inline" or "manual")

    Yields:
        None

    Example:
        >>> import oban.testing
        >>>
        >>> oban.testing.set_mode("manual")
        >>>
        >>> def test_inline_execution():
        ...     with oban.testing.mode("inline"):
        ...         # Jobs execute immediately in this context
        ...         await EmailWorker.enqueue({"to": "user@example.com"})
    """
    token = _testing_mode.set(testing_mode)

    try:
        yield
    finally:
        _testing_mode.reset(token)


def _get_mode() -> str | None:
    return _testing_mode.get()


async def reset_oban(oban: str | Oban = "oban"):
    """Reset Oban tables between tests.

    Truncates all oban related tables with CASCADE and RESTART IDENTITY. Useful
    for cleaning up between tests when using manual testing mode.

    Args:
        oban: Oban instance name (default: "oban") or Oban instance

    Example:
        >>> from oban.testing import reset_oban
        >>> import pytest
        >>>
        >>> # In your conftest.py
        >>> @pytest.fixture(autouse=True)
        >>> async def _reset_oban_after_test():
        ...     yield
        ...     await reset_oban()
        >>>
        >>> # Or call directly in tests
        >>> async def test_something(oban):
        ...     await oban.enqueue(SomeWorker.new({}))
        ...     # ... test assertions ...
        ...     await reset_oban()
    """
    if isinstance(oban, str):
        oban_instance = get_instance(oban)
    else:
        oban_instance = oban

    await oban_instance._query.reset()


async def all_enqueued(*, oban: str | Oban = "oban", **filters) -> list[Job]:
    """Retrieve all currently enqueued jobs matching a set of filters.

    Only jobs matching all of the provided filters will be returned. Additionally,
    jobs are returned in descending order where the most recently enqueued job will be
    listed first.

    Args:
        oban: Oban instance name (default: "oban") or Oban instance
        **filters: Job fields to match (e.g., worker=EmailWorker, args={"to": "..."},
                   queue="mailers", priority=5). Args supports partial matching.

    Returns:
        List of Job instances matching the filters, in descending order by ID

    Example:
        >>> from oban.testing import all_enqueued
        >>> from app.workers import EmailWorker
        >>>
        >>> # Assert based on only some of a job's args
        >>> jobs = await all_enqueued(worker=EmailWorker)
        >>> assert len(jobs) == 1
        >>> assert jobs[0].args["id"] == 1
        >>>
        >>> # Assert that exactly one job was inserted for a queue
        >>> jobs = await all_enqueued(queue="alpha")
        >>> assert len(jobs) == 1
        >>>
        >>> # Assert that there aren't any jobs enqueued
        >>> assert await all_enqueued() == []
    """
    if isinstance(oban, str):
        oban_instance = get_instance(oban)
    else:
        oban_instance = oban

    if "worker" in filters and not isinstance(filters["worker"], str):
        filters["worker"] = worker_name(filters["worker"])

    jobs = await oban_instance._query.all_jobs(["available", "scheduled"])

    if not filters:
        return jobs

    return [job for job in jobs if _match_filters(job, filters)]


async def assert_enqueued(*, oban: str | Oban = "oban", **filters):
    """Assert that a job matching the given criteria was enqueued.

    This helper queries the database for jobs in 'available' or 'scheduled' state
    that match the provided filters.

    Args:
        oban: Oban instance name (default: "oban") or Oban instance
        **filters: Job fields to match (e.g., worker=EmailWorker, args={"to": "..."},
                   queue="mailers", priority=5). Args supports partial matching.

    Raises:
        AssertionError: If no matching job is found

    Example:
        >>> from oban.testing import assert_enqueued
        >>> from app.workers import EmailWorker
        >>>
        >>> # Assert job was enqueued with specific worker and args
        >>> async def test_signup_sends_email(app):
        ...     await app.post("/signup", json={"email": "user@example.com"})
        ...     await assert_enqueued(worker=EmailWorker, args={"to": "user@example.com"})
        >>>
        >>> # Match on queue alone
        >>> await assert_enqueued(queue="mailers")
        >>>
        >>> # Partial args matching
        >>> await assert_enqueued(worker=EmailWorker, args={"to": "user@example.com"})
        >>>
        >>> # Filter by queue and priority
        >>> await assert_enqueued(worker=EmailWorker, queue="mailers", priority=5)
        >>>
        >>> # Use an alternate oban instance
        >>> await assert_enqueued(worker=BatchWorker, oban="batch")
    """
    matching = await all_enqueued(oban=oban, **filters)

    if not matching:
        all_jobs = await all_enqueued(oban=oban)
        # TODO: Improve the representation of jobs
        raise AssertionError(
            f"Expected a job matching: {filters} to be enqueued. Instead found:\n\n{all_jobs}"
        )


def _match_filters(job: Job, filters: dict) -> bool:
    for key, value in filters.items():
        if key == "args":
            if not _args_match(value, job.args):
                return False
        elif getattr(job, key, None) != value:
            return False

    return True


def _args_match(expected: dict, actual: dict) -> bool:
    for key, value in expected.items():
        if key not in actual or actual[key] != value:
            return False

    return True


def process_job(job: Job):
    """Execute a worker's process method with the given job.

    This helper is designed for unit testing workers in isolation without
    requiring database interaction.

    Args:
        job: A Job instance to process

    Returns:
        The result from the worker's process method (any value is accepted)

    Raises:
        WorkerResolutionError: If the worker cannot be resolved from the job's worker path

    Example:
        >>> from oban import worker
        >>> from oban.testing import process_job
        >>>
        >>> @worker()
        ... class EmailWorker:
        ...     def process(self, job):
        ...         return {"sent": True, "to": job.args["to"]}
        >>>
        >>> def test_email_worker():
        ...     job = EmailWorker.new({"to": "user@example.com", "subject": "Hello"})
        ...     result = process_job(job)
        ...     assert result["sent"] is True
        ...     assert result["to"] == "user@example.com"

        You can also test function-based workers using the @job decorator:

        >>> from oban import job
        >>> from oban.testing import process_job
        >>>
        >>> @job()
        ... def send_notification(user_id: int, message: str):
        ...     return f"Sent '{message}' to user {user_id}"
        >>>
        >>> def test_send_notification():
        ...     job = send_notification.new(123, "Hello World")
        ...     result = process_job(job)
        ...     assert result == "Sent 'Hello World' to user 123"
    """
    now = datetime.now(timezone.utc)

    job.args = json.loads(json.dumps(job.args))
    job.meta = json.loads(json.dumps(job.meta))

    if job.id is None:
        job.id = id(job)

    if job.attempt == 0:
        job.attempt = 1

    if job.attempted_at is None:
        job.attempted_at = now

    if job.scheduled_at is None:
        job.scheduled_at = now

    if job.inserted_at is None:
        job.inserted_at = now

    worker_cls = resolve_worker(job.worker)

    return worker_cls().process(job)

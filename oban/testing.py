"""Testing helpers for Oban workers and queues.

This module provides utilities for unit testing workers without database interaction.
"""

from __future__ import annotations

import asyncio
import json

from contextlib import contextmanager
from contextvars import ContextVar
from datetime import datetime, timedelta, timezone
from typing import TYPE_CHECKING

from ._executor import Executor
from .job import Job
from .oban import get_instance
from ._worker import worker_name

if TYPE_CHECKING:
    from .oban import Oban

_testing_mode = ContextVar[str | None]("oban_testing_mode", default=None)

FAR_FUTURE = timedelta(365 * 100)


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
        oban = get_instance(oban)

    await oban._query.reset()


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
        oban = get_instance(oban)

    if "worker" in filters and not isinstance(filters["worker"], str):
        filters["worker"] = worker_name(filters["worker"])

    jobs = await oban._query.all_jobs(["available", "scheduled"])

    return [job for job in jobs if _match_filters(job, filters)]


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


async def assert_enqueued(*, oban: str | Oban = "oban", timeout: float = 0, **filters):
    """Assert that a job matching the given criteria was enqueued.

    This helper queries the database for jobs in 'available' or 'scheduled' state
    that match the provided filters. With a timeout, it will poll repeatedly until
    a matching job is found or the timeout expires.

    Args:
        oban: Oban instance name (default: "oban") or Oban instance
        timeout: Maximum time to wait for a matching job (in seconds). Default: 0 (no wait)
        **filters: Job fields to match (e.g., worker=EmailWorker, args={"to": "..."},
                   queue="mailers", priority=5). Args supports partial matching.

    Raises:
        AssertionError: If no matching job is found within the timeout

    Example:
        >>> from oban.testing import assert_enqueued
        >>> from app.workers import EmailWorker
        >>>
        >>> # Assert job was enqueued with specific worker and args
        >>> async def test_signup_sends_email(app):
        ...     await app.post("/signup", json={"email": "user@example.com"})
        ...     await assert_enqueued(worker=EmailWorker, args={"to": "user@example.com"})
        >>>
        >>> # Wait up to 0.2 seconds for an async job to be enqueued
        >>> await assert_enqueued(worker=EmailWorker, timeout=0.2)
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

    async def has_matching_jobs():
        jobs = await all_enqueued(oban=oban, **filters)

        return bool(jobs)

    if not await _poll_until(has_matching_jobs, timeout):
        all_jobs = await all_enqueued(oban=oban)
        formatted = "\n".join(f"  {job}" for job in all_jobs)
        timeout_msg = f" within {timeout}s" if timeout > 0 else ""

        raise AssertionError(
            f"Expected a job matching: {filters} to be enqueued{timeout_msg}. Instead found:\n\n{formatted}"
        )


async def refute_enqueued(*, oban: str | Oban = "oban", timeout: float = 0, **filters):
    """Assert that no job matching the given criteria was enqueued.

    This helper queries the database for jobs in 'available' or 'scheduled' state
    that match the provided filters and asserts that none are found. With a timeout,
    it will poll repeatedly during the timeout period to ensure no matching job appears.

    Args:
        oban: Oban instance name (default: "oban") or Oban instance
        timeout: Time to monitor for matching jobs (in seconds). Default: 0 (check once)
        **filters: Job fields to match (e.g., worker=EmailWorker, args={"to": "..."},
                   queue="mailers", priority=5). Args supports partial matching.

    Raises:
        AssertionError: If any matching jobs are found

    Example:
        >>> from oban.testing import refute_enqueued
        >>> from app.workers import EmailWorker
        >>>
        >>> # Assert no email jobs were enqueued
        >>> async def test_no_email_on_invalid_signup(app):
        ...     await app.post("/signup", json={"email": "invalid"})
        ...     await refute_enqueued(worker=EmailWorker)
        >>>
        >>> # Monitor for 0.2 seconds to ensure no async job is enqueued
        >>> await refute_enqueued(worker=EmailWorker, timeout=0.2)
        >>>
        >>> # Refute specific args
        >>> await refute_enqueued(worker=EmailWorker, args={"to": "blocked@example.com"})
        >>>
        >>> # Refute on queue
        >>> await refute_enqueued(queue="mailers")
    """

    async def has_matching_jobs():
        jobs = await all_enqueued(oban=oban, **filters)

        return bool(jobs)

    if await _poll_until(has_matching_jobs, timeout):
        matching = await all_enqueued(oban=oban, **filters)
        formatted = "\n".join(f"  {job}" for job in matching)
        timeout_msg = f" within {timeout}s" if timeout > 0 else ""

        raise AssertionError(
            f"Expected no jobs matching: {filters} to be enqueued{timeout_msg}. Instead found:\n\n{formatted}"
        )


async def drain_queue(
    queue: str = "default",
    oban: str | Oban = "oban",
    with_recursion: bool = True,
    with_safety: bool = False,
    with_scheduled: bool = True,
) -> dict[str, int]:
    """Synchronously execute all available jobs in a queue.

    All execution happens within the current process. Draining a queue from within
    the current process is especially useful for testing, where jobs enqueued by a
    process in sandbox mode are only visible to that process.

    Args:
        queue: Name of the queue to drain
        oban: Oban instance name (default: "oban") or Oban instance
        with_recursion: Whether to drain jobs recursively, or all in a single pass.
                        Either way, jobs are processed sequentially, one at a time.
                        Recursion is required when jobs insert other jobs or depend
                        on the execution of other jobs. Defaults to True.
        with_scheduled: Whether to include scheduled or retryable jobs when draining.
                        In recursive mode, which is the default, this will include snoozed
                        jobs, and may lead to an infinite loop if the job snoozes repeatedly.
                        Defaults to True.
        with_safety: Whether to silently catch and record errors when draining. When
                     False, raised exceptions are immediately propagated to the caller.
                     Defaults to False.

    Returns:
        Dict with counts for each terminal job state (completed, discarded, cancelled,
        scheduled, retryable)

    Example:
        >>> from oban.testing import drain_queue
        >>> from oban import worker
        >>>
        >>> # Drain a queue with jobs
        >>> result = await drain_queue(queue="default")
        >>> # {'completed': 2, 'discarded': 1, 'cancelled': 0, ...}
        >>>
        >>> # Drain without scheduled jobs
        >>> await drain_queue(queue="default", with_scheduled=False)
        >>>
        >>> # Drain without safety and assert an error is raised
        >>> import pytest
        >>> with pytest.raises(RuntimeError):
        ...     await drain_queue(queue="risky", with_safety=False)
        >>>
        >>> # Drain without recursion (jobs that enqueue other jobs)
        >>> await drain_queue(queue="default", with_recursion=False)
    """
    if isinstance(oban, str):
        oban = get_instance(oban)

    summary = {
        "cancelled": 0,
        "completed": 0,
        "discarded": 0,
        "retryable": 0,
        "scheduled": 0,
    }

    while True:
        if with_scheduled:
            before = datetime.now(timezone.utc) + FAR_FUTURE
            await oban._query.stage_jobs(limit=1000, queues=[queue], before=before)

        match await oban._query.fetch_jobs(
            demand=1, queue=queue, node="drain", uuid="drain"
        ):
            case []:
                break
            case [job]:
                executor = await Executor(job=job, safe=with_safety).execute()

                if executor.action is not None:
                    await oban._query.ack_jobs([executor.action])

                summary[executor.status] += 1

        if not with_recursion:
            break

    return summary


async def _poll_until(condition, timeout: float, interval: float = 0.01) -> bool:
    if timeout <= 0:
        return await condition()

    elapsed = 0.0

    while elapsed < timeout:
        elapsed += interval

        if await condition():
            return True

        await asyncio.sleep(interval)

    return await condition()


def process_job(job: Job):
    """Execute a worker's process method with the given job.

    This helper is designed for unit testing workers in isolation without
    requiring database interaction.

    Args:
        job: A Job instance to process

    Returns:
        The result from the worker's process method (any value is accepted)

    Raises:
        Any exception raised by the worker if it fails

    Example:
        >>> from oban import worker
        >>> from oban.testing import process_job
        >>>
        >>> @worker()
        ... class EmailWorker:
        ...     async def process(self, job):
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

    async def _execute():
        executor = await Executor(job, safe=False).execute()

        return executor.result

    try:
        asyncio.get_running_loop()
        return _execute()
    except RuntimeError:
        return asyncio.run(_execute())

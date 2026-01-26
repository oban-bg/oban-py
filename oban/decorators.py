"""
Decorators for creating Oban workers and jobs.

This module provides two decorators for making your code enqueueable:

- `@worker` For classes with a `process` method
- `@job` For wrapping functions as jobs
"""

import inspect
from functools import wraps
from typing import Any, Callable

from ._extensions import use_ext
from ._worker import register_worker, worker_name
from ._scheduler import register_scheduled
from .job import Job, Result

JOB_FIELDS = set(Job.__slots__) - {"extra", "_cancellation"} | {"schedule_in"}


def worker(*, oban: str = "oban", cron: str | dict | None = None, **overrides):
    """Decorate a class to make it a viable worker.

    The decorator adds worker functionality to a class, including job creation
    and enqueueing methods. The decorated class must implement a `process` method.

    For simpler function-based jobs, consider using @job instead.

    Args:
        oban: Name of the Oban instance to use (default: "oban")
        cron: Optional cron configuration for periodic execution. Can be:
              - A string expression (e.g., "0 0 \\* \\* \\*" or "@daily")
              - A dict with "expr" and optional "timezone" keys (timezone as string)
        **overrides: Configuration options for the worker (queue, priority, etc.)

    Returns:
        A decorator function that can be applied to worker classes

    Example:
        >>> from oban import Oban, worker
        >>>
        >>> # Create an Oban instance with a specific name
        >>> oban_instance = Oban(name="oban", queues={"default": 10, "mailers": 5})
        >>>
        >>> @worker(queue="mailers", priority=1)
        ... class EmailWorker:
        ...     async def process(self, job):
        ...         print(f"Sending email: {job.args}")
        ...         return None
        >>>
        >>> # Create a job without enqueueing
        >>> job = EmailWorker.new({"to": "user@example.com", "subject": "Hello"})
        >>> print(job.queue)  # "mailers"
        >>> print(job.priority)  # 1
        >>>
        >>> # Create and enqueue a job
        >>> job = EmailWorker.enqueue(
        ...     {"to": "admin@example.com", "subject": "Alert"},
        ...     priority=5  # Override default priority
        ... )
        >>> print(job.priority)  # 5
        >>>
        >>> # Schedule a job to run in 5 minutes using a timedelta
        >>> from datetime import timedelta
        >>> job = EmailWorker.enqueue(..., schedule_in=timedelta(minutes=5))
        >>>
        >>> # Schedule a job to run in 60 seconds
        >>> job = EmailWorker.enqueue(...,schedule_in=60)
        >>>
        >>> # Periodic worker that runs daily at midnight
        >>> @worker(queue="cleanup", cron="@daily")
        ... class DailyCleanup:
        ...     async def process(self, job):
        ...         print("Running daily cleanup")
        ...         return None
        >>>
        >>> # Periodic worker with timezone
        >>> @worker(queue="reports", cron={"expr": "0 9 \\* \\* MON-FRI", "timezone": "America/New_York"})
        ... class BusinessHoursReport:
        ...     async def process(self, job):
        ...         print("Running during NY business hours")
        ...         return None
        >>>
        >>> # Workers can also be created without args
        >>> job = DailyCleanup.new()  # args defaults to {}
        >>>
        >>> # Custom backoff for retries
        >>> @worker(queue="default")
        ... class CustomBackoffWorker:
        ...     async def process(self, job):
        ...         return None
        ...
        ...     def backoff(self, job):
        ...         # Simple linear backoff at 2x the attempt number
        ...         return 2 * job.attempt

    Note:
        The worker class must implement a ``process(self, job: Job) -> Result[Any]`` method.
        If not implemented, a NotImplementedError will be raised when called.

        Optionally implement a ``backoff(self, job: Job) -> int`` method to customize
        retry delays. If not provided, uses Oban's default jittery clamped backoff.
    """

    def decorate(cls: type) -> type:
        if not hasattr(cls, "process"):

            async def process(self, job: Job) -> Result[Any]:
                raise NotImplementedError("Worker must implement process method")

            setattr(cls, "process", process)

        @classmethod
        def new(cls, args: dict[str, Any] | None = None, /, **params) -> Job:
            merged = {**cls._opts, **params}
            extras = {
                key: merged.pop(key) for key in list(merged) if key not in JOB_FIELDS
            }

            if extras:
                merged["extra"] = extras

            return Job(worker_name(cls), args=args or {}, **merged)

        @classmethod
        async def enqueue(
            cls, args: dict[str, Any] | None = None, /, **overrides
        ) -> Job:
            from .oban import get_instance

            job = cls.new(args, **overrides)

            return await get_instance(cls._oban_name).enqueue(job)

        setattr(cls, "_opts", overrides)
        setattr(cls, "_oban_name", oban)
        setattr(cls, "new", new)
        setattr(cls, "enqueue", enqueue)

        register_worker(cls)

        use_ext("worker.after_register", lambda _cls: None, cls)

        if cron:
            register_scheduled(cron, cls)

        return cls

    return decorate


def job(*, oban: str = "oban", cron: str | dict | None = None, **overrides):
    """Decorate a function to make it an Oban job.

    The decorated function's signature is preserved for new() and enqueue().

    Use @job for simple function-based tasks where you don't need access to
    job metadata such as the attempt, past errors.

    Args:
        oban: Name of the Oban instance to use (default: "oban")
        cron: Optional cron configuration for periodic execution. Can be:
              - A string expression (e.g., "0 0 \\* \\* \\*" or "@daily")
              - A dict with "expr" and optional "timezone" keys (timezone as string)
        **overrides: Configuration options (queue, priority, etc.)

    Example:
        >>> from oban import job
        >>>
        >>> @job(queue="mailers", priority=1)
        ... def send_email(to: str, subject: str, body: str):
        ...     print(f"Sending to {to}: {subject}")
        >>>
        >>> send_email.enqueue("user@example.com", "Hello", "World")
        >>>
        >>> # Periodic job that runs weekly at midnight
        >>> @job(queue="reports", cron="@weekly")
        ... def generate_weekly_report():
        ...     print("Generating weekly report")
        ...     return {"status": "complete"}
    """

    def decorate(func: Callable[..., Any]) -> type:
        sig = inspect.signature(func)

        class FunctionWorker:
            async def process(self, job: Job):
                result = func(**job.args)
                if inspect.isawaitable(result):
                    return await result
                return result

        FunctionWorker.__name__ = func.__name__  # type: ignore[attr-defined]
        FunctionWorker.__module__ = func.__module__  # type: ignore[attr-defined]
        FunctionWorker.__qualname__ = func.__qualname__  # type: ignore[attr-defined]
        FunctionWorker.__doc__ = func.__doc__

        worker_cls = worker(oban=oban, cron=cron, **overrides)(FunctionWorker)

        original_new = worker_cls.new
        original_enq = worker_cls.enqueue

        @wraps(func)
        def new_with_sig(*args, **kwargs):
            bound = sig.bind(*args, **kwargs)
            bound.apply_defaults()
            return original_new(dict(bound.arguments))

        @wraps(func)
        async def enq_with_sig(*args, **kwargs):
            bound = sig.bind(*args, **kwargs)
            bound.apply_defaults()
            return await original_enq(dict(bound.arguments))

        worker_cls.new = staticmethod(new_with_sig)
        worker_cls.enqueue = staticmethod(enq_with_sig)

        return worker_cls

    return decorate

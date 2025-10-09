"""
Decorators for creating Oban workers and jobs.

This module provides two decorators for making your code enqueueable:
- @worker: For classes with a `process` method
- @job: For wrapping functions as jobs
"""

import inspect
from functools import wraps
from typing import Any, Callable

from .job import Job
from .types import Result
from ._worker import _worker_registry


def worker(*, oban: str = "oban", **overrides):
    """Decorate a class to make it a viable worker.

    The decorator adds worker functionality to a class, including job creation
    and enqueueing methods. The decorated class must implement a `process` method.

    For simpler function-based jobs, consider using @job instead.

    Args:
        oban: Name of the Oban instance to use (default: "oban")
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
        ...     def process(self, job):
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
        >>> # Custom backoff for retries
        >>> @worker(queue="default")
        ... class CustomBackoffWorker:
        ...     def process(self, job):
        ...         return None
        ...
        ...     def backoff(self, job):
        ...         # Simple linear backoff at 2x the attempt number
        ...         return 2 * job.attempt

    Note:
        The worker class must implement a `process(self, job: Job) -> Result[Any]` method.
        If not implemented, a NotImplementedError will be raised when called.

        Optionally implement a `backoff(self, job: Job) -> int` method to customize
        retry delays. If not provided, uses Oban's default jittery clamped backoff.
    """

    def decorate(cls: type) -> type:
        if not hasattr(cls, "process"):

            def process(self, job: Job) -> Result[Any]:
                raise NotImplementedError("Worker must implement process method")

            setattr(cls, "process", process)

        @classmethod
        def new(cls, args: dict[str, Any], /, **overrides) -> Job:
            worker = f"{cls.__module__}.{cls.__qualname__}"
            params = {**cls._opts, **overrides}

            return Job.new(worker=worker, args=args, **params)

        @classmethod
        async def enqueue(cls, args: dict[str, Any], /, **overrides) -> Job:
            from .oban import get_instance

            job = cls.new(args, **overrides)

            return await get_instance(cls._oban_name).enqueue(job)

        setattr(cls, "_opts", overrides)
        setattr(cls, "_oban_name", oban)
        setattr(cls, "new", new)
        setattr(cls, "enqueue", enqueue)

        # Register the worker class
        worker_path = f"{cls.__module__}.{cls.__qualname__}"
        _worker_registry[worker_path] = cls

        return cls

    return decorate


def job(*, oban: str = "oban", **overrides):
    """Decorate a function to make it an Oban job.

    The decorated function's signature is preserved for new() and enqueue().

     Use @job for simple function-based tasks where you don't need access to
     job metadata such as the attempt, past errors.

    Args:
        oban: Name of the Oban instance to use (default: "oban")
        **overrides: Configuration options (queue, priority, etc.)

    Example:
        >>> from oban import job
        >>>
        >>> @job(queue="mailers", priority=1)
        ... def send_email(to: str, subject: str, body: str):
        ...     print(f"Sending to {to}: {subject}")
        >>>
        >>> send_email.enqueue("user@example.com", "Hello", "World")
    """

    def decorate(func: Callable) -> type:
        sig = inspect.signature(func)

        class FunctionWorker:
            def process(self, job: Job):
                return func(**job.args)

        FunctionWorker.__name__ = func.__name__
        FunctionWorker.__module__ = func.__module__
        FunctionWorker.__qualname__ = func.__qualname__
        FunctionWorker.__doc__ = func.__doc__

        worker_cls = worker(oban=oban, **overrides)(FunctionWorker)

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

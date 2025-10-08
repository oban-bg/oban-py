import importlib
from typing import Any

from .job import Job
from .types import Result

_worker_registry: dict[str, type] = {}


def resolve_worker(path: str) -> type:
    """Resolve a worker class by its path.

    First checks the worker registry for dynamically defined workers,
    then falls back to importing the module.
    """
    if path in _worker_registry:
        return _worker_registry[path]

    parts = path.split(".")
    mod_name, cls_name = ".".join(parts[:-1]), parts[-1]

    # TODO: Add try/except checks
    mod = importlib.import_module(mod_name)
    cls = getattr(mod, cls_name)

    return cls


def worker(*, oban: str = "oban", **overrides):
    """Decorate a class to make it a viable worker.

    The decorator adds worker functionality to a class, including job creation
    and enqueueing methods. The decorated class must implement a `perform` method.

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
        ...     def perform(self, job):
        ...         # Send email logic here
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
        ...     def perform(self, job):
        ...         return None
        ...
        ...     def backoff(self, job):
        ...         # Simple linear backoff at 2x the attempt number
        ...         return 2 * job.attempt

    Note:
        The worker class must implement a `perform(self, job: Job) -> Result[Any]` method.
        If not implemented, a NotImplementedError will be raised when called.

        Optionally implement a `backoff(self, job: Job) -> int` method to customize
        retry delays. If not provided, uses Oban's default jittery clamped backoff.
    """

    def decorate(cls: type) -> type:
        if not hasattr(cls, "perform"):

            def perform(self, job: Job) -> Result[Any]:
                raise NotImplementedError("Worker must implement perform method")

            setattr(cls, "perform", perform)

        @classmethod
        def new(cls, args: dict[str, Any], /, **overrides) -> Job:
            worker = f"{cls.__module__}.{cls.__qualname__}"
            params = {**cls._opts, **overrides}

            return Job.new(worker=worker, args=args, **params)

        @classmethod
        def enqueue(cls, args: dict[str, Any], /, **overrides) -> Job:
            from .oban import get_instance

            job = cls.new(args, **overrides)

            return get_instance(cls._oban_name).enqueue(job)

        setattr(cls, "_opts", overrides)
        setattr(cls, "_oban_name", oban)
        setattr(cls, "new", new)
        setattr(cls, "enqueue", enqueue)

        # Register the worker class
        worker_path = f"{cls.__module__}.{cls.__qualname__}"
        _worker_registry[worker_path] = cls

        return cls

    return decorate

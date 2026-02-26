"""Worker protocol and utilities for Oban.

This module defines the Worker Protocol that classes decorated with @worker
implement, providing type hints for static analysis tools.
"""

import importlib
from typing import Any, Protocol

from .job import Job, Result


class Worker(Protocol):
    """Protocol for Oban workers.

    Classes decorated with @worker implement this protocol and gain
    `new()` and `enqueue()` classmethods for job creation and management,
    along with the `process()` method for job execution.

    Example:
        >>> from oban import worker
        >>>
        >>> @worker(queue="default")
        ... class EmailWorker:
        ...     async def process(self, job):
        ...         print(f"Sending email: {job.args}")
        ...
        >>> # Static analysis now understands these:
        >>> job = EmailWorker.new({"to": "user@example.com"})
        >>> await EmailWorker.enqueue({"to": "admin@example.com"})
    """

    _opts: dict[str, Any]
    _oban_name: str

    @classmethod
    def new(cls, args: dict[str, Any] | None = None, /, **params) -> Job:
        """Create a Job instance without enqueueing it.

        Args:
            args: Job arguments dictionary
            **params: Optional overrides for job fields (queue, priority, etc.)

        Returns:
            A Job instance ready to be enqueued
        """
        ...

    @classmethod
    async def enqueue(
        cls, args: dict[str, Any] | None = None, /, conn=None, **overrides
    ) -> Job:
        """Create and enqueue a Job.

        Args:
            args: Job arguments dictionary
            conn: Optional database connection for transactional insertion
            **overrides: Optional overrides for job fields

        Returns:
            The enqueued Job instance
        """
        ...

    async def process(self, job: Job) -> Result[Any]:
        """Process the job.

        This method must be implemented by the worker class. It is called
        by the Oban executor when a job is ready to be processed.

        Args:
            job: The Job instance to process

        Returns:
            The result of job processing (value, None, Cancel, or Snooze)
        """
        ...

    def backoff(self, job: Job) -> int:
        """Calculate the delay before the next retry attempt.

        This method is optional. If not implemented, Oban uses its default
        jittery clamped backoff strategy.

        Args:
            job: The failed Job instance

        Returns:
            Delay in seconds before the next retry
        """
        ...


class WorkerResolutionError(Exception):
    """Raised when a worker class cannot be resolved from a path string.
    This error occurs when the worker resolution process fails due to:
    - Invalid path format
    - Module not found or import errors
    - Class not found in the module
    - Resolved attribute is not a class
    """

    pass


_registry: dict[str, type] = {}


def worker_name(cls: type) -> str:
    """Generate the fully qualified name for a worker class."""
    return f"{cls.__module__}.{cls.__qualname__}"


def register_worker(cls) -> None:
    """Register a worker class for usage later"""
    key = worker_name(cls)
    _registry[key] = cls


def resolve_worker(path: str) -> type:
    """Resolve a worker class by its path.
    Loads worker classes from the local registry, falling back to importing
    the module.

    Args:
        path: Fully qualified class path (e.g., "myapp.workers.EmailWorker")

    Returns:
        The resolved worker class

    Raises:
        WorkerResolutionError: If the worker cannot be resolved
    """
    if path in _registry:
        return _registry[path]

    parts = path.split(".")
    mod_name, cls_name = ".".join(parts[:-1]), parts[-1]

    try:
        mod = importlib.import_module(mod_name)
    except ModuleNotFoundError as error:
        raise WorkerResolutionError(
            f"Module '{mod_name}' not found for worker '{path}'"
        ) from error
    except ImportError as error:
        raise WorkerResolutionError(
            f"Failed to import module '{mod_name}' for worker '{path}'"
        ) from error

    try:
        cls = getattr(mod, cls_name)
    except AttributeError as error:
        raise WorkerResolutionError(
            f"Class '{cls_name}' not found in module '{mod_name}'"
        ) from error

    if not isinstance(cls, type):
        raise WorkerResolutionError(
            f"'{path}' resolved to {type(cls).__name__}, expected a class"
        )

    register_worker(cls)

    return cls

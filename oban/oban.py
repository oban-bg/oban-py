from __future__ import annotations

from typing import Any, Callable, Type
from psycopg_pool import ConnectionPool

from . import _query
from .job import Job
from ._runner import Runner
from ._stager import Stager
from ._worker import worker


class Oban:
    def __init__(
        self,
        *,
        pool: dict[str, Any] | ConnectionPool,
        queues: dict[str, int] | None = None,
        stage_interval: float = 1.0,
    ) -> None:
        """Initialize an Oban instance.

        Args:
            pool: Database connection pool or configuration dict with 'url' key
            queues: Queue names mapped to worker limits (default: {})
            stage_interval: How often to stage scheduled jobs in seconds (default: 1.0)
        """
        queues = queues or {}

        for queue, limit in queues.items():
            if limit < 1:
                raise ValueError(f"Queue '{queue}' limit must be positive")

        if stage_interval <= 0:
            raise ValueError("stage_interval must be positive")

        if isinstance(pool, dict):
            if "url" not in pool:
                raise ValueError("Pool configuration must include 'url'")

            pool["conninfo"] = pool.pop("url")
            pool["open"] = False
            self._pool = ConnectionPool(**pool)
        else:
            self._pool = pool

        self._runners = {
            queue: Runner(oban=self, queue=queue, limit=limit)
            for queue, limit in queues.items()
        }

        self._stager = Stager(
            oban=self, runners=self._runners, stage_interval=stage_interval
        )

    def worker(self, **overrides) -> Callable[[Type], Type]:
        """Create a worker decorator for this Oban instance.

        The decorator adds worker functionality to a class, including job creation
        and enqueueing methods. The decorated class must implement a `perform` method.

        Args:
            **overrides: Configuration options for the worker (queue, priority, etc.)

        Returns:
            A decorator function that can be applied to worker classes

        Example:
            >>> oban = Oban(queues={"default": 10, "mailers": 5})

            >>> @oban.worker(queue="mailers", priority=1)
            ... class EmailWorker:
            ...     def perform(self, job):
            ...         # Send email logic here
            ...         print(f"Sending email: {job.args}")
            ...         return None

            >>> # Create a job without enqueueing
            >>> job = EmailWorker.new({"to": "user@example.com", "subject": "Hello"})
            >>> print(job.queue)  # "mailers"
            >>> print(job.priority)  # 1

            >>> # Create and enqueue a job
            >>> job = EmailWorker.enqueue(
            ...     {"to": "admin@example.com", "subject": "Alert"},
            ...     priority=5  # Override default priority
            ... )
            >>> print(job.priority)  # 5

            >>> # Custom backoff for retries
            >>> @oban.worker(queue="default")
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
        return worker(oban=self, **overrides)

    def start(self) -> Oban:
        self._pool.open()

        for runner in self._runners.values():
            runner.start()

        self._stager.start()

        return self

    def stop(self) -> None:
        self._stager.stop()
        for runner in self._runners.values():
            runner.stop()
        self._pool.close()

    def enqueue(self, job: Job) -> Job:
        with self.get_connection() as conn:
            return _query.insert_job(conn, job)

    def get_connection(self) -> Any:
        """Get a connection from the pool.

        Returns a context manager that yields a connection.

        Usage:
          with oban.get_connection() as conn:
              # use conn
        """

        # TODO: Can we use a connection if we're already in a transaction? Would that be an extra
        # argument to `enqueue`?

        return self._pool.connection()

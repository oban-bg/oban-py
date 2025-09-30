from dataclasses import dataclass, field
from typing import Any, Callable, Dict, Type, Union
from psycopg_pool import ConnectionPool

from . import _query
from .job import Job
from ._runner import Runner
from ._worker import worker


@dataclass
class Oban:
    pool: Union[Dict[str, Any], ConnectionPool]
    queues: Dict[str, int] = field(default_factory=dict)

    def __post_init__(self):
        """Validate configuration after initialization."""
        for queue_name, limit in self.queues.items():
            if limit < 1:
                raise ValueError(f"Queue '{queue_name}' limit must be positive")

        if isinstance(self.pool, dict):
            if "url" not in self.pool:
                raise ValueError("Pool configuration must include 'url'")

            self.pool["conninfo"] = self.pool.pop("url")
            self.pool["open"] = False

            self.pool = ConnectionPool(**self.pool)

    def worker(self, **overrides) -> Callable[[Type], Type]:
        """Create a worker decorator for this Oban instance.

        The decorator adds worker functionality to a class, including job creation
        and enqueueing methods. The decorated class must implement a `perform` method.

        Args:
            **overrides: Configuration options for the worker (queue, priority, etc.)

        Returns:
            A decorator function that can be applied to worker classes

        Example:
            >>> oban_instance = Oban(queues={"default": 10, "mailers": 5})

            >>> @oban_instance.worker(queue="mailers", priority=1)
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

        Note:
            The worker class must implement a `perform(self, job: Job) -> Result[Any]` method.
            If not implemented, a NotImplementedError will be raised when called.
        """
        return worker(oban=self, **overrides)

    def start(self):
        if self.pool and not self.pool._pool:
            self.pool.open()

        # Faking this for a single queue at first
        self._runner = Runner(oban=self, queue="default", limit=10)
        self._runner.start()

        return self

    def stop(self):
        # Stop runner first and wait for it to finish
        if self._runner:
            self._runner.stop()

        # Now safe to close the pool
        if self.pool:
            self.pool.close()

    def enqueue(self, job: Job) -> Job:
        with self.get_connection() as conn:
            return _query.insert_job(conn, job)

    def get_connection(self):
        """Get a connection from the pool.

        Returns a context manager that yields a connection.

        Usage:
          with oban.get_connection() as conn:
              # use conn
        """

        return self.pool.connection()

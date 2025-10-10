from __future__ import annotations

import asyncio
import socket

from typing import Any
from uuid import uuid4

from . import _query
from ._driver import wrap_conn
from .job import Job
from ._runner import Runner
from ._stager import Stager

_instances: dict[str, Oban] = {}
_instances_lock = asyncio.Lock()


class Oban:
    def __init__(
        self,
        *,
        conn: Any,
        name: str = "oban",
        node: str | None = None,
        queues: dict[str, int] | None = None,
        stage_interval: float = 1.0,
    ) -> None:
        """Initialize an Oban instance.

        Args:
            conn: Database connection or pool (e.g., AsyncConnection or AsyncConnectionPool)
            name: Name for this instance in the registry (default: "oban")
            node: Node identifier for this instance (default: socket.gethostname())
            queues: Queue names mapped to worker limits (default: {})
            stage_interval: How often to stage scheduled jobs in seconds (default: 1.0)
        """
        queues = queues or {}

        for queue, limit in queues.items():
            if limit < 1:
                raise ValueError(f"Queue '{queue}' limit must be positive")

        if stage_interval <= 0:
            raise ValueError("stage_interval must be positive")

        self._driver = wrap_conn(conn)
        self._name = name
        self._node = node or socket.gethostname()

        self._runners = {
            queue: Runner(oban=self, queue=queue, limit=limit, uuid=str(uuid4()))
            for queue, limit in queues.items()
        }

        self._stager = Stager(
            oban=self, runners=self._runners, stage_interval=stage_interval
        )

        # TODO: handle async lock or bypass
        _instances[name] = self

    async def __aenter__(self) -> Oban:
        return await self.start()

    async def __aexit__(self, _exc_type, _exc_val, _exc_tb) -> None:
        await self.stop()

    async def start(self) -> Oban:
        for queue, runner in self._runners.items():
            await runner.start()

        await self._stager.start()

        return self

    async def stop(self) -> None:
        await self._stager.stop()

        for runner in self._runners.values():
            await runner.stop()

    async def enqueue(self, job: Job) -> Job:
        """Insert a job into the database for processing.

        Args:
            job: A Job instance created via Worker.new()

        Returns:
            The inserted job with database-assigned values (id, timestamps, state)

        Example:
            >>> from myapp.oban import oban, EmailWorker
            >>>
            >>> job = EmailWorker.new({"to": "user@example.com", "subject": "Welcome"})
            >>> await oban.enqueue(job)

        Note:
            For convenience, you can also use Worker.enqueue() directly:

            >>> await EmailWorker.enqueue({"to": "user@example.com", "subject": "Welcome"})
        """
        async with self.get_connection() as conn:
            result = await _query.insert_jobs(conn, [job])

            return result[0]

    async def enqueue_many(self, jobs: list[Job]) -> list[Job]:
        """Insert multiple jobs into the database in a single operation.

        This is more efficient than calling enqueue() multiple times as it uses a
        single database query to insert all jobs.

        Args:
            jobs: A list of Job instances created via Worker.new()

        Returns:
            The inserted jobs with database-assigned values (id, timestamps, state)

        Example:
            >>> from myapp.oban import oban, EmailWorker
            >>>
            >>> jobs = [
            ...     EmailWorker.new({"to": "user1@example.com"}),
            ...     EmailWorker.new({"to": "user2@example.com"}),
            ...     EmailWorker.new({"to": "user3@example.com"}),
            ... ]
            >>>
            >>> await oban.enqueue_many(jobs)
        """
        async with self.get_connection() as conn:
            return await _query.insert_jobs(conn, jobs)

    def get_connection(self) -> Any:
        """Get a connection from the driver.

        Returns an async context manager that yields a connection.

        Usage:
          async with oban.get_connection() as conn:
              # use conn
        """
        return self._driver.connection()


def get_instance(name: str = "oban") -> Oban:
    """Get an Oban instance from the registry by name.

    Args:
        name: Name of the instance to retrieve (default: "oban")

    Returns:
        The Oban instance

    Raises:
        RuntimeError: If no instance with the given name exists
    """
    instance = _instances.get(name)

    if instance is None:
        raise RuntimeError(f"Oban instance '{name}' not found in registry")

    return instance

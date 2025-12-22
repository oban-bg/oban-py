"""Core class for enqueueing and processing jobs.

This module provides the Oban class for managing queues and processing jobs. Oban can
run in client mode (enqueueing only) or server mode (enqueueing and processing).
"""

from __future__ import annotations

import asyncio
import socket
from collections.abc import Iterable
from typing import Any, Callable

from psycopg_pool import AsyncConnectionPool

from .job import Job
from ._leader import Leader
from ._lifeline import Lifeline
from ._notifier import Notifier, PostgresNotifier
from ._producer import Producer, QueueInfo
from ._pruner import Pruner
from ._query import Query
from ._refresher import Refresher
from ._scheduler import Scheduler
from ._stager import Stager

QueueConfig = int | dict[str, Any]

_instances: dict[str, Oban] = {}


class Oban:
    def __init__(
        self,
        *,
        pool: Any,
        dispatcher: Any = None,
        leadership: bool | None = None,
        lifeline: dict[str, Any] = {},
        name: str | None = None,
        node: str | None = None,
        notifier: Notifier | None = None,
        prefix: str | None = None,
        pruner: dict[str, Any] = {},
        queues: dict[str, QueueConfig] | None = None,
        refresher: dict[str, Any] = {},
        scheduler: dict[str, Any] = {},
        stager: dict[str, Any] = {},
    ) -> None:
        """Initialize an Oban instance.

        Oban can run in two modes:

        - Server mode: When queues are configured, this instance processes jobs.
          Leadership is enabled by default to coordinate cluster-wide operations.
        - Client mode: When no queues are configured, this instance only enqueues jobs.
          Leadership is disabled by default.

        Args:
            pool: Database connection pool (e.g., AsyncConnectionPool)
            leadership: Enable leadership election (default: True if queues configured, False otherwise)
            lifeline: Lifeline config options: interval (default: 60.0)
            name: Name for this instance in the registry (default: "oban")
            node: Node identifier for this instance (default: socket.gethostname())
            notifier: Notifier instance for pub/sub (default: PostgresNotifier with default config)
            prefix: PostgreSQL schema where Oban tables are located (default: "public")
            pruner: Pruning config options: max_age in seconds (default: 86_400.0, 1 day),
                    interval (default: 60.0), limit (default: 20_000).
            queues: Queue names mapped to worker limits (default: {})
            refresher: Refresher config options: interval (default: 15.0), max_age (default: 60.0)
            scheduler: Scheduler config options: timezone (default: "UTC")
            stager: Stager config options: interval (default: 1.0), limit (default: 20_000)
        """
        queues = queues or {}

        if leadership is None:
            leadership = bool(queues)

        self._dispatcher = dispatcher
        self._name = name or "oban"
        self._node = node or socket.gethostname()
        self._prefix = prefix or "public"
        self._query = Query(pool, self._prefix)

        self._notifier = notifier or PostgresNotifier(
            query=self._query, prefix=self._prefix
        )

        self._producers = {
            queue: Producer(
                dispatcher=self._dispatcher,
                query=self._query,
                name=self._name,
                node=self._node,
                notifier=self._notifier,
                queue=queue,
                **self._parse_queue_config(queue, config),
            )
            for queue, config in queues.items()
        }

        self._leader = Leader(
            query=self._query,
            node=self._node,
            name=self._name,
            enabled=leadership,
            notifier=self._notifier,
        )

        self._stager = Stager(
            query=self._query,
            notifier=self._notifier,
            producers=self._producers,
            leader=self._leader,
            **stager,
        )

        self._lifeline = Lifeline(leader=self._leader, query=self._query, **lifeline)
        self._pruner = Pruner(leader=self._leader, query=self._query, **pruner)

        self._refresher = Refresher(
            leader=self._leader,
            producers=self._producers,
            query=self._query,
            **refresher,
        )

        self._scheduler = Scheduler(
            leader=self._leader,
            notifier=self._notifier,
            query=self._query,
            **scheduler,
        )

        self._signal_token = None

        _instances[self._name] = self

    @staticmethod
    def _parse_queue_config(queue: str, config: QueueConfig) -> dict[str, Any]:
        if isinstance(config, int):
            return {"limit": config}
        else:
            return config

    @staticmethod
    async def create_pool(
        dsn: str | None = None,
        *,
        min_size: int | None = None,
        max_size: int | None = None,
        timeout: float | None = None,
    ) -> AsyncConnectionPool:
        """Create a connection pool for use with Oban.

        This is a convenience method that creates and opens an AsyncConnectionPool.
        Configuration is loaded from multiple sources in order of precedence (lowest
        to highest):

        1. oban.toml in the current directory
        2. Environment variables (OBAN_DSN, OBAN_POOL_MIN_SIZE, etc.)
        3. Explicit arguments passed to this method

        The caller is responsible for closing the pool when done.

        Args:
            dsn: PostgreSQL connection string (e.g., "postgresql://localhost/mydb")
            min_size: Minimum number of connections to keep open (default: 1)
            max_size: Maximum number of connections in the pool (default: 10)
            timeout: Timeout in seconds for acquiring a connection (default: 30.0)

        Returns:
            An open AsyncConnectionPool ready for use

        Example:
            Using configuration from oban.toml or environment:

            >>> pool = await Oban.create_pool()
            >>> async with Oban(pool=pool) as oban:
            ...     await oban.enqueue(SomeWorker.new({"key": "value"}))
            >>> await pool.close()

            Overriding the dsn:

            >>> pool = await Oban.create_pool("postgresql://localhost/mydb")
        """
        from ._config import Config

        config = Config.load(
            dsn=dsn,
            pool_min_size=min_size,
            pool_max_size=max_size,
            pool_timeout=timeout,
        )

        return await config.create_pool()

    async def __aenter__(self) -> Oban:
        return await self.start()

    async def __aexit__(self, _exc_type, _exc_val, _exc_tb) -> None:
        await self.stop()

    @property
    def is_leader(self) -> bool:
        """Check if this node is currently the leader.

        Returns False if leadership is not enabled for this instance. Otherwise, it indicates
        whether this instance is acting as leader.

        Example:
            >>> async with Oban(pool=pool, leadership=True) as oban:
            ...     if oban.is_leader:
            ...         # Perform leader-only operation
        """
        return self._leader.is_leader

    def _connection(self):
        return self._query.connection()

    async def start(self) -> Oban:
        """Start the Oban instance and begin processing jobs.

        This starts all internal processes including the notifier, leader election,
        job staging, scheduling, and queue producers. When queues are configured,
        it also verifies that the required database tables exist.

        Returns:
            The started Oban instance for method chaining

        Raises:
            RuntimeError: If required database tables are missing (run migrations first)

        Example:
            For most use cases, prefer using Oban as an async context manager:

            >>> async with Oban(pool=pool, queues={"default": 10}) as oban:
            ...     await oban.enqueue(MyWorker.new({"id": 1}))

            Use explicit start/stop for more control over the lifecycle:

            >>> oban = Oban(pool=pool, queues={"default": 10})
            >>> await oban.start()
            >>> # ... application runs ...
            >>> await oban.stop()
        """
        if self._producers:
            await self._verify_structure()

        if self._dispatcher:
            await self._dispatcher.start()

        tasks = [
            self._notifier.start(),
            self._leader.start(),
            self._stager.start(),
            self._lifeline.start(),
            self._pruner.start(),
            self._refresher.start(),
            self._scheduler.start(),
        ]

        for producer in self._producers.values():
            tasks.append(producer.start())

        await asyncio.gather(*tasks)

        self._signal_token = await self._notifier.listen(
            "signal", self._on_signal, wait=False
        )

        return self

    async def stop(self) -> None:
        """Stop the Oban instance and gracefully shut down all processes.

        This stops all internal processes including queue producers, the notifier,
        leader election, and background tasks. Running jobs are allowed to complete
        before producers fully stop.

        Calling stop on an instance that was never started is safe and returns
        immediately.

        Example:
            For most use cases, prefer using Oban as an async context manager:

            >>> async with Oban(pool=pool, queues={"default": 10}) as oban:
            ...     await oban.enqueue(MyWorker.new({"id": 1}))

            Use explicit start/stop for more control over the lifecycle:

            >>> oban = Oban(pool=pool, queues={"default": 10})
            >>> await oban.start()
            >>> # ... application runs ...
            >>> await oban.stop()
        """
        if not self._signal_token:
            return

        await self._notifier.unlisten(self._signal_token)

        tasks = [
            self._leader.stop(),
            self._stager.stop(),
            self._lifeline.stop(),
            self._pruner.stop(),
            self._refresher.stop(),
            self._scheduler.stop(),
            self._notifier.stop(),
        ]

        for producer in self._producers.values():
            tasks.append(producer.stop())

        await asyncio.gather(*tasks)

        if self._dispatcher:
            await self._dispatcher.stop()

    async def _verify_structure(self) -> None:
        existing = await self._query.verify_structure()

        for table in ["oban_jobs", "oban_leaders", "oban_producers"]:
            if table not in existing:
                raise RuntimeError(
                    f"The '{table}' is missing, run schema installation first."
                )

    async def enqueue(self, job: Job) -> Job:
        """Enqueue a job in the database for processing.

        Args:
            job: A Job instance created via Worker.new()

        Returns:
            The inserted job with database-assigned values (id, timestamps, state)

        Example:
            >>> job = EmailWorker.new({"to": "user@example.com", "subject": "Welcome"})
            >>> await oban.enqueue(job)

            For convenience, you can also use Worker.enqueue() directly:

            >>> await EmailWorker.enqueue({"to": "user@example.com", "subject": "Welcome"})
        """
        result = await self.enqueue_many(job)

        return result[0]

    async def enqueue_many(
        self, jobs_or_first: Iterable[Job] | Job, /, *rest: Job
    ) -> list[Job]:
        """Insert multiple jobs into the database in a single operation.

        This is more efficient than calling enqueue() multiple times as it uses a
        single database query to insert all jobs.

        Args:
            jobs_or_first: Either an iterable of jobs, or the first job when using
                variadic arguments
            *rest: Additional jobs when using variadic arguments

        Returns:
            The inserted jobs with database-assigned values (id, timestamps, state)

        Example:
            >>> job1 = EmailWorker.new({"to": "user1@example.com"})
            >>> job2 = EmailWorker.new({"to": "user2@example.com"})
            >>> job3 = EmailWorker.new({"to": "user3@example.com"})
            >>> await oban.enqueue_many(job1, job2, job3)
            >>> # Or with an iterable:
            >>> await oban.enqueue_many([job1, job2, job3])
            >>> await oban.enqueue_many(Worker.new({"id": id}) for id in range(10))
        """
        from .testing import _get_mode

        if isinstance(jobs_or_first, Job):
            jobs = [jobs_or_first, *rest]
        else:
            jobs = list(jobs_or_first)

        if _get_mode() == "inline":
            await self._execute_inline(jobs)

            return jobs

        result = await self._query.insert_jobs(jobs)

        queues = {job.queue for job in result if job.state == "available"}

        await self._notifier.notify("insert", [{"queue": queue} for queue in queues])

        return result

    async def _execute_inline(self, jobs):
        from .testing import process_job

        for job in jobs:
            result = process_job(job)

            if asyncio.iscoroutine(result):
                await result

    async def get_job(self, job_id: int) -> Job | None:
        """Fetch a job by its ID.

        Args:
            job_id: The ID of the job to fetch

        Returns:
            The Job if found, None otherwise

        Example:
            >>> job = await oban.get_job(123)
            >>> if job:
            ...     print(f"Job state: {job.state}")
        """
        return await self._query.get_job(job_id)

    async def retry_job(self, job: Job | int) -> None:
        """Retry a job by setting it as available for execution.

        Jobs currently `available` or `executing` are ignored. The job is scheduled
        for immediate execution, with `max_attempts` increased if already maxed out.

        Args:
            job: A Job instance or job ID to retry

        Example:
            Retry a job by ID:

            >>> await oban.retry_job(123)

            Retry a job instance:

            >>> await oban.retry_job(job)
        """
        job_id = job.id if isinstance(job, Job) else job

        if not job_id:
            raise ValueError("Cannot retry a job that has not been enqueued")

        await self.retry_many_jobs([job_id])

    async def retry_many_jobs(self, jobs: list[Job | int]) -> int:
        """Retry multiple jobs by setting them as available for execution.

        Jobs currently `available` or `executing` are ignored. Jobs are scheduled
        for immediate execution, with max_attempts increased if already maxed out.

        Args:
            jobs: List of Job instances or job IDs to retry

        Returns:
            The number of jobs updated

        Example:
            Retry multiple jobs by ID:

            >>> count = await oban.retry_many_jobs([123, 456, 789])

            Retry multiple job instances:

            >>> count = await oban.retry_many_jobs([job_1, job_2, job_3])
        """
        job_ids = [self._extract_id(job) for job in jobs]

        return await self._query.retry_many_jobs(job_ids)

    async def delete_job(self, job: Job | int) -> None:
        """Delete a job from the database.

        Jobs in the `executing` state cannot be deleted and are ignored.

        Args:
            job: A Job instance or job ID to delete

        Example:
            Delete a job by ID:

            >>> await oban.delete_job(123)

            Delete a job instance:

            >>> await oban.delete_job(job)
        """
        job_id = job.id if isinstance(job, Job) else job

        if not job_id:
            raise ValueError("Cannot delete a job that has not been enqueued")

        await self.delete_many_jobs([job_id])

    async def delete_many_jobs(self, jobs: list[Job | int]) -> int:
        """Delete multiple jobs from the database.

        Jobs in the `executing` state cannot be deleted and are ignored.

        Args:
            jobs: List of Job instances or job IDs to delete

        Returns:
            The number of jobs deleted

        Example:
            Delete multiple jobs by ID:

            >>> count = await oban.delete_many_jobs([123, 456, 789])

            Delete multiple job instances:

            >>> count = await oban.delete_many_jobs([job_1, job_2, job_3])
        """
        job_ids = [self._extract_id(job) for job in jobs]

        return await self._query.delete_many_jobs(job_ids)

    async def cancel_job(self, job: Job | int) -> None:
        """Cancel a job to prevent it from running.

        Jobs are marked as `cancelled`. Only jobs with the statuses `executing`,
        `available`, `scheduled`, or `retryable` can be cancelled.

        For `executing` jobs, the database state is updated immediately, but the
        running task is not forcefully terminated. Workers should check for cancellation
        at safe points and stop gracefully by calling `job.cancelled()`:

        >>> async def process(self, job):
        ...     for item in dataset:
        ...         if job.cancelled():
        ...             return Cancel("Job was cancelled")
        ...         await process_item(item)

        Args:
            job: A Job instance or job ID to cancel

        Example:
            Cancel a job by ID:

            >>> await oban.cancel_job(123)

            Cancel a job instance:

            >>> await oban.cancel_job(job)
        """
        job_id = job.id if isinstance(job, Job) else job

        if not job_id:
            raise ValueError("Cannot cancel a job that has not been enqueued")

        await self.cancel_many_jobs([job_id])

    async def cancel_many_jobs(self, jobs: list[Job | int]) -> int:
        """Cancel multiple jobs to prevent them from running.

        Jobs are marked as `cancelled`. Only jobs with the statuses `executing`,
        `available`, `scheduled`, or `retryable` can be cancelled.

        For `executing` jobs, the database state is updated immediately, but
        running tasks are not forcefully terminated. Workers should check for cancellation
        at safe points and stop gracefully by calling `job.cancelled()`:

        >>> async def process(self, job):
        ...     for item in dataset:
        ...         if job.cancelled():
        ...             return Cancel("Job was cancelled")
        ...         await process_item(item)

        Args:
            jobs: List of Job instances or job IDs to cancel

        Returns:
            The number of jobs cancelled

        Example:
            Cancel multiple jobs by ID:

            >>> count = await oban.cancel_many_jobs([123, 456, 789])

            Cancel multiple job instances:

            >>> count = await oban.cancel_many_jobs([job_1, job_2, job_3])
        """
        job_ids = [self._extract_id(job) for job in jobs]

        count, executing_ids = await self._query.cancel_many_jobs(job_ids)

        if executing_ids:
            payloads = [{"action": "pkill", "job_id": id} for id in executing_ids]

            await self._notifier.notify("signal", payloads)

        return count

    async def update_job(
        self, job: Job | int, changes: dict[str, Any] | Callable[[Job], dict[str, Any]]
    ) -> Job:
        """Update a job with the given changes.

        This function accepts either a job instance or id, along with either a dict of
        changes or a callable that receives the job and returns a dict of changes.

        The update operation is wrapped in a transaction with a locking clause to
        prevent concurrent modifications.

        Fields and Validations:
            All changes are validated using the same validations as `Job.new()`. Only the
            following subset of fields can be updated:

            - args
            - max_attempts
            - meta
            - priority
            - queue
            - scheduled_at
            - tags
            - worker

        Warning:
            Use caution when updating jobs that are currently executing. Modifying fields
            like args, queue, or worker while a job is running may lead to unexpected
            behavior or inconsistent state. Consider whether the job should be cancelled
            first, or if the update should be deferred until after execution completes.

        Args:
            job: A Job instance or job ID to update
            changes: Either a dict of field changes, or a callable that takes the job
                     and returns a dict of changes

        Returns:
            The updated job with all current field values

        Example:
            Update a job with a dict of changes:

            >>> await oban.update_job(job, {"tags": ["urgent"], "priority": 0})

            Update a job by id:

            >>> await oban.update_job(123, {"tags": ["processed"], "meta": {"batch_id": 456}})

            Update a job using a callable:

            >>> await oban.update_job(job, lambda j: {"tags": ["retry"] + j.tags})

            >>> from datetime import datetime
            >>> await oban.update_job(job, lambda j: {
            ...     "meta": {**j.meta, "processed_at": datetime.now()}
            ... })

            Use schedule_in for convenient scheduling:

            >>> await oban.update_job(job, {"schedule_in": 300})  # 5 minutes from now
        """
        result = await self.update_many_jobs([job], changes)

        return result[0]

    async def update_many_jobs(
        self,
        jobs: list[Job | int],
        changes: dict[str, Any] | Callable[[Job], dict[str, Any]],
    ) -> list[Job]:
        """Update multiple jobs with the given changes.

        This function accepts a list of job instances or ids, along with either a dict of
        changes or a callable that receives each job and returns a dict of changes.

        The update operation is wrapped in a transaction with a locking clause to
        prevent concurrent modifications.

        Fields and Validations:
            All changes are validated using the same validations as Job.new(). Only the
            following subset of fields can be updated:

            - args
            - max_attempts
            - meta
            - priority
            - queue
            - scheduled_at
            - tags
            - worker

        Warning:
            Use caution when updating jobs that are currently executing. Modifying fields
            like args, queue, or worker while a job is running may lead to unexpected
            behavior or inconsistent state. Consider whether the job should be cancelled
            first, or if the update should be deferred until after execution completes.

        Args:
            jobs: List of Job instances or job IDs to update
            changes: Either a dict of field changes, or a callable that takes a job
                     and returns a dict of changes

        Returns:
            The updated jobs with all current field values

        Example:
            Update multiple jobs with a dict of changes:

            >>> await oban.update_many_jobs([job1, job2], {"priority": 0})

            Update multiple jobs by ID:

            >>> await oban.update_many_jobs([123, 456], {"tags": ["processed"]})

            Update multiple jobs using a callable:

            >>> await oban.update_many_jobs(
            ...     [job1, job2],
            ...     lambda job: {"tags": ["retry"] + job.tags}
            ... )
        """
        instances = []

        for job in jobs:
            if isinstance(job, Job):
                instances.append(job)
            else:
                fetched = await self._query.get_job(job)

                if fetched is None:
                    raise ValueError(f"Job with id {job} not found")

                instances.append(fetched)

        # This isn't optimized for small updates, as we're re-sending values that haven't changed
        # and we're also not combining fetching and updating in a single transaction. This works
        # well enough for an infrequently used method.
        updated = [
            job.update(changes.copy() if isinstance(changes, dict) else changes(job))
            for job in instances
        ]

        return await self._query.update_many_jobs(updated)

    async def pause_queue(self, queue: str, *, node: str | None = None) -> None:
        """Pause a queue, preventing it from executing new jobs.

        All running jobs will remain running until they are finished.

        Args:
            queue: The name of the queue to pause
            node: Specific node name to pause. If not provided, pauses across all nodes.

        Example:
            Pause the default queue across all nodes:

            >>> await oban.pause_queue("default")

            Pause the default queue only on a particular node:

            >>> await oban.pause_queue("default", node="worker.1")
        """
        if not node or node == self._node:
            producer = self._producers.get(queue)

            if producer:
                await producer.pause()

        if node != self._node:
            ident = self._scope_signal(node)

            await self._notifier.notify(
                "signal", {"action": "pause", "queue": queue, "ident": ident}
            )

    async def resume_queue(self, queue: str, *, node: str | None = None) -> None:
        """Resume a paused queue, allowing it to execute jobs again.

        Args:
            queue: The name of the queue to resume
            node: Specific node name to resume. If not provided, resumes across all nodes.

        Example:
            Resume the default queue across all nodes:

            >>> await oban.resume_queue("default")

            Resume the default queue only on a particular node:

            >>> await oban.resume_queue("default", node="worker.1")
        """
        if not node or node == self._node:
            producer = self._producers.get(queue)

            if producer:
                await producer.resume()

        if node != self._node:
            ident = self._scope_signal(node)

            await self._notifier.notify(
                "signal", {"action": "resume", "queue": queue, "ident": ident}
            )

    async def pause_all_queues(self, *, node: str | None = None) -> None:
        """Pause all queues, preventing them from executing new jobs.

        All running jobs will remain running until they are finished.

        Args:
            node: Specific node name to pause. If not provided, pauses across all nodes.

        Example:
            Pause all queues across all nodes:

            >>> await oban.pause_all_queues()

            Pause all queues only on a particular node:

            >>> await oban.pause_all_queues(node="worker.1")
        """
        if not node or node == self._node:
            for producer in self._producers.values():
                await producer.pause()

        if node != self._node:
            ident = self._scope_signal(node)

            await self._notifier.notify(
                "signal", {"action": "pause", "queue": "*", "ident": ident}
            )

    async def resume_all_queues(self, *, node: str | None = None) -> None:
        """Resume all paused queues, allowing them to execute jobs again.

        Args:
            node: Specific node name to resume. If not provided, resumes across all nodes.

        Example:
            Resume all queues across all nodes:

            >>> await oban.resume_all_queues()

            Resume all queues only on a particular node:

            >>> await oban.resume_all_queues(node="worker.1")
        """
        if not node or node == self._node:
            for producer in self._producers.values():
                await producer.resume()

        if node != self._node:
            ident = self._scope_signal(node)

            await self._notifier.notify(
                "signal", {"action": "resume", "queue": "*", "ident": ident}
            )

    def check_queue(self, queue: str) -> QueueInfo | None:
        """Check the current state of a queue.

        This allows you to introspect on a queue's health by retrieving key attributes
        of the producer's state, such as the current limit, running job IDs, and when
        the producer was started.

        Args:
            queue: The name of the queue to check

        Returns:
            A QueueInfo instance with the producer's state, or None if the queue
            isn't running on this node.

        Example:
            Get details about the default queue:

            >>> state = oban.check_queue("default")
            ... print(f"Queue {state.queue} has {len(state.running)} jobs running")

            Attempt to check a queue that isn't running locally:

            >>> state = oban.check_queue("not_running")
            >>> print(state)  # None
        """
        producer = self._producers.get(queue)

        if producer:
            return producer.check()

    def check_all_queues(self) -> list[QueueInfo]:
        """Check the current state of all queues running on this node.

        Returns:
            A list of QueueInfo instances, one for each queue running locally.
            Returns an empty list if no queues are running on this node.

        Example:
            Get details about all local queues:

            >>> states = oban.check_all_queues()
            >>> for state in states:
            ...     print(f"{state.queue}: {len(state.running)} running, paused={state.paused}")
        """
        return [producer.check() for producer in self._producers.values()]

    async def start_queue(
        self, *, queue: str, limit: int, paused: bool = False, node: str | None = None
    ) -> None:
        """Start a new supervised queue.

        By default, this starts a new supervised queue across all nodes running Oban on the
        same database and prefix.

        Args:
            queue: The name of the queue to start
            limit: The concurrency limit for the queue
            paused: Whether the queue starts in a paused state (default: False)
            node: Specific node name to start the queue on. If not provided, starts across all nodes.

        Example:
            Start the priority queue with a concurrency limit of 10 across all nodes:

            >>> await oban.start_queue(queue="priority", limit=10)

            Start the media queue on a particular node:

            >>> await oban.start_queue(queue="media", limit=5, node="worker.1")

            Start the media queue in a paused state:

            >>> await oban.start_queue(queue="media", limit=5, paused=True)
        """
        if not node or node == self._node:
            await self._start_queue_local(queue=queue, limit=limit, paused=paused)
        else:
            ident = self._scope_signal(node)

            await self._notifier.notify(
                "signal",
                {
                    "action": "start",
                    "queue": queue,
                    "limit": limit,
                    "paused": paused,
                    "ident": ident,
                },
            )

    async def _start_queue_local(self, **params) -> None:
        queue = params["queue"]

        if queue in self._producers:
            return

        producer = Producer(
            dispatcher=self._dispatcher,
            query=self._query,
            name=self._name,
            node=self._node,
            notifier=self._notifier,
            **params,
        )

        self._producers[queue] = producer

        await producer.start()

    async def stop_queue(self, queue: str, *, node: str | None = None) -> None:
        """Stop a supervised queue.

        By default, this stops the queue across all connected nodes.

        Args:
            queue: The name of the queue to stop
            node: Specific node name to stop the queue on. If not provided, stops across all nodes.

        Example:
            Stop the priority queue across all nodes:

            >>> await oban.stop_queue("priority")

            Stop the media queue on a particular node:

            >>> await oban.stop_queue("media", node="worker.1")
        """
        if not node or node == self._node:
            await self._stop_queue_local(queue)
        else:
            ident = self._scope_signal(node)

            await self._notifier.notify(
                "signal", {"action": "stop", "queue": queue, "ident": ident}
            )

    async def _stop_queue_local(self, queue: str) -> None:
        producer = self._producers.pop(queue, None)

        if producer:
            await producer.stop()

    async def scale_queue(
        self, *, queue: str, node: str | None = None, **kwargs: Any
    ) -> None:
        """Scale the concurrency for a queue.

        By default, this scales the queue across all connected nodes.

        Args:
            queue: The name of the queue to scale
            node: Specific node name to scale the queue on. If not provided, scales across all nodes.
            **kwargs: Options passed through transparently, including:
                limit: The new concurrency limit

        Example:
            Scale a queue up, triggering immediate execution of queued jobs:

            >>> await oban.scale_queue(queue="default", limit=50)

            Scale the queue back down, allowing executing jobs to finish:

            >>> await oban.scale_queue(queue="default", limit=5)

            Scale the queue on a particular node:

            >>> await oban.scale_queue(queue="default", limit=10, node="worker.1")
        """
        if not node or node == self._node:
            await self._scale_queue_local(queue, **kwargs)

        if node != self._node:
            ident = self._scope_signal(node)

            await self._notifier.notify(
                "signal",
                {"action": "scale", "queue": queue, "ident": ident, **kwargs},
            )

    @staticmethod
    def _extract_id(job: Job | int) -> int:
        match job:
            case Job(id=int(id)):
                return id
            case int(id):
                return id
            case _:
                raise ValueError("Cannot retry a job that has not been enqueued")

    async def _scale_queue_local(self, queue: str, **kwargs: Any) -> None:
        producer = self._producers.get(queue)

        if producer:
            await producer.scale(**kwargs)

    def _scope_signal(self, node: str | None) -> str:
        if node is not None:
            return f"{self._name}.{node}"
        else:
            return "any"

    async def _on_signal(self, _channel: str, payload: dict) -> None:
        ident = payload.pop("ident")

        if ident != "any" and ident != f"{self._name}.{self._node}":
            return

        match payload.pop("action"):
            case "start":
                await self._start_queue_local(**payload)
            case "stop":
                await self._stop_queue_local(**payload)
            case "scale":
                await self._scale_queue_local(**payload)


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

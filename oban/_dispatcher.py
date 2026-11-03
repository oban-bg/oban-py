from __future__ import annotations

import asyncio
import logging
import os
from dataclasses import dataclass
from datetime import UTC, datetime
from multiprocessing import Process, Queue
from typing import TYPE_CHECKING
from uuid import uuid4

from ._backoff import jittery_clamped
from ._engine import run
from ._executor import AckAction
from ._serialization import deserialize_result, serialize_cancel, serialize_job

if TYPE_CHECKING:
    from ._config import Config
    from ._producer import Producer
    from .job import Job

logger = logging.getLogger(__name__)


@dataclass
class Engine:
    id: int
    generation: str
    process: Process
    push_queue: Queue


@dataclass
class Pending:
    future: asyncio.Future
    job: Job
    engine_id: int
    engine_generation: str


class Dispatcher:
    def __init__(
        self,
        conf: Config | None = None,
        num_processes: int | None = None,
        setup: str | None = None,
        teardown: str | None = None,
    ):
        self._conf = conf
        self._num_processes = num_processes or os.cpu_count() or 4
        self._setup = setup
        self._teardown = teardown

        self._collector_task = None
        self._engines = []
        self._monitor_interval = 1.0
        self._monitor_task = None
        self._next_engine = 0
        self._pending = {}
        self._pull_queue = Queue()
        self._running = False

    async def start(self) -> None:
        if self._running:
            return

        self._running = True
        self._engines = [self._spawn(idx) for idx in range(self._num_processes)]

        self._collector_task = asyncio.create_task(
            self._collect_results(), name="oban-dispatcher-collector"
        )

        self._monitor_task = asyncio.create_task(
            self._monitor_engines(), name="oban-dispatcher-monitor"
        )

    async def stop(self) -> None:
        if not self._running:
            return

        self._running = False

        if self._monitor_task:
            self._monitor_task.cancel()
            try:
                await self._monitor_task
            except asyncio.CancelledError:
                pass

        for engine in self._engines:
            engine.push_queue.put(None)

        if self._pending:
            pending_futures = [pending.future for pending in self._pending.values()]
            await asyncio.wait(pending_futures, timeout=30.0)

        for engine in self._engines:
            engine.process.join(timeout=5.0)

            if engine.process.is_alive():
                engine.process.terminate()
                engine.process.join()

            engine.push_queue.close()

        self._pull_queue.put(None)

        if self._collector_task:
            try:
                await asyncio.wait_for(self._collector_task, timeout=5.0)
            except TimeoutError:
                self._collector_task.cancel()

    def dispatch(self, _producer: Producer, job: Job) -> asyncio.Task[AckAction | None]:
        future = asyncio.get_running_loop().create_future()
        engine = self._engines[self._next_engine % len(self._engines)]
        self._next_engine += 1

        job._cancellation = asyncio.Event()
        self._pending[job.id] = Pending(
            future=future,
            job=job,
            engine_id=engine.id,
            engine_generation=engine.generation,
        )
        engine.push_queue.put(serialize_job(job))

        return asyncio.create_task(self._await_result(future))

    def cancel(self, _producer: Producer, job: Job) -> None:
        job_id = job.id

        if job_id is None:
            return

        pending = self._pending.get(job_id)

        if not pending:
            return

        engine = next(
            (
                engine
                for engine in self._engines
                if engine.id == pending.engine_id
                and engine.generation == pending.engine_generation
            ),
            None,
        )

        if engine:
            engine.push_queue.put(serialize_cancel(engine.generation, job_id))

    async def _await_result(self, future: asyncio.Future) -> AckAction | None:
        return await future

    async def _collect_results(self) -> None:
        loop = asyncio.get_running_loop()

        while True:
            try:
                result = await loop.run_in_executor(None, self._pull_queue.get)
            except Exception:
                if not self._running:
                    break
                raise

            if result is None:
                break

            generation, job_id, ack_action = deserialize_result(result)
            pending = self._pending.get(job_id)

            if pending and pending.engine_generation == generation:
                self._pending.pop(job_id)

                if not pending.future.done():
                    pending.future.set_result(ack_action)

    async def _monitor_engines(self) -> None:
        while self._running:
            await asyncio.sleep(self._monitor_interval)

            for idx, engine in enumerate(self._engines):
                if not engine.process.is_alive():
                    exit_code = engine.process.exitcode
                    engine.process.join()
                    logger.warning(
                        "Engine %d died (exit code: %s), restarting", idx, exit_code
                    )
                    self._fail_pending(engine, exit_code)
                    engine.push_queue.close()
                    engine.push_queue.cancel_join_thread()
                    self._engines[idx] = self._spawn(idx)

    def _fail_pending(self, engine: Engine, exit_code: int | None) -> None:
        failed = [
            pending
            for pending in self._pending.values()
            if pending.engine_generation == engine.generation
        ]

        for pending in failed:
            self._pending.pop(pending.job.id, None)

            if not pending.future.done():
                pending.future.set_result(
                    self._failure_action(pending.job, engine, exit_code)
                )

    def _failure_action(
        self, job: Job, engine: Engine, exit_code: int | None
    ) -> AckAction:
        error = {
            "attempt": job.attempt,
            "at": datetime.now(UTC).isoformat(),
            "error": (
                f"Engine {engine.generation}:{engine.id} exited unexpectedly with code {exit_code}"
            ),
        }

        if job.attempt >= job.max_attempts:
            return AckAction(job=job, state="discarded", error=error)

        return AckAction(
            job=job,
            state="retryable",
            error=error,
            schedule_in=jittery_clamped(job.attempt, job.max_attempts),
        )

    def _spawn(self, engine_id: int) -> Engine:
        generation = str(uuid4())
        push_queue = Queue()
        process = Process(
            target=run,
            args=(
                push_queue,
                self._pull_queue,
                self._conf,
                self._setup,
                self._teardown,
                generation,
            ),
            name=f"oban-{engine_id}",
        )
        process.start()

        return Engine(
            id=engine_id,
            generation=generation,
            process=process,
            push_queue=push_queue,
        )

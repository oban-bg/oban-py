from __future__ import annotations

import asyncio
import importlib
import signal
from collections.abc import Awaitable, Callable
from dataclasses import replace
from multiprocessing import Queue
from typing import TYPE_CHECKING

from ._executor import Executor
from ._serialization import deserialize_command, serialize_result

if TYPE_CHECKING:
    from ._config import Config

# psycopg connections aren't fork-safe, so a worker process can't share the pool the
# parent opened before spawning. Engines build their own client-mode Oban (a fresh pool
# plus query, no producers or supervision loops) so that workers reaching for
# Oban.get_instance() resolve to connections owned by this process.
ENGINE_POOL_MAX_SIZE = 5


def import_callable(path: str) -> Callable[[], Awaitable[None]]:
    module_name, func_name = path.rsplit(".", 1)
    module = importlib.import_module(module_name)

    return getattr(module, func_name)


async def _build_oban(conf: Config):
    pool_max_size = conf.pool_max_size

    if pool_max_size is None:
        pool_max_size = ENGINE_POOL_MAX_SIZE

    child_conf = replace(conf, queues={}, processes=None, pool_max_size=pool_max_size)

    pool = await child_conf.create_pool()
    await child_conf.create_oban(pool)

    return pool


def run(
    pull_queue: Queue,
    push_queue: Queue,
    conf: Config | None,
    setup: str | None,
    teardown: str | None,
    generation: str,
) -> None:
    signal.signal(signal.SIGINT, signal.SIG_IGN)

    asyncio.run(_loop(pull_queue, push_queue, conf, setup, teardown, generation))


async def _loop(
    pull_queue: Queue,
    push_queue: Queue,
    conf: Config | None,
    setup: str | None,
    teardown: str | None,
    generation: str,
) -> None:
    pool = await _build_oban(conf) if conf and conf.dsn else None

    if setup:
        await import_callable(setup)()

    pending = {}
    loop = asyncio.get_running_loop()

    try:
        while True:
            command_data = await loop.run_in_executor(None, pull_queue.get)

            if command_data is None:
                break

            match deserialize_command(command_data):
                case ("run", job):
                    job._cancellation = asyncio.Event()

                    task = asyncio.create_task(_execute(job, push_queue, generation))
                    pending[job.id] = (job, task)
                    task.add_done_callback(
                        lambda _task, job_id=job.id: pending.pop(job_id, None)
                    )

                case ("cancel", command_generation, job_id):
                    if command_generation == generation and job_id in pending:
                        job, _task = pending[job_id]
                        job._cancellation.set()

    finally:
        if pending:
            await asyncio.gather(
                *(task for _job, task in pending.values()),
                return_exceptions=True,
            )

        if teardown:
            await import_callable(teardown)()

        if pool:
            await pool.close()


async def _execute(job, push_queue: Queue, generation: str) -> None:
    executor = await Executor(job=job, safe=True).execute()

    push_queue.put(serialize_result(generation, job.id, executor.action))

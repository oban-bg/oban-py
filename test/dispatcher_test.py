import asyncio
import os

from oban import Cancel, Oban, Record, worker
from oban._config import Config
from oban._dispatcher import Dispatcher
from oban._recorded import decode_recorded
from oban.testing import reset_oban

from .helpers import with_backoff


@worker()
class ProcessWorker:
    async def process(self, job):
        fetched = await Oban.get_instance().get_job(job.id)

        return Record({"id": fetched.id, "pid": os.getpid()})


@worker()
class CancellableWorker:
    async def process(self, job):
        while not job.cancelled():
            await asyncio.sleep(0.01)

        return Cancel("Cancelled during execution")


@worker()
class CrashWorker:
    async def process(self, job):
        os._exit(1)


@worker()
class OkWorker:
    async def process(self, job):
        pass


async def assert_states(oban, jobs, state):
    async def check():
        fetched = [await oban.get_job(job.id) for job in jobs]

        return all(job.state == state for job in fetched)

    await with_backoff(check, timeout=3.0)


class TestMultiProcess:
    async def test_executing_jobs_in_worker_processes(self, test_dsn):
        conf = Config(
            dsn=test_dsn,
            leadership=False,
            processes=2,
            queues={"default": 10},
            stager={"interval": 0.01},
        )
        oban = await conf.create_oban()

        try:
            async with oban:
                jobs = await oban.enqueue_many(*[ProcessWorker.new() for _ in range(3)])

                await assert_states(oban, jobs, "completed")

                for job in jobs:
                    job = await oban.get_job(job.id)
                    result = decode_recorded(job.meta["return"])

                    assert result["id"] == job.id
                    assert result["pid"] != os.getpid()
        finally:
            await reset_oban(oban)
            await oban._query._pool.close()

    async def test_cancelling_a_job_in_a_worker_process(self, oban_instance):
        dispatcher = Dispatcher(num_processes=1)

        async with oban_instance(queues={"default": 1}, dispatcher=dispatcher) as oban:
            job = await oban.enqueue(CancellableWorker.new())

            await assert_states(oban, [job], "executing")
            await oban.cancel_job(job.id)

            # The queue's only slot frees up once the worker observes the cancellation
            following = await oban.enqueue(OkWorker.new())

            await assert_states(oban, [following], "completed")

    async def test_recovering_from_a_crashed_worker_process(self, oban_instance):
        dispatcher = Dispatcher(num_processes=2)
        dispatcher._monitor_interval = 0.01

        async with oban_instance(queues={"default": 2}, dispatcher=dispatcher) as oban:
            retryable = await oban.enqueue(CrashWorker.new())
            discarded = await oban.enqueue(CrashWorker.new(max_attempts=1))

            await assert_states(oban, [retryable], "retryable")
            await assert_states(oban, [discarded], "discarded")

            job = await oban.get_job(retryable.id)

            assert "exited unexpectedly" in job.errors[-1]["error"]

            following = await oban.enqueue(OkWorker.new())

            await assert_states(oban, [following], "completed")

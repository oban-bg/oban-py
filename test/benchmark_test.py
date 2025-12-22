import asyncio
import hashlib
import os
import pytest

from oban import Oban, job, worker
from oban._config import Config


TEST_DSN = os.getenv("DSN_BASE", "postgresql://postgres@localhost") + "/oban_py_test"


def cpu_intensive_work(iterations: int) -> str:
    data = b"benchmark"

    for _ in range(iterations):
        data = hashlib.sha256(data).digest()

    return data.hex()


class TestEnqueueBenchmark:
    @pytest.mark.benchmark
    def test_enqueue_10k_jobs(self, benchmark, oban_instance):
        """Benchmark inserting 10,000 jobs into the database."""

        @worker()
        class EmptyWorker:
            async def process(self, _):
                pass

        oban = oban_instance()
        jobs = [EmptyWorker.new() for _ in range(10_000)]

        async def run():
            await oban.enqueue_many(*jobs)

        benchmark(lambda: asyncio.run(run()))

    @pytest.mark.benchmark
    @pytest.mark.oban(queues={"default": 20})
    def test_insert_and_execute_1k_jobs(self, benchmark, oban_instance):
        """Benchmark inserting and executing 1,000 jobs."""
        total = 1_000
        event = None

        @job()
        def process(index):
            if index == total:
                event.set()

        async def run():
            nonlocal event
            event = asyncio.Event()

            async with oban_instance() as oban:
                jobs = [process.new(index=idx) for idx in range(1, total + 1)]
                await oban.enqueue_many(*jobs)
                await event.wait()

        benchmark(lambda: asyncio.run(run()))

    @pytest.mark.benchmark
    def test_cpu_intensive_100_jobs(self, benchmark):
        """Benchmark executing 100 CPU-intensive jobs."""
        total = 100
        event = None

        @job()
        def heavy_work(index):
            nonlocal event

            cpu_intensive_work(iterations=100_000)

            if index == total:
                event.set()

        async def run():
            nonlocal event
            event = asyncio.Event()
            pool = await Config(
                dsn=TEST_DSN, pool_min_size=2, pool_max_size=10
            ).create_pool()

            try:
                oban = Oban(pool=pool, queues={"default": 20}, leadership=False)

                async with oban:
                    jobs = [heavy_work.new(index=idx) for idx in range(1, total + 1)]
                    await oban.enqueue_many(*jobs)
                    await event.wait()
            finally:
                await pool.close()

        benchmark(lambda: asyncio.run(run()))

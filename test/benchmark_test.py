import asyncio
import pytest

from oban import worker


@worker()
class BenchmarkWorker:
    async def process(self, job):
        pass


class TestEnqueueBenchmark:
    @pytest.mark.benchmark
    def test_enqueue_1k_jobs(self, benchmark, oban_instance):
        """Benchmark inserting 10,000 jobs into the database."""
        oban = oban_instance()
        jobs = [BenchmarkWorker.new() for _ in range(10_000)]

        async def enqueue_jobs():
            await oban.enqueue_many(*jobs)

        benchmark(lambda: asyncio.run(enqueue_jobs()))

import asyncio
import pytest
import time
from datetime import datetime, timedelta, timezone

from oban import Cancel, Snooze, worker, _query


@worker()
class Worker:
    processed = set()

    def process(self, job):
        Worker.processed.add(job.args["ref"])

        match job.args:
            case {"act": "er"}:
                raise RuntimeError("this failed")
            case {"act": "ca"}:
                return Cancel("no reason")
            case {"act": "sn"}:
                return Snooze(1)
            case _:
                return None


async def with_backoff(check_fn, timeout=1.0, interval=0.01):
    start = time.time()
    last_error = None

    while time.time() - start < timeout:
        try:
            result = check_fn()
            if asyncio.iscoroutine(result):
                await result
            return
        except AssertionError as error:
            last_error = error
            await asyncio.sleep(interval)

    if last_error:
        raise last_error


class TestEnqueue:
    async def test_jobs_created_with_new_are_inserted_into_database(self, oban_instance):
        async with oban_instance() as oban:
            job = Worker.new({"ref": 1})

            assert job.id is None

            job = await oban.enqueue(job)

            assert job.id is not None
            assert job.args == {"ref": 1}
            assert job.worker == "test.oban_test.Worker"
            assert job.state == "available"


class TestEnqueueMany:
    async def test_multiple_jobs_are_inserted_into_database(self, oban_instance):
        async with oban_instance() as oban:
            jobs = [
                Worker.new({"ref": 1}),
                Worker.new({"ref": 2}),
                Worker.new({"ref": 3}),
            ]

            jobs = await oban.enqueue_many(jobs)

            assert len(jobs) == 3

            for job in jobs:
                assert job.id is not None
                assert job.inserted_at is not None
                assert job.scheduled_at is not None
                assert job.state == "available"


class TestIntegration:
    def teardown_method(self):
        Worker.processed.clear()

    def assert_processed(self, ref):
        assert ref in Worker.processed

    async def get_job(self, oban, job_id):
        async with oban.get_connection() as conn:
            return await _query.get_job(conn, job_id)

    async def assert_job_state(self, oban, job_id, expected_state):
        job = await self.get_job(oban, job_id)

        assert job is not None and job.state == expected_state

    @pytest.mark.oban(queues={"default": 2})
    async def test_inserting_and_executing_jobs(self, oban_instance):
        async with oban_instance() as oban:
            job_1 = await Worker.enqueue({"act": "ok", "ref": 1})
            job_2 = await Worker.enqueue({"act": "er", "ref": 2})
            job_3 = await Worker.enqueue({"act": "ca", "ref": 3})
            job_4 = await Worker.enqueue({"act": "sn", "ref": 4})
            job_5 = await Worker.enqueue({"act": "er", "ref": 5}, max_attempts=1)

            await with_backoff(lambda: self.assert_processed(1))
            await with_backoff(lambda: self.assert_processed(2))
            await with_backoff(lambda: self.assert_processed(3))
            await with_backoff(lambda: self.assert_processed(4))
            await with_backoff(lambda: self.assert_processed(5))

            await with_backoff(lambda: self.assert_job_state(oban, job_1.id, "completed"))
            await with_backoff(lambda: self.assert_job_state(oban, job_2.id, "retryable"))
            await with_backoff(lambda: self.assert_job_state(oban, job_3.id, "cancelled"))
            await with_backoff(lambda: self.assert_job_state(oban, job_4.id, "scheduled"))
            await with_backoff(lambda: self.assert_job_state(oban, job_5.id, "discarded"))

    @pytest.mark.oban(queues={"default": 2})
    async def test_executing_scheduled_jobs(self, oban_instance):
        async with oban_instance() as oban:
            utc_now = datetime.now(timezone.utc)

            past_time = utc_now - timedelta(seconds=30)
            next_time = utc_now + timedelta(seconds=30)

            job_1 = await Worker.enqueue({"ref": 1}, scheduled_at=past_time)
            job_2 = await Worker.enqueue({"ref": 2}, scheduled_at=next_time)

            await with_backoff(lambda: self.assert_processed(1))
            await with_backoff(lambda: self.assert_job_state(oban, job_1.id, "completed"))

            await self.assert_job_state(oban, job_2.id, "scheduled")

    @pytest.mark.oban(queues={"default": 2})
    async def test_errored_jobs_are_retryable_with_backoff(self, oban_instance):
        async with oban_instance() as oban:
            job = await Worker.enqueue({"act": "er", "ref": 1})
            now = datetime.now(timezone.utc)

            await with_backoff(lambda: self.assert_job_state(oban, job.id, "retryable"))

            job = await self.get_job(oban, job.id)

            assert job.scheduled_at > now

            assert len(job.errors) > 0
            assert job.errors[0]["at"] is not None
            assert job.errors[0]["attempt"] == 1
            assert job.errors[0]["error"] is not None

    @pytest.mark.oban(queues={"default": 2})
    async def test_errored_jobs_without_attempts_are_discarded(self, oban_instance):
        async with oban_instance() as oban:
            job = await Worker.enqueue({"act": "er", "ref": 1}, max_attempts=1)

            await with_backoff(lambda: self.assert_job_state(oban, job.id, "discarded"))

            job = await self.get_job(oban, job.id)

            assert job.discarded_at is not None

            assert len(job.errors) > 0
            assert job.errors[0]["at"] is not None
            assert job.errors[0]["attempt"] == 1
            assert job.errors[0]["error"] is not None

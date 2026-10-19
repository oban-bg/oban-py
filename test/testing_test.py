import asyncio
from datetime import UTC, datetime, timedelta

import pytest

from oban import Cancel, Snooze, job, worker
from oban.testing import (
    all_enqueued,
    assert_enqueued,
    drain_queue,
    mode,
    process_job,
    refute_enqueued,
)


def future(**kwargs):
    return datetime.now(UTC) + timedelta(**kwargs)


@worker()
class TestProcessJob:
    def test_process_job_with_worker_new(self):
        @worker()
        class SampleWorker:
            async def process(self, job):
                return job.args

        result = process_job(SampleWorker.new({"user_id": 123}))

        assert result["user_id"] == 123

    def test_process_job_with_function_new(self):
        @job()
        def echo(email):
            return email

        result = process_job(echo.new("test@example.com"))

        assert result == "test@example.com"

    def test_process_job_with_attempt_number(self):
        @worker()
        class RetryAwareWorker:
            async def process(self, job):
                if job.attempt < 3:
                    raise ValueError("boom")
                return {"success": True, "attempt": job.attempt}

        job = RetryAwareWorker.new()
        job.attempt = 3

        result = process_job(job)

        assert result["success"] is True
        assert result["attempt"] == 3

    def test_process_job_with_failing_worker(self):
        @worker()
        class FailingWorker:
            async def process(self, job):
                raise ValueError("boom")

        with pytest.raises(ValueError, match="boom"):
            process_job(FailingWorker.new())

    def test_process_job_sets_execution_defaults(self):
        @worker()
        class VerifyingWorker:
            async def process(self, job):
                assert job.id is not None
                assert job.attempt == 1
                assert job.attempted_at is not None
                assert job.scheduled_at is not None
                assert job.inserted_at is not None

        job = VerifyingWorker.new({"data": "test"})

        assert job.id is None
        assert job.attempt == 0
        assert job.attempted_at is None
        assert job.scheduled_at is None
        assert job.inserted_at is None

        process_job(job)

    def test_process_job_preserves_explicit_values(self):
        @worker()
        class VerifyingWorker:
            async def process(self, job):
                assert job.id == 999
                assert job.attempt == 5

        job = VerifyingWorker.new({}, attempt=5, id=999)

        process_job(job)

    def test_process_job_json_recodes_args_and_meta(self):
        @worker()
        class JsonTestWorker:
            async def process(self, job):
                assert isinstance(job.args["value"], list)
                assert job.args["value"] == [1, 2, 3]

        job = JsonTestWorker.new({"value": (1, 2, 3)})

        process_job(job)

    def test_process_job_rejects_non_json_serializable(self):
        @worker()
        class BadWorker:
            async def process(self, job):
                pass

        job = BadWorker.new({"function": lambda x: x})

        with pytest.raises(TypeError):
            process_job(job)


class TestInlineMode:
    async def test_inline_mode_executes_immediately(self, oban_instance):
        executed = asyncio.Event()

        @worker()
        class InlineWorker:
            async def process(self, job):
                executed.set()

        with mode("inline"):
            oban = oban_instance()
            job = await oban.enqueue(InlineWorker.new({"value": 42}))

            assert executed.is_set()
            assert job.args["value"] == 42
            assert job.worker.endswith("InlineWorker")


class TestAllEnqueued:
    async def test_all_enqueued_with_no_filters(self, oban_instance):
        @worker(queue="alpha")
        class Alpha:
            async def process(self, job):
                pass

        oban = oban_instance()
        await oban.enqueue_many(Alpha.new(), Alpha.new({"id": 1}))

        jobs = await all_enqueued()

        assert len(jobs) == 2

    async def test_all_enqueued_with_worker_filter(self, oban_instance):
        @worker(queue="alpha")
        class Alpha:
            async def process(self, job):
                pass

        @worker(queue="omega")
        class Omega:
            async def process(self, job):
                pass

        oban = oban_instance()
        await oban.enqueue_many(Alpha.new(), Omega.new(), Alpha.new({"id": 1}))

        jobs = await all_enqueued(worker=Alpha)

        assert len(jobs) == 2
        assert all(job.worker.endswith("Alpha") for job in jobs)

    async def test_all_enqueued_with_args_filter(self, oban_instance):
        @worker()
        class Alpha:
            async def process(self, job):
                pass

        oban = oban_instance()
        await oban.enqueue_many(
            Alpha.new({"id": 1}),
            Alpha.new({"id": 1, "extra": "data"}),
            Alpha.new({"id": 2}),
        )

        jobs = await all_enqueued(worker=Alpha, args={"id": 1})

        assert len(jobs) == 2
        assert all(job.args["id"] == 1 for job in jobs)

    async def test_all_enqueued_returns_descending_order(self, oban_instance):
        @worker()
        class Alpha:
            async def process(self, job):
                pass

        oban = oban_instance()

        await oban.enqueue_many(
            Alpha.new({"id": 1}), Alpha.new({"id": 2}), Alpha.new({"id": 3})
        )

        jobs = await all_enqueued(worker=Alpha)

        assert len(jobs) == 3
        assert jobs[0].args["id"] == 3
        assert jobs[1].args["id"] == 2
        assert jobs[2].args["id"] == 1


class TestAssertEnqueued:
    async def test_assert_enqueued_with_worker(self, oban_instance):
        @worker(queue="alpha")
        class Alpha:
            async def process(self, job):
                pass

        @worker(queue="omega", max_attempts=5)
        class Omega:
            async def process(self, job):
                pass

        oban = oban_instance()
        await oban.enqueue_many(
            Alpha.new(),
            Alpha.new({"id": 1, "xd": 1}),
            Omega.new({"id": 1, "xd": 2}),
        )

        await assert_enqueued(worker=Alpha)
        await assert_enqueued(worker=Alpha, queue="alpha")
        await assert_enqueued(worker=Omega)
        await assert_enqueued(worker=Omega, args={})
        await assert_enqueued(worker=Omega, args={"id": 1})
        await assert_enqueued(worker=Omega, args={"id": 1, "xd": 2})
        await assert_enqueued(worker=Omega, oban="Oban")

    async def test_assert_enqueued_with_scheduled_in(self, oban_instance):
        @worker()
        class Delayed:
            async def process(self, job):
                pass

        oban = oban_instance()
        await oban.enqueue(Delayed.new(scheduled_in=3600))

        await assert_enqueued(worker=Delayed, scheduled_in=3600)
        await assert_enqueued(worker=Delayed, scheduled_in=timedelta(hours=1))
        await refute_enqueued(worker=Delayed, scheduled_in=60)

    async def test_assert_enqueued_with_suspended_job(self, oban_instance):
        @worker()
        class Suspended:
            async def process(self, job):
                pass

        oban = oban_instance()
        await oban.enqueue(Suspended.new(state="suspended"))

        await assert_enqueued(worker=Suspended)

    async def test_assert_enqueued_raises_on_no_match(self, oban_instance):
        @worker(queue="alpha")
        class Alpha:
            async def process(self, job):
                pass

        oban = oban_instance()
        await oban.enqueue_many(Alpha.new({"id": 1}))

        with pytest.raises(AssertionError) as exc_info:
            await assert_enqueued(worker=Alpha, args={"id": 999})

        message = str(exc_info.value)

        assert "Expected a job matching" in message
        assert "worker" in message
        assert "Job(id=1, worker=Alpha" in message

    async def test_assert_enqueued_with_timeout(self, oban_instance):
        @worker()
        class Alpha:
            async def process(self, job):
                pass

        oban = oban_instance()

        async def enqueue_after_delay():
            await asyncio.sleep(0.02)
            await oban.enqueue(Alpha.new({"id": 1}))

        asyncio.create_task(enqueue_after_delay())

        await assert_enqueued(worker=Alpha, timeout=0.05)

    async def test_assert_enqueued_timeout_expires(self, oban_instance):
        @worker()
        class Alpha:
            async def process(self, job):
                pass

        oban_instance()

        with pytest.raises(AssertionError, match="within 0.01s"):
            await assert_enqueued(worker=Alpha, timeout=0.01)


class TestRefuteEnqueued:
    async def test_refute_enqueued_passes_when_no_match(self, oban_instance):
        @worker(queue="alpha")
        class Alpha:
            async def process(self, job):
                pass

        oban = oban_instance()
        await oban.enqueue(Alpha.new({"id": 1}))

        await refute_enqueued(worker=Alpha, args={"id": 999})
        await refute_enqueued(queue="omega")

    async def test_refute_enqueued_raises_when_match_found(self, oban_instance):
        @worker(queue="alpha")
        class Alpha:
            async def process(self, job):
                pass

        oban = oban_instance()
        await oban.enqueue(Alpha.new({"id": 1}))

        with pytest.raises(AssertionError) as exc_info:
            await refute_enqueued(worker=Alpha)

        message = str(exc_info.value)

        assert "Expected no jobs matching" in message
        assert "worker" in message
        assert "Job(id=1, worker=Alpha" in message

    async def test_refute_enqueued_with_timeout_passes(self, oban_instance):
        @worker()
        class Alpha:
            async def process(self, job):
                pass

        oban_instance()

        await refute_enqueued(worker=Alpha, timeout=0.05)

    async def test_refute_enqueued_with_timeout_fails(self, oban_instance):
        @worker()
        class Alpha:
            async def process(self, job):
                pass

        oban = oban_instance()

        async def enqueue_after_delay():
            await asyncio.sleep(0.02)
            await oban.enqueue(Alpha.new({"id": 1}))

        asyncio.create_task(enqueue_after_delay())

        with pytest.raises(AssertionError, match="within 0.1s"):
            await refute_enqueued(worker=Alpha, timeout=0.1)


class TestDrainQueue:
    async def test_draining_available_and_scheduled_jobs(self, oban_instance):
        @worker(queue="default")
        class Simple:
            async def process(self, job):
                pass

        await oban_instance().enqueue_many(
            Simple.new(),
            Simple.new(),
            Simple.new(scheduled_at=future(hours=1)),
        )

        result = await drain_queue(queue="default")

        assert result["completed"] == 3

    async def test_draining_without_scheduled_jobs(self, oban_instance):
        @worker(queue="default")
        class Scheduled:
            async def process(self, job):
                pass

        oban_instance()

        await Scheduled.enqueue(scheduled_at=future(hours=1))

        result = await drain_queue(queue="default", with_scheduled=False)

        assert result["completed"] == 0

    async def test_draining_with_safety_enabled(self, oban_instance):
        @worker(queue="default", max_attempts=2)
        class FailingWorker:
            async def process(self, job):
                raise ValueError("boom")

        oban_instance()

        await FailingWorker.enqueue()

        result = await drain_queue(queue="default", with_safety=True)

        assert result["retryable"] == 1

    async def test_draining_without_safety_propagates_errors(self, oban_instance):
        @worker(queue="default")
        class FailingWorker:
            async def process(self, job):
                raise RuntimeError("test error")

        oban_instance()

        await FailingWorker.enqueue()

        with pytest.raises(RuntimeError, match="test error"):
            await drain_queue(queue="default", with_safety=False)

    async def test_drain_queue_with_recursion(self, oban_instance):
        @worker(queue="default")
        class RecursiveWorker:
            async def process(self, job):
                depth = job.args["depth"]

                if depth < 2:
                    await RecursiveWorker.enqueue({"depth": depth + 1})

        oban_instance()

        await RecursiveWorker.enqueue({"depth": 0})

        result = await drain_queue(queue="default", with_recursion=True)

        assert result["completed"] == 3

    async def test_draining_with_alternate_states(self, oban_instance):
        @worker(queue="default")
        class CancelWorker:
            async def process(self, job):
                return Cancel(reason="test cancel")

        @worker(queue="default")
        class SnoozeWorker:
            async def process(self, job):
                if job.meta.get("snoozed", 0) < 1:
                    return Snooze(seconds=60)

        oban = oban_instance()
        await oban.enqueue_many(SnoozeWorker.new(), CancelWorker.new())

        result = await drain_queue(queue="default", with_scheduled=False)

        assert result["cancelled"] == 1
        assert result["scheduled"] == 1
        assert result["completed"] == 0

    async def test_drain_queue_without_recursion(self, oban_instance):
        @worker(queue="default")
        class Simple:
            async def process(self, job):
                pass

        await oban_instance().enqueue_many(Simple.new(), Simple.new())

        result = await drain_queue(queue="default", with_recursion=False)

        assert result["completed"] == 1

        await assert_enqueued(queue="default")

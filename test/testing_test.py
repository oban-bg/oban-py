import asyncio
import pytest

from oban import job, worker
from oban.testing import all_enqueued, assert_enqueued, mode, process_job


@worker()
class TestProcessJob:
    def test_process_job_with_worker_new(self):
        @worker()
        class SampleWorker:
            def process(self, job):
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
            def process(self, job):
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
            def process(self, job):
                raise ValueError("boom")

        with pytest.raises(ValueError, match="boom"):
            process_job(FailingWorker.new())

    def test_process_job_sets_execution_defaults(self):
        @worker()
        class VerifyingWorker:
            def process(self, job):
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
            def process(self, job):
                assert job.id == 999
                assert job.attempt == 5

        job = VerifyingWorker.new({}, attempt=5, id=999)

        process_job(job)

    def test_process_job_json_recodes_args_and_meta(self):
        @worker()
        class JsonTestWorker:
            def process(self, job):
                assert isinstance(job.args["value"], list)
                assert job.args["value"] == [1, 2, 3]

        job = JsonTestWorker.new({"value": (1, 2, 3)})

        process_job(job)

    def test_process_job_rejects_non_json_serializable(self):
        @worker()
        class BadWorker:
            def process(self, job):
                pass

        job = BadWorker.new({"function": lambda x: x})

        with pytest.raises(TypeError):
            process_job(job)


class TestInlineMode:
    @pytest.mark.oban(queues={})
    async def test_inline_mode_executes_immediately(self, oban_instance):
        executed = asyncio.Event()

        @worker()
        class InlineWorker:
            def process(self, job):
                executed.set()

        with mode("inline"):
            oban = oban_instance()
            job = await oban.enqueue(InlineWorker.new({"value": 42}))

            assert executed.is_set()
            assert job.args["value"] == 42
            assert job.worker.endswith("InlineWorker")


class TestAllEnqueued:
    @pytest.mark.oban(queues={})
    async def test_all_enqueued_with_no_filters(self, oban_instance):
        @worker(queue="alpha")
        class Alpha:
            def process(self, job):
                pass

        oban = oban_instance()
        await oban.enqueue_many(Alpha.new(), Alpha.new({"id": 1}))

        jobs = await all_enqueued()

        assert len(jobs) == 2

    @pytest.mark.oban(queues={})
    async def test_all_enqueued_with_worker_filter(self, oban_instance):
        @worker(queue="alpha")
        class Alpha:
            def process(self, job):
                pass

        @worker(queue="omega")
        class Omega:
            def process(self, job):
                pass

        oban = oban_instance()
        await oban.enqueue_many(Alpha.new(), Omega.new(), Alpha.new({"id": 1}))

        jobs = await all_enqueued(worker=Alpha)

        assert len(jobs) == 2
        assert all(job.worker.endswith("Alpha") for job in jobs)

    @pytest.mark.oban(queues={})
    async def test_all_enqueued_with_args_filter(self, oban_instance):
        @worker()
        class Alpha:
            def process(self, job):
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

    @pytest.mark.oban(queues={})
    async def test_all_enqueued_returns_descending_order(self, oban_instance):
        @worker()
        class Alpha:
            def process(self, job):
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
    @pytest.mark.oban(queues={})
    async def test_assert_enqueued_with_worker(self, oban_instance):
        @worker(queue="alpha")
        class Alpha:
            def process(self, job):
                pass

        @worker(queue="omega", max_attempts=5)
        class Omega:
            def process(self, job):
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
        await assert_enqueued(worker=Omega, oban="oban")

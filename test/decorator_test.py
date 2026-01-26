import asyncio
import pytest

from oban import job, worker
from oban.testing import process_job


class TestWorkerDecorator:
    def test_creates_worker_with_new_and_enqueue_methods(self):
        @worker()
        class TestWorker:
            async def process(self, job):
                return job.args

        assert hasattr(TestWorker, "new")
        assert hasattr(TestWorker, "enqueue")

    def test_new_creates_job_with_worker_path(self):
        @worker(queue="test", priority=5)
        class TestWorker:
            async def process(self, job):
                return job.args

        job = TestWorker.new({"foo": "bar"})

        assert job.worker.endswith("TestWorker")
        assert job.args == {"foo": "bar"}
        assert job.queue == "test"
        assert job.priority == 5

    def test_overrides_apply_to_individual_jobs(self):
        @worker(queue="default", priority=1)
        class TestWorker:
            async def process(self, job):
                return job.args

        job = TestWorker.new({"foo": "bar"}, priority=9, queue="urgent")

        assert job.queue == "urgent"
        assert job.priority == 9

    def test_accepts_cron_expression(self):
        @worker(queue="cleanup", cron="0 0 * * *")
        class DailyWorker:
            async def process(self, job):
                pass

        job = DailyWorker.new({})

        assert job.queue == "cleanup"

    def test_raises_on_invalid_cron_expression(self):
        with pytest.raises(ValueError, match="incorrect number of fields"):

            @worker(cron="@unknown")
            class InvalidWorker:
                async def process(self, job):
                    pass


class TestJobDecorator:
    def test_preserves_function_metadata(self):
        @job()
        def my_function(a: int, b: str):
            """Test function docstring."""
            return f"{a}:{b}"

        assert my_function.__name__ == "my_function"
        assert my_function.__doc__ == "Test function docstring."

    def test_new_accepts_positional_args(self):
        @job(queue="test")
        def send_email(to: str, body: str):
            pass

        new_job = send_email.new("user@example.com", "Hello")

        assert new_job.args == {"to": "user@example.com", "body": "Hello"}
        assert new_job.queue == "test"
        assert new_job.worker.endswith("send_email")

    def test_new_accepts_keyword_args(self):
        @job(queue="test")
        def send_email(to: str, body: str):
            pass

        new_job = send_email.new(to="user@example.com", body="World")

        assert new_job.args == {"to": "user@example.com", "body": "World"}

    def test_new_validates_signature(self):
        @job()
        def send_email(to: str, subject: str):
            pass

        with pytest.raises(TypeError):
            send_email.new("user@example.com")

        with pytest.raises(TypeError):
            send_email.new("a", "b", "c")

    def test_accepts_cron_expression(self):
        @job(queue="reports", cron="0 9 * * MON")
        def weekly_report():
            pass

        job_instance = weekly_report.new()

        assert job_instance.queue == "reports"

    def test_raises_on_invalid_cron_expression(self):
        with pytest.raises(ValueError, match="incorrect number of fields"):

            @job(cron="* * *")
            def bad_task():
                pass

    def test_async_job_functions_are_awaited(self):
        executed = False

        @job()
        async def async_task():
            nonlocal executed
            executed = True
            return "ok"

        result = process_job(async_task.new())

        assert not asyncio.iscoroutine(result)
        assert executed
        assert result == "ok"

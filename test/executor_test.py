import asyncio
from datetime import UTC, datetime, timedelta

import pytest

from oban import job, telemetry, worker
from oban._executor import Executor
from oban.worker import JobTimeoutError


@worker()
class SuccessWorker:
    async def process(self, job):
        pass


@worker()
class FailureWorker:
    async def process(self, job):
        raise ValueError("Worker failed")


class TestExecutorTelemetry:
    async def test_emits_start_and_stop_events_for_successful_execution(self):
        calls = []

        def handler(name, metadata):
            calls.append((name, metadata))

        telemetry.attach("test-executor", ["oban.job.start", "oban.job.stop"], handler)

        job = SuccessWorker.new(
            scheduled_at=datetime.now(UTC) - timedelta(seconds=2),
            attempted_at=datetime.now(UTC),
        )

        await Executor(job, safe=True).execute()

        assert len(calls) == 2

        start_name, start_meta = calls[0]
        stop_name, stop_meta = calls[1]

        assert start_name == "oban.job.start"
        assert start_meta["job"] is job

        assert stop_name == "oban.job.stop"
        assert stop_meta["job"] is job
        assert stop_meta["state"] == "completed"
        assert stop_meta["duration"] > 0
        assert stop_meta["queue_time"] > 0

    async def test_emits_exception_events_for_failed_execution(self):
        calls = []

        def handler(name, metadata):
            calls.append((name, metadata))

        telemetry.attach("test-executor", ["oban.job.exception"], handler)

        job = FailureWorker.new()

        await Executor(job, safe=True).execute()

        exception_name, exception_meta = calls[0]

        assert exception_name == "oban.job.exception"
        assert exception_meta["job"] is job
        assert exception_meta["state"] == "retryable"
        assert exception_meta["error_type"] == "ValueError"
        assert exception_meta["error_message"] == "Worker failed"
        assert "traceback" in exception_meta
        assert "duration" in exception_meta

    async def test_returns_executor_with_error_details_for_failure(self):
        job = FailureWorker.new()

        executor = await Executor(job, safe=True).execute()

        assert isinstance(executor.worker, FailureWorker)
        assert executor.status == "retryable"
        assert isinstance(executor.result, ValueError)
        assert str(executor.result) == "Worker failed"

        error = executor.action.error

        assert error["attempt"] == 0
        assert "at" in error
        assert "Traceback (most recent call last)" in error["error"]
        assert "ValueError: Worker failed" in error["error"]

    async def test_unsafe_mode_still_emits_telemetry_before_reraise(self):
        calls = []

        def handler(name, metadata):
            calls.append(name)

        telemetry.attach("test-executor", ["oban.job.exception"], handler)

        job = FailureWorker.new()

        with pytest.raises(ValueError):
            await Executor(job, safe=False).execute()

        assert "oban.job.exception" in calls


class TestExecutorCurrentJob:
    async def test_getting_current_job_from_context(self):
        current_job = None

        @job()
        def echo(_arg):
            nonlocal current_job
            current_job = Executor.current_job()

        await Executor(echo.new(123)).execute()

        assert current_job


@worker()
class SlowWorker:
    async def process(self, job):
        await asyncio.sleep(1)

    def timeout(self, job):
        return 0.01


@worker()
class RaisingTimeoutWorker:
    async def process(self, job):
        raise TimeoutError("upstream timed out")

    def timeout(self, job):
        return 1


class TestExecutorTimeout:
    async def test_timing_out_slow_jobs(self):
        executor = await Executor(SlowWorker.new()).execute()

        assert executor.status == "retryable"
        assert isinstance(executor.result, JobTimeoutError)
        assert "timed out after 0.01s" in executor.action.error["error"]

    async def test_preserving_timeouts_raised_by_jobs(self):
        executor = await Executor(RaisingTimeoutWorker.new()).execute()

        assert type(executor.result) is TimeoutError
        assert str(executor.result) == "upstream timed out"

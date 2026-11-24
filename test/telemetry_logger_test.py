import json
import logging
from datetime import UTC, datetime, timedelta

import pytest

from oban import telemetry, worker
from oban.telemetry import logger
from oban.testing import process_job


@worker()
class SuccessWorker:
    async def process(self, job):
        pass


@worker()
class FailureWorker:
    async def process(self, job):
        raise ValueError("Worker failed")


class TestLoggerHandler:
    async def test_logs_start_and_stop_events_for_successful_job(self, caplog):
        caplog.set_level(logging.DEBUG, logger="oban")

        job = SuccessWorker.new(
            scheduled_at=datetime.now(UTC) - timedelta(seconds=2),
            attempted_at=datetime.now(UTC),
        )
        await process_job(job)

        records = [rec for rec in caplog.records if rec.name == "oban"]
        assert len(records) == 2

        start_data = json.loads(records[0].message)
        stop_data = json.loads(records[1].message)

        assert start_data["event"] == "oban.job.start"
        assert start_data["id"] == job.id
        assert start_data["worker"].endswith("SuccessWorker")
        assert start_data["queue"] == "default"
        assert start_data["attempt"] == 1

        assert stop_data["event"] == "oban.job.stop"
        assert stop_data["id"] == job.id
        assert stop_data["state"] == "completed"
        assert stop_data["duration"] > 0
        assert stop_data["queue_time"] > 0

    async def test_logs_start_and_exception_events_for_failed_job(self, caplog):
        caplog.set_level(logging.DEBUG, logger="oban")

        job = FailureWorker.new(
            scheduled_at=datetime.now(UTC) - timedelta(seconds=1),
            attempted_at=datetime.now(UTC),
        )

        with pytest.raises(ValueError):
            await process_job(job)

        records = [rec for rec in caplog.records if rec.name == "oban"]
        assert len(records) == 2

        error_data = json.loads(records[1].message)

        assert records[1].levelno == logging.INFO
        assert error_data["event"] == "oban.job.exception"
        assert error_data["id"] == job.id
        assert error_data["state"] == "retryable"
        assert error_data["error_type"] == "ValueError"
        assert error_data["error_message"] == "Worker failed"
        assert error_data["duration"] > 0
        assert error_data["queue_time"] > 0

    def test_logs_shutdown_events_with_orphaned_jobs(self, caplog):
        caplog.set_level(logging.DEBUG, logger="oban")

        meta = {"duration": 15_000_000_000, "queue": "default"}

        telemetry.execute("oban.queue.shutdown", {**meta, "orphaned": []})
        telemetry.execute("oban.queue.shutdown", {**meta, "orphaned": [1, 2]})

        [record] = [rec for rec in caplog.records if rec.name == "oban"]
        data = json.loads(record.message)

        assert record.levelno == logging.WARNING
        assert data["event"] == "oban.queue.shutdown"
        assert data["duration"] == 15_000
        assert data["orphaned"] == [1, 2]

    def test_logs_background_work_once_it_stops_and_its_failures_as_errors(
        self, caplog
    ):
        caplog.set_level(logging.DEBUG, logger="oban")

        telemetry.execute("oban.pruner.prune.start", {"system_time": 1})
        telemetry.execute(
            "oban.pruner.prune.stop",
            {"count": 5, "duration": 1_234_000, "system_time": 1},
        )
        telemetry.execute(
            "oban.stager.stage.exception",
            {
                "duration": 1_234_000,
                "error_message": "connection lost",
                "error_type": "OperationalError",
                "system_time": 1,
            },
        )

        records = [rec for rec in caplog.records if rec.name == "oban"]

        assert [(rec.levelno, json.loads(rec.message)) for rec in records] == [
            (
                logging.DEBUG,
                {"count": 5, "duration": 1.23, "event": "oban.pruner.prune.stop"},
            ),
            (
                logging.ERROR,
                {
                    "duration": 1.23,
                    "error_message": "connection lost",
                    "error_type": "OperationalError",
                    "event": "oban.stager.stage.exception",
                },
            ),
        ]

    def test_skips_events_below_the_logger_level(self, caplog):
        caplog.set_level(logging.INFO, logger="oban")

        # Formatting this event without its count or jobs would fail, and be logged as an error
        telemetry.execute("oban.jobs.retry.stop", {"duration": 1_000_000})

        assert caplog.records == []

    def test_attach_and_detach_are_deprecated_and_do_nothing(self, caplog):
        caplog.set_level(logging.DEBUG, logger="oban")

        with pytest.warns(DeprecationWarning, match="attach"):
            logger.attach(level=logging.DEBUG, events=["oban.job.start"])

        with pytest.warns(DeprecationWarning, match="detach"):
            logger.detach()

        telemetry.execute("oban.pruner.prune.stop", {"count": 1, "duration": 1})

        assert len([rec for rec in caplog.records if rec.name == "oban"]) == 1

    async def test_logs_job_operation_events(self, caplog, oban_instance):
        caplog.set_level(logging.DEBUG, logger="oban")

        async with oban_instance() as oban:
            job = await SuccessWorker.enqueue(state="completed")

            await oban.retry_many_jobs([job.id, 99999])

        [record] = [rec for rec in caplog.records if "oban.jobs.retry" in rec.message]
        data = json.loads(record.message)

        assert record.levelno == logging.DEBUG
        assert data["event"] == "oban.jobs.retry.stop"
        assert data["count"] == 1
        assert data["job_ids"] == [job.id]

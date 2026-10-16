import json
import logging
from datetime import UTC, datetime, timedelta

import pytest

from oban import worker
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

        logger.attach()

        job = SuccessWorker.new(
            scheduled_at=datetime.now(UTC) - timedelta(seconds=2),
            attempted_at=datetime.now(UTC),
        )
        await process_job(job)

        logger.detach()

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

        logger.attach()

        job = FailureWorker.new(
            scheduled_at=datetime.now(UTC) - timedelta(seconds=1),
            attempted_at=datetime.now(UTC),
        )

        with pytest.raises(ValueError):
            await process_job(job)

        logger.detach()

        records = [rec for rec in caplog.records if rec.name == "oban"]
        assert len(records) == 2

        error_data = json.loads(records[1].message)

        assert error_data["event"] == "oban.job.exception"
        assert error_data["id"] == job.id
        assert error_data["state"] == "retryable"
        assert error_data["error_type"] == "ValueError"
        assert error_data["error_message"] == "Worker failed"
        assert error_data["duration"] > 0
        assert error_data["queue_time"] > 0

import asyncio
from datetime import UTC, datetime, timedelta

import pytest

from oban import Job
from oban._stager import Stager


class TestStagerValidation:
    def test_valid_config_passes(self):
        Stager._validate(interval=1.0, limit=20_000)

    def test_interval_must_be_numeric(self):
        with pytest.raises(TypeError, match="interval must be a number"):
            Stager._validate(interval="not a number", limit=20_000)

    def test_interval_must_be_positive(self):
        with pytest.raises(ValueError, match="interval must be positive"):
            Stager._validate(interval=0, limit=20_000)

        with pytest.raises(ValueError, match="interval must be positive"):
            Stager._validate(interval=-1.0, limit=20_000)

    def test_limit_must_be_integer(self):
        with pytest.raises(TypeError, match="limit must be an integer"):
            Stager._validate(interval=1.0, limit=999.5)

        with pytest.raises(TypeError, match="limit must be an integer"):
            Stager._validate(interval=1.0, limit="10000")

    def test_limit_must_be_positive(self):
        with pytest.raises(ValueError, match="limit must be positive"):
            Stager._validate(interval=1.0, limit=0)

        with pytest.raises(ValueError, match="limit must be positive"):
            Stager._validate(interval=1.0, limit=-1)


class TestStagerStop:
    async def test_stop_lets_in_flight_staging_finish(self):
        finished = asyncio.Event()

        class SlowQuery:
            async def stage_jobs(self, limit, queues):
                await asyncio.sleep(0.02)
                finished.set()

                return (0, [])

        stager = Stager(query=SlowQuery(), notifier=None, producers={})
        stager._loop_task = asyncio.create_task(stager._loop())

        await asyncio.sleep(0)
        await stager.stop()

        assert finished.is_set()
        assert stager._loop_task.done()


class TestStagerFailure:
    async def test_notifies_all_producers_when_staging_fails(self):
        notified = asyncio.Event()

        class FailingQuery:
            async def stage_jobs(self, limit, queues):
                raise RuntimeError("staging failed")

        class Producer:
            def notify(self):
                notified.set()

        stager = Stager(
            query=FailingQuery(), notifier=None, producers={"default": Producer()}
        )
        stager._loop_task = asyncio.create_task(stager._loop())

        await asyncio.wait_for(notified.wait(), timeout=1.0)
        await stager.stop()


class TestStaging:
    @pytest.mark.oban(queues={})
    async def test_reports_queues_with_newly_staged_jobs(self, oban_instance):
        async with oban_instance() as oban:
            past = datetime.now(UTC) - timedelta(seconds=1)

            await oban.enqueue(Job(worker="Worker", queue="alpha", scheduled_at=past))

            (_, queues) = await oban._query.stage_jobs(10, ["alpha", "omega"])

            assert queues == ["alpha"]

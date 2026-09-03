import asyncio
import os
import socket
import pytest
from datetime import datetime, timedelta, timezone

from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

from .helpers import with_backoff
from oban import Cancel, Record, Snooze, worker
from oban._recorded import decode_recorded


async def assert_state(oban, job_id, expected_state):
    job = await oban.get_job(job_id)

    assert job is not None and job.state == expected_state


@worker()
class Worker:
    processed = set()

    async def process(self, job):
        Worker.processed.add(job.args["ref"])

        match job.args:
            case {"act": "er"}:
                raise RuntimeError("this failed")
            case {"act": "ca"}:
                return Cancel("no reason")
            case {"act": "sn"}:
                return Snooze(1)
            case {"act": "sl"}:
                await asyncio.sleep(job.args["sleep"])

                return None
            case {"act": "re"}:
                return Record(job.args["val"])
            case _:
                return None


class TestEnqueue:
    async def test_jobs_are_inserted_into_database(self, oban_instance):
        async with oban_instance() as oban:
            job = Worker.new({"ref": 1})

            assert job.id is None

            job = await oban.enqueue(job)

            assert job.id is not None
            assert job.args == {"ref": 1}
            assert job.worker == "test.oban_test.Worker"
            assert job.state == "available"

    async def test_enqueue_with_conn_commits_with_transaction(self, oban_instance):
        async with oban_instance() as oban:
            async with oban._connection() as conn:
                async with conn.transaction():
                    job = await oban.enqueue(Worker.new({"ref": 1}), conn=conn)

                    assert job.id is not None

            assert (await oban.get_job(job.id)) is not None

    async def test_enqueue_with_conn_rolls_back_with_transaction(self, oban_instance):
        async with oban_instance() as oban:
            job_id = None

            try:
                async with oban._connection() as conn:
                    async with conn.transaction():
                        job = await oban.enqueue(Worker.new({"ref": 1}), conn=conn)
                        job_id = job.id

                        raise RuntimeError("Force rollback")
            except RuntimeError:
                pass

            assert (await oban.get_job(job_id)) is None


class TestEnqueueMany:
    async def test_multiple_jobs_are_inserted_into_database(self, oban_instance):
        async with oban_instance() as oban:
            jobs = await oban.enqueue_many(
                Worker.new({"ref": 1}),
                Worker.new({"ref": 2}),
                Worker.new({"ref": 3}),
            )

            assert len(jobs) == 3

            for job in jobs:
                assert job.id is not None
                assert job.inserted_at is not None
                assert job.scheduled_at is not None
                assert job.state == "available"

    async def test_enqueue_many_with_conn_commits_with_transaction(self, oban_instance):
        async with oban_instance() as oban:
            async with oban._connection() as conn:
                async with conn.transaction():
                    jobs = await oban.enqueue_many(
                        Worker.new({"ref": 1}),
                        Worker.new({"ref": 2}),
                        conn=conn,
                    )

            for job in jobs:
                assert (await oban.get_job(job.id)) is not None


class TestEnqueueWithSQLAlchemy:
    async def test_enqueue_with_async_session(self, oban_instance, test_dsn):
        sa_dsn = test_dsn.replace("postgresql://", "postgresql+psycopg://")
        engine = create_async_engine(sa_dsn)
        factory = async_sessionmaker(engine, class_=AsyncSession)

        async with oban_instance() as oban:
            async with factory() as session:
                async with session.begin():
                    job = await oban.enqueue(Worker.new({"ref": 1}), conn=session)

            assert (await oban.get_job(job.id)) is not None

        await engine.dispose()

    async def test_enqueue_with_async_connection(self, oban_instance, test_dsn):
        sa_dsn = test_dsn.replace("postgresql://", "postgresql+psycopg://")
        engine = create_async_engine(sa_dsn)

        async with oban_instance() as oban:
            async with engine.connect() as conn:
                async with conn.begin():
                    job = await oban.enqueue(Worker.new({"ref": 1}), conn=conn)

            assert (await oban.get_job(job.id)) is not None

        await engine.dispose()


class TestIntegration:
    def teardown_method(self):
        Worker.processed.clear()

    def assert_processed(self, ref):
        assert ref in Worker.processed

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

            await with_backoff(lambda: assert_state(oban, job_1.id, "completed"))
            await with_backoff(lambda: assert_state(oban, job_2.id, "retryable"))
            await with_backoff(lambda: assert_state(oban, job_3.id, "cancelled"))
            await with_backoff(lambda: assert_state(oban, job_4.id, "scheduled"))
            await with_backoff(lambda: assert_state(oban, job_5.id, "discarded"))

    @pytest.mark.oban(queues={"alpha": 1, "gamma": 1})
    async def test_limiting_concurrent_execution(self, oban_instance):
        oban = oban_instance()

        await oban.start()

        try:
            job_1, job_2, job_3, job_4 = await oban.enqueue_many(
                Worker.new({"act": "sl", "ref": 1, "sleep": 0.05}, queue="alpha"),
                Worker.new({"act": "sl", "ref": 2, "sleep": 0.05}, queue="alpha"),
                Worker.new({"act": "sl", "ref": 3, "sleep": 0.05}, queue="gamma"),
                Worker.new({"act": "sl", "ref": 4, "sleep": 0.05}, queue="gamma"),
            )

            await with_backoff(lambda: self.assert_processed(1))
            await with_backoff(lambda: self.assert_processed(3))

            await assert_state(oban, job_2.id, "available")
            await assert_state(oban, job_4.id, "available")
        finally:
            await oban.stop()

    @pytest.mark.oban(queues={"default": 2})
    async def test_executing_scheduled_jobs(self, oban_instance):
        async with oban_instance() as oban:
            utc_now = datetime.now(timezone.utc)

            past_time = utc_now - timedelta(seconds=30)
            next_time = utc_now + timedelta(seconds=30)

            job_1 = await Worker.enqueue({"ref": 1}, scheduled_at=past_time)
            job_2 = await Worker.enqueue({"ref": 2}, scheduled_at=next_time)

            await with_backoff(lambda: self.assert_processed(1))
            await with_backoff(lambda: assert_state(oban, job_1.id, "completed"))

            await assert_state(oban, job_2.id, "scheduled")

    @pytest.mark.oban(queues={"default": 2})
    async def test_completed_jobs_have_completed_at_timestamp(self, oban_instance):
        async with oban_instance() as oban:
            job = await Worker.enqueue({"act": "ok", "ref": 1})

            await with_backoff(lambda: assert_state(oban, job.id, "completed"))

            job = await oban.get_job(job.id)

            assert job.completed_at is not None

    @pytest.mark.oban(queues={"default": 2})
    async def test_cancelled_jobs_have_completed_at_timestamp(self, oban_instance):
        async with oban_instance() as oban:
            job = await Worker.enqueue({"act": "ca", "ref": 1})

            await with_backoff(lambda: assert_state(oban, job.id, "cancelled"))

            job = await oban.get_job(job.id)

            assert job.cancelled_at is not None

    @pytest.mark.oban(queues={"default": 2})
    async def test_snoozed_jobs_have_future_scheduled_at_timestamp(self, oban_instance):
        async with oban_instance() as oban:
            job = await Worker.enqueue({"act": "sn", "ref": 1}, meta={"keep": True})

            await with_backoff(lambda: assert_state(oban, job.id, "scheduled"))

            job = await oban.get_job(job.id)

            assert job.scheduled_at > datetime.now(timezone.utc)
            assert job.meta["snoozed"] == 1
            assert job.meta["keep"]

    @pytest.mark.oban(queues={"default": 2})
    async def test_recorded_jobs_store_encoded_value_in_meta(self, oban_instance):
        async with oban_instance() as oban:
            job = await Worker.enqueue(
                {"act": "re", "ref": 1, "val": {"count": 42}}, meta={"keep": True}
            )

            await with_backoff(lambda: assert_state(oban, job.id, "completed"))

            job = await oban.get_job(job.id)

            assert job.meta["recorded"]
            assert job.meta["keep"]
            assert decode_recorded(job.meta["return"]) == {"count": 42}

    @pytest.mark.oban(queues={"default": 2})
    async def test_errored_jobs_are_retryable_with_backoff(self, oban_instance):
        async with oban_instance() as oban:
            job = await Worker.enqueue({"act": "er", "ref": 1})
            now = datetime.now(timezone.utc)

            await with_backoff(lambda: assert_state(oban, job.id, "retryable"))

            job = await oban.get_job(job.id)

            assert job.scheduled_at > now

            assert len(job.errors) > 0
            assert job.errors[0]["at"] is not None
            assert job.errors[0]["attempt"] == 1
            assert job.errors[0]["error"] is not None

    @pytest.mark.oban(queues={"default": 2})
    async def test_errored_jobs_without_attempts_are_discarded(self, oban_instance):
        async with oban_instance() as oban:
            job = await Worker.enqueue({"act": "er", "ref": 1}, max_attempts=1)

            await with_backoff(lambda: assert_state(oban, job.id, "discarded"))

            job = await oban.get_job(job.id)

            assert job.discarded_at is not None
            assert len(job.errors) > 0

    @pytest.mark.oban(queues={"default": 3}, stager={"interval": 10.0})
    async def test_jobs_fetched_immediately_after_completion(self, oban_instance):
        async with oban_instance() as oban:
            await oban.enqueue_many(
                Worker.new({"act": "ok", "ref": 1}),
                Worker.new({"act": "ok", "ref": 2}),
                Worker.new({"act": "ok", "ref": 3}),
            )

            await with_backoff(lambda: self.assert_processed(1))
            await with_backoff(lambda: self.assert_processed(2))
            await with_backoff(lambda: self.assert_processed(3))


class TestPauseAndResumeQueue:
    @pytest.mark.oban(queues={"default": 2})
    async def test_paused_queue_does_not_execute_new_jobs(self, oban_instance):
        executed = asyncio.Event()

        @worker()
        class Pausable:
            async def process(self, _job):
                executed.set()

        async with oban_instance() as oban:
            await oban.pause_queue("default")

            await Pausable.enqueue()

            await asyncio.sleep(0.05)
            assert not executed.is_set()

            await oban.resume_queue("default")

            await asyncio.sleep(0.05)
            assert executed.is_set()

    @pytest.mark.oban(queues={"alpha": 1, "gamma": 1})
    async def test_pause_queue_only_affects_specified_queue(self, oban_instance):
        async with oban_instance() as oban:
            await oban.pause_queue("alpha")

            # Required to prevent the pause broadcast from overwriting the resume
            await asyncio.sleep(0.1)

            assert oban.check_queue("alpha").paused
            assert not oban.check_queue("gamma").paused

            await oban.resume_queue("alpha")
            assert not oban.check_queue("alpha").paused

    @pytest.mark.oban(queues={"default": 2})
    async def test_pause_queue_with_node(self, oban_instance):
        oban_1 = oban_instance(node="node1")
        oban_2 = oban_instance(node="node2")

        await oban_1.start()
        await oban_2.start()

        try:
            await oban_1.pause_queue("default", node="node1")

            assert oban_1.check_queue("default").paused
            assert not oban_2.check_queue("default").paused

            await oban_1.resume_queue("default", node="node1")

            assert not oban_1.check_queue("default").paused
        finally:
            await oban_1.stop()
            await oban_2.stop()


class TestCheckQueue:
    @pytest.mark.oban(queues={"alpha": 1, "gamma": 2})
    async def test_checking_queue_state_at_runtime(self, oban_instance):
        async with oban_instance() as oban:
            alpha_state = oban.check_queue("alpha")

            assert alpha_state is not None
            assert alpha_state.limit == 1
            assert alpha_state.node is not None
            assert alpha_state.paused is False
            assert alpha_state.queue == "alpha"
            assert alpha_state.running == []
            assert isinstance(alpha_state.started_at, datetime)

            gamma_state = oban.check_queue("gamma")
            assert gamma_state.limit == 2
            assert gamma_state.queue == "gamma"
            assert gamma_state.running == []

    async def test_checking_state_for_inactive_queue(self, oban_instance):
        async with oban_instance() as oban:
            assert oban.check_queue("default") is None

    async def test_queue_with_dict_config_limit_and_paused(self, oban_instance):
        queues = {"default": {"limit": 10, "paused": True}}

        async with oban_instance(queues=queues) as oban:
            state = oban.check_queue("default")

            assert state.limit == 10
            assert state.paused is True


class TestCheckAllQueues:
    @pytest.mark.oban(queues={"alpha": 1, "gamma": 2, "delta": 3})
    async def test_checking_all_queues(self, oban_instance):
        async with oban_instance() as oban:
            states = oban.check_all_queues()

            assert len(states) == 3

            # Sort by queue name for consistent ordering
            states.sort(key=lambda state: state.queue)

            assert states[0].queue == "alpha"
            assert states[0].limit == 1
            assert states[0].paused is False

            assert states[1].queue == "delta"
            assert states[1].limit == 3

            assert states[2].queue == "gamma"
            assert states[2].limit == 2

    async def test_checking_all_queues_with_no_queues(self, oban_instance):
        async with oban_instance() as oban:
            states = oban.check_all_queues()

            assert states == []


class TestPauseAndResumeAllQueues:
    @pytest.mark.oban(queues={"alpha": 1, "gamma": 1, "delta": 1})
    async def test_pausing_and_resuming_all_queues(self, oban_instance):
        async with oban_instance() as oban:
            await oban.pause_all_queues()

            assert oban.check_queue("alpha").paused
            assert oban.check_queue("gamma").paused
            assert oban.check_queue("delta").paused

            # Sleep to prevent overwrite
            await asyncio.sleep(0.1)

            await oban.resume_all_queues()

            assert not oban.check_queue("alpha").paused
            assert not oban.check_queue("gamma").paused
            assert not oban.check_queue("delta").paused

    @pytest.mark.oban(queues={"alpha": 1, "gamma": 1})
    async def test_pausing_and_resuming_all_queues_with_node(self, oban_instance):
        oban_1 = oban_instance(node="node.1")
        oban_2 = oban_instance(node="node.2")

        await oban_1.start()
        await oban_2.start()

        try:
            await oban_1.pause_all_queues(node="node.1")

            assert oban_1.check_queue("alpha").paused
            assert oban_1.check_queue("gamma").paused

            assert not oban_2.check_queue("alpha").paused
            assert not oban_2.check_queue("gamma").paused

            await oban_2.pause_all_queues(node="node.2")
            await oban_1.resume_all_queues(node="node.1")

            assert not oban_1.check_queue("alpha").paused
            assert not oban_1.check_queue("gamma").paused

            assert oban_2.check_queue("alpha").paused
            assert oban_2.check_queue("gamma").paused
        finally:
            await oban_1.stop()
            await oban_2.stop()


class TestStartQueue:
    async def test_starting_queue_dynamically(self, oban_instance):
        async with oban_instance() as oban:
            await oban.start_queue(queue="priority", limit=5)

            state = oban.check_queue("priority")
            assert state is not None
            assert state.queue == "priority"
            assert state.limit == 5
            assert state.paused is False

    async def test_starting_queue_in_paused_state(self, oban_instance):
        async with oban_instance() as oban:
            await oban.start_queue(queue="media", limit=3, paused=True)

            state = oban.check_queue("media")
            assert state.paused is True

    async def test_starting_queue_with_node(self, oban_instance):
        oban_1 = oban_instance(node="node.1")
        oban_2 = oban_instance(node="node.2")

        await oban_1.start()
        await oban_2.start()

        try:
            await asyncio.sleep(0.1)

            await oban_1.start_queue(queue="priority", limit=10, node="node.2")

            await with_backoff(lambda: oban_1.check_queue("priority") is None)
            await with_backoff(lambda: oban_2.check_queue("priority") is not None)

            await oban_2.start_queue(queue="priority", limit=10, node="node.1")

            await with_backoff(lambda: oban_1.check_queue("priority") is not None)
        finally:
            await oban_1.stop()
            await oban_2.stop()

    async def test_starting_queue_across_all_nodes(self, oban_instance):
        oban_1 = oban_instance(node="node.1")
        oban_2 = oban_instance(node="node.2")

        await oban_1.start()
        await oban_2.start()

        try:
            await oban_1.start_queue(queue="priority", limit=10)

            await with_backoff(lambda: oban_1.check_queue("priority") is not None)
            await with_backoff(lambda: oban_2.check_queue("priority") is not None)
        finally:
            await oban_1.stop()
            await oban_2.stop()

    async def test_starting_queue_with_invalid_limit(self, oban_instance):
        async with oban_instance() as oban:
            with pytest.raises(ValueError, match="limit must be positive"):
                await oban.start_queue(queue="bad", limit=0)

    async def test_starting_queue_that_already_exists(self, oban_instance):
        async with oban_instance() as oban:
            await oban.start_queue(queue="priority", limit=5)

            state_1 = oban.check_queue("priority")

            # Starting again should be idempotent (no-op)
            await oban.start_queue(queue="priority", limit=10)

            state_2 = oban.check_queue("priority")

            # Limit should remain unchanged
            assert state_1.limit == state_2.limit == 5


class TestStopQueue:
    @pytest.mark.oban(queues={"alpha": 1})
    async def test_stopping_queue_with_node(self, oban_instance):
        oban_1 = oban_instance(node="node.1")
        oban_2 = oban_instance(node="node.2")

        await oban_1.start()
        await oban_2.start()

        try:
            assert oban_1.check_queue("alpha") is not None
            assert oban_2.check_queue("alpha") is not None

            await oban_1.stop_queue("alpha", node="node.1")

            await with_backoff(lambda: oban_1.check_queue("alpha") is None)

            assert oban_1.check_queue("alpha") is None
            assert oban_2.check_queue("alpha") is not None
        finally:
            await oban_1.stop()
            await oban_2.stop()

    @pytest.mark.oban(queues={"alpha": 1})
    async def test_stopping_queue_across_all_nodes(self, oban_instance):
        oban_1 = oban_instance(node="node.1")
        oban_2 = oban_instance(node="node.2")

        await oban_1.start()
        await oban_2.start()

        try:
            assert oban_1.check_queue("alpha") is not None
            assert oban_2.check_queue("alpha") is not None

            await oban_1.stop_queue("alpha")

            await with_backoff(lambda: oban_1.check_queue("alpha") is None)
            await with_backoff(lambda: oban_2.check_queue("alpha") is None)
        finally:
            await oban_1.stop()
            await oban_2.stop()


class TestRetryJob:
    @pytest.mark.oban(queues={"default": 1})
    async def test_retrying_a_job(self, oban_instance):
        async with oban_instance() as oban:
            job = await Worker.enqueue({"act": "er", "ref": 1}, max_attempts=1)

            await with_backoff(lambda: assert_state(oban, job.id, "discarded"))

            await oban.pause_queue("default")

            await oban.retry_job(job.id)

            await assert_state(oban, job.id, "available")

    async def test_retrying_a_nonexistent_job(self, oban_instance):
        async with oban_instance() as oban:
            await oban.retry_job(99999)


class TestRetryAllJobs:
    @pytest.mark.oban(queues={"default": 3})
    async def test_retrying_multiple_jobs(self, oban_instance):
        async with oban_instance() as oban:
            job_1 = await Worker.enqueue({"act": "er", "ref": 1}, max_attempts=1)
            job_2 = await Worker.enqueue({"act": "ca", "ref": 2}, max_attempts=1)
            job_3 = await Worker.enqueue({"act": "ok", "ref": 3}, max_attempts=1)

            await with_backoff(lambda: assert_state(oban, job_1.id, "discarded"))
            await with_backoff(lambda: assert_state(oban, job_2.id, "cancelled"))
            await with_backoff(lambda: assert_state(oban, job_3.id, "completed"))

            await oban.pause_queue("default")

            assert await oban.retry_many_jobs([job_1.id, job_2, job_3]) == 3

            await assert_state(oban, job_1.id, "available")
            await assert_state(oban, job_2.id, "available")
            await assert_state(oban, job_3.id, "available")


class TestDeleteJob:
    async def test_deleting_a_job_by_id(self, oban_instance):
        async with oban_instance() as oban:
            job = await Worker.enqueue({"ref": 1})

            await oban.delete_job(job.id)

            assert await oban.get_job(job.id) is None

    async def test_deleting_a_job_by_instance(self, oban_instance):
        async with oban_instance() as oban:
            job = await Worker.enqueue({"ref": 1})

            await oban.delete_job(job)

            assert await oban.get_job(job.id) is None

    async def test_deleting_nonexistent_job(self, oban_instance):
        async with oban_instance() as oban:
            await oban.delete_job(99999)


class TestDeleteAllJobs:
    @pytest.mark.oban()
    async def test_deleting_multiple_jobs(self, oban_instance):
        async with oban_instance() as oban:
            job_1 = await Worker.enqueue({"act": "ok", "ref": 1})
            job_2 = await Worker.enqueue({"act": "ok", "ref": 2})

            assert await oban.delete_many_jobs([job_1.id, job_2]) == 2

            assert await oban.get_job(job_1.id) is None
            assert await oban.get_job(job_2.id) is None


class TestCancelJob:
    async def test_cancelling_a_scheduled_job(self, oban_instance):
        async with oban_instance() as oban:
            job = await Worker.enqueue({"ref": 1}, schedule_in=60)

            await oban.cancel_job(job.id)

            job = await oban.get_job(job.id)

            assert job.state == "cancelled"
            assert job.cancelled_at is not None

    @pytest.mark.oban(queues={"default": 1})
    async def test_cancelling_executing_job_signals_cancellation(self, oban_instance):
        async with oban_instance() as oban:
            cancelled = asyncio.Event()

            @worker()
            class CancellableWorker:
                async def process(self, job):
                    for _ in range(1000):
                        if job.cancelled():
                            cancelled.set()

                            return Cancel("Cancelled during execution")

                        await asyncio.sleep(0.01)

            job = await CancellableWorker.enqueue()

            await with_backoff(lambda: assert_state(oban, job.id, "executing"))

            await oban.cancel_job(job.id)

            await with_backoff(lambda: cancelled.is_set())
            await with_backoff(lambda: assert_state(oban, job.id, "cancelled"))

    async def test_cancelling_completed_job_is_ignored(self, oban_instance):
        async with oban_instance() as oban:
            job = await Worker.enqueue({"act": "ok", "ref": 1}, state="completed")

            await oban.cancel_job(job.id)

            await assert_state(oban, job.id, "completed")

    async def test_cancelling_nonexistent_job(self, oban_instance):
        async with oban_instance() as oban:
            await oban.cancel_job(99999)


class TestCancelAllJobs:
    @pytest.mark.oban()
    async def test_cancelling_multiple_jobs(self, oban_instance):
        async with oban_instance() as oban:
            job_1 = await Worker.enqueue()
            job_2 = await Worker.enqueue()

            await oban.cancel_many_jobs([job_1.id, job_2]) == 2

            await assert_state(oban, job_1.id, "cancelled")
            await assert_state(oban, job_2.id, "cancelled")


class TestUpdateJob:
    async def test_updating_job(self, oban_instance):
        async with oban_instance() as oban:
            job = await Worker.enqueue({"ref": 1}, priority=5, tags=["original"])

            updated = await oban.update_job(
                job.id, {"priority": 0, "tags": ["updated"]}
            )

            assert updated.id == job.id
            assert updated.priority == 0
            assert updated.tags == ["updated"]
            assert updated.args == {"ref": 1}


class TestUpdateManyJob:
    async def test_updating_multiple_jobs(self, oban_instance):
        async with oban_instance() as oban:
            job_1 = await Worker.enqueue({"ref": 1}, priority=5, tags=["original"])
            job_2 = await Worker.enqueue({"ref": 2}, priority=5, tags=[])

            updated = await oban.update_many_jobs(
                [job_1, job_2.id],
                lambda job: {"tags": ["updated"] + job.tags, "priority": 0},
            )

            assert len(updated) == 2
            assert updated[0].priority == 0
            assert updated[0].tags == ["original", "updated"]
            assert updated[1].priority == 0
            assert updated[1].tags == ["updated"]

    async def test_updating_missing_job(self, oban_instance):
        async with oban_instance() as oban:
            with pytest.raises(ValueError, match="not found"):
                await oban.update_many_jobs([999999], {"priority": 0})


class TestScaleQueue:
    @pytest.mark.oban(queues={"alpha": 5})
    async def test_scaling_queue_with_node(self, oban_instance):
        oban_1 = oban_instance(node="node.1")
        oban_2 = oban_instance(node="node.2")

        await oban_1.start()
        await oban_2.start()

        try:
            await oban_1.scale_queue(queue="alpha", limit=10, node="node.1")

            await with_backoff(lambda: oban_1.check_queue("alpha").limit == 10)

            assert oban_1.check_queue("alpha").limit == 10
            assert oban_2.check_queue("alpha").limit == 5
        finally:
            await oban_1.stop()
            await oban_2.stop()

    @pytest.mark.oban(queues={"alpha": 5})
    async def test_scaling_queue_across_all_nodes(self, oban_instance):
        oban_1 = oban_instance(node="node.1")
        oban_2 = oban_instance(node="node.2")

        await oban_1.start()
        await oban_2.start()

        try:
            await oban_1.scale_queue(queue="alpha", limit=20)

            await with_backoff(lambda: oban_1.check_queue("alpha").limit == 20)
            await with_backoff(lambda: oban_2.check_queue("alpha").limit == 20)
        finally:
            await oban_1.stop()
            await oban_2.stop()


class TestNodeIdentity:
    async def test_default_node_combines_hostname_and_pid(self, oban_instance):
        oban = oban_instance()

        assert oban._node == f"{socket.gethostname()}.{os.getpid()}"

    async def test_explicit_node_is_used_verbatim(self, oban_instance):
        oban = oban_instance(node="web.1")

        assert oban._node == "web.1"

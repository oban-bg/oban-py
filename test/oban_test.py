import asyncio
import pytest
from datetime import datetime, timedelta, timezone

from .helpers import with_backoff
from oban import Cancel, Snooze, worker


async def get_job(oban, job_id):
    return await oban._query.get_job(job_id)


async def assert_state(oban, job_id, expected_state):
    job = await get_job(oban, job_id)

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
            case _:
                return None


class TestEnqueue:
    async def test_jobs_created_with_new_are_inserted_into_database(
        self, oban_instance
    ):
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

            job = await get_job(oban, job.id)

            assert job.completed_at is not None

    @pytest.mark.oban(queues={"default": 2})
    async def test_cancelled_jobs_have_completed_at_timestamp(self, oban_instance):
        async with oban_instance() as oban:
            job = await Worker.enqueue({"act": "ca", "ref": 1})

            await with_backoff(lambda: assert_state(oban, job.id, "cancelled"))

            job = await get_job(oban, job.id)

            assert job.cancelled_at is not None

    @pytest.mark.oban(queues={"default": 2})
    async def test_snoozed_jobs_have_future_scheduled_at_timestamp(self, oban_instance):
        async with oban_instance() as oban:
            job = await Worker.enqueue({"act": "sn", "ref": 1})
            now = datetime.now(timezone.utc)

            await with_backoff(lambda: assert_state(oban, job.id, "scheduled"))

            job = await get_job(oban, job.id)

            assert job.scheduled_at > now
            assert (job.scheduled_at - now).total_seconds() >= 1

    @pytest.mark.oban(queues={"default": 2})
    async def test_errored_jobs_are_retryable_with_backoff(self, oban_instance):
        async with oban_instance() as oban:
            job = await Worker.enqueue({"act": "er", "ref": 1})
            now = datetime.now(timezone.utc)

            await with_backoff(lambda: assert_state(oban, job.id, "retryable"))

            job = await get_job(oban, job.id)

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

            job = await get_job(oban, job.id)

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

            await oban.retry_job(job.id)

            await with_backoff(lambda: assert_state(oban, job.id, "available"))

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

            assert await oban.retry_all_jobs([job_1.id, job_2, job_3]) == 3

            await with_backoff(lambda: assert_state(oban, job_1.id, "available"))
            await with_backoff(lambda: assert_state(oban, job_2.id, "available"))
            await with_backoff(lambda: assert_state(oban, job_3.id, "available"))


class TestDeleteJob:
    async def test_deleting_a_job_by_id(self, oban_instance):
        async with oban_instance() as oban:
            job = await Worker.enqueue({"ref": 1})

            await oban.delete_job(job.id)

            assert await get_job(oban, job.id) is None

    async def test_deleting_a_job_by_instance(self, oban_instance):
        async with oban_instance() as oban:
            job = await Worker.enqueue({"ref": 1})

            await oban.delete_job(job)

            assert await get_job(oban, job.id) is None

    async def test_deleting_nonexistent_job(self, oban_instance):
        async with oban_instance() as oban:
            await oban.delete_job(99999)


class TestDeleteAllJobs:
    @pytest.mark.oban()
    async def test_deleting_multiple_jobs(self, oban_instance):
        async with oban_instance() as oban:
            job_1 = await Worker.enqueue({"act": "ok", "ref": 1})
            job_2 = await Worker.enqueue({"act": "ok", "ref": 2})

            assert await oban.delete_all_jobs([job_1.id, job_2]) == 2

            assert await get_job(oban, job_1.id) is None
            assert await get_job(oban, job_2.id) is None


class TestCancelJob:
    async def test_cancelling_a_scheduled_job(self, oban_instance):
        async with oban_instance() as oban:
            job = await Worker.enqueue({"ref": 1}, schedule_in=60)

            await oban.cancel_job(job.id)

            job = await get_job(oban, job.id)

            assert job.state == "cancelled"
            assert job.cancelled_at is not None

    async def test_cancelling_completed_job_is_ignored(self, oban_instance):
        async with oban_instance() as oban:
            job = await Worker.enqueue({"act": "ok", "ref": 1})

            # Manually mark it as completed
            await oban._query.complete_job(job)
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

            await oban.cancel_all_jobs([job_1.id, job_2]) == 2

            await assert_state(oban, job_1.id, "cancelled")
            await assert_state(oban, job_2.id, "cancelled")


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

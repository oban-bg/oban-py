import asyncio

import pytest

from oban import Cancel, Job, telemetry, worker
from oban._extensions import _extensions, put_ext
from oban._producer import LocalDispatcher, Producer, _get_jobs

from .helpers import with_backoff


async def assert_state(oban, job_id, expected_state):
    job = await oban.get_job(job_id)

    assert job is not None and job.state == expected_state


async def all_producers(conn):
    result = await conn.execute("""
        SELECT uuid, name, node, queue, meta
        FROM oban_producers
        ORDER BY queue
    """)

    return await result.fetchall()


class TestProducerValidation:
    def validate(self, **opts):
        base = {"name": "Oban", "node": "worker", "query": None}

        Producer(**base, **opts)

    def test_limit_must_be_positive(self):
        with pytest.raises(ValueError, match="limit must be positive"):
            self.validate(queue="default", limit=0)

        with pytest.raises(ValueError, match="limit must be positive"):
            self.validate(queue="default", limit=-1)

    def test_queue_must_be_string(self):
        with pytest.raises(TypeError, match="queue must be a string"):
            self.validate(queue=123, limit=10)

        with pytest.raises(TypeError, match="queue must be a string"):
            self.validate(queue=None, limit=10)

    def test_queue_must_not_be_blank(self):
        with pytest.raises(ValueError, match="queue must not be blank"):
            self.validate(queue="", limit=10)

        with pytest.raises(ValueError, match="queue must not be blank"):
            self.validate(queue="   ", limit=10)


class TestProducerTracking:
    @pytest.mark.oban(node="work-1", queues={"alpha": 1, "gamma": 2})
    async def test_producer_records_created_on_start(self, oban_instance):
        async with oban_instance() as oban, oban._connection() as conn:
            alpha, gamma = await all_producers(conn)

            assert alpha[0]
            assert alpha[1] == "Oban"
            assert alpha[2] == "work-1"
            assert alpha[3] == "alpha"
            assert alpha[4]["local_limit"] == 1

            assert gamma[0]
            assert gamma[1] == "Oban"
            assert gamma[2] == "work-1"
            assert gamma[3] == "gamma"
            assert gamma[4]["local_limit"] == 2

    @pytest.mark.oban(queues={"alpha": 1})
    async def test_producer_records_deleted_on_stop(self, oban_instance):
        oban = oban_instance()

        await oban.start()

        async with oban._connection() as conn:
            records = await all_producers(conn)

            assert len(records) == 1

        await oban.stop()

        async with oban._connection() as conn:
            records = await all_producers(conn)

            assert len(records) == 0


class TestProducerTelemetry:
    @pytest.mark.oban(queues={"default": 5})
    async def test_emits_fetch_events(self, oban_instance):
        @worker()
        class SimpleWorker:
            async def process(self, job):
                pass

        calls = asyncio.Queue()

        def handler(_name, meta):
            calls.put_nowait(meta)

        telemetry.attach("test-producer", ["oban.producer.get.stop"], handler)

        async with oban_instance() as oban:
            await oban.enqueue_many(SimpleWorker.new(), SimpleWorker.new())

            meta = await asyncio.wait_for(calls.get(), timeout=1.0)

        assert meta["queue"] == "default"
        assert meta["count"] == 2

        telemetry.detach("test-producer")


class TestPausedProducers:
    @pytest.mark.oban(queues={"default": {"limit": 2, "paused": True}})
    async def test_paused_producers_get_jobs_without_fetching(
        self, oban_instance, monkeypatch
    ):
        calls = asyncio.Queue()

        async def get_jobs(producer):
            calls.put_nowait(producer._paused)

            return await _get_jobs(producer)

        monkeypatch.setitem(_extensions, "producer.get_jobs", None)
        put_ext("producer.get_jobs", get_jobs)

        async with oban_instance() as oban:
            job = await oban.enqueue(Job(worker="Unused", queue="default"))

            oban._producers["default"].notify()

            assert await asyncio.wait_for(calls.get(), timeout=1.0) is True
            await assert_state(oban, job.id, "available")


class TestProducerAcks:
    @pytest.mark.oban(queues={"default": 1})
    async def test_paused_queue_acks_completed_job(self, oban_instance):
        finished = asyncio.Event()

        async with oban_instance() as oban:

            @worker()
            class PausingWorker:
                async def process(self, job):
                    await oban.pause_queue("default")
                    finished.set()

            job = await oban.enqueue(PausingWorker.new())

            await asyncio.wait_for(finished.wait(), timeout=1.0)
            await with_backoff(lambda: assert_state(oban, job.id, "completed"))

    @pytest.mark.oban(queues={"default": 1})
    async def test_stale_ack_ignored_after_job_reattempted(self, oban_instance):
        finished = asyncio.Event()

        async with oban_instance() as oban:

            @worker()
            class ReattemptedWorker:
                async def process(self, job):
                    async with oban._connection() as conn:
                        await conn.execute(
                            "UPDATE oban_jobs SET attempted_at = attempted_at + interval '1 second' WHERE id = %s",
                            (job.id,),
                        )

                    finished.set()

            job = await oban.enqueue(ReattemptedWorker.new())

            await asyncio.wait_for(finished.wait(), timeout=1.0)

            producer = oban._producers["default"]

            # The ACK is pending once the job leaves running, and flushed once pending is empty
            await with_backoff(
                lambda: not producer._running_jobs and not producer._pending_acks
            )

            await assert_state(oban, job.id, "executing")

    @pytest.mark.oban(queues={"default": 1})
    async def test_cancel_ack_records_error_for_cancelled_job(self, oban_instance):
        finished = asyncio.Event()

        async with oban_instance() as oban:

            @worker()
            class CancelledWorker:
                async def process(self, job):
                    async with oban._connection() as conn:
                        await conn.execute(
                            "UPDATE oban_jobs SET state = 'cancelled' WHERE id = %s",
                            (job.id,),
                        )

                    finished.set()

                    return Cancel("cancelled elsewhere")

            job = await oban.enqueue(CancelledWorker.new())

            await asyncio.wait_for(finished.wait(), timeout=1.0)

            async def assert_error_recorded():
                fetched = await oban.get_job(job.id)

                assert fetched is not None and len(fetched.errors) == 1

            await with_backoff(assert_error_recorded)
            await assert_state(oban, job.id, "cancelled")


class TestProducerShutdown:
    @pytest.mark.oban(queues={"default": 1}, shutdown_grace_period=1.0)
    async def test_waiting_for_jobs_within_grace_period(self, oban_instance):
        started = asyncio.Event()

        @worker()
        class BriefWorker:
            async def process(self, job):
                started.set()
                await asyncio.sleep(0.05)

        oban = oban_instance()

        async with oban:
            job = await oban.enqueue(BriefWorker.new())

            await asyncio.wait_for(started.wait(), timeout=1.0)

        fetched = await oban.get_job(job.id)

        assert fetched.state == "completed"

    @pytest.mark.oban(
        queues={"default": 1}, refresher={"interval": 0.01}, shutdown_grace_period=5.0
    )
    async def test_staying_registered_while_waiting_for_jobs(self, oban_instance):
        started = asyncio.Event()
        finish = asyncio.Event()

        @worker()
        class WaitingWorker:
            async def process(self, job):
                started.set()
                await finish.wait()

        oban = oban_instance()

        await oban.start()
        await oban.enqueue(WaitingWorker.new())
        await asyncio.wait_for(started.wait(), timeout=1.0)

        stopping = asyncio.create_task(oban.stop())

        async def refreshed_at():
            async with oban._connection() as conn:
                result = await conn.execute(
                    "SELECT updated_at FROM oban_producers WHERE meta ? 'shutdown_started_at'"
                )
                row = await result.fetchone()

                assert row is not None

                return row[0]

        await with_backoff(refreshed_at)

        draining = await refreshed_at()

        async def assert_refreshed():
            assert await refreshed_at() > draining

        await with_backoff(assert_refreshed)

        finish.set()

        await stopping

        async with oban._connection() as conn:
            result = await conn.execute("SELECT uuid FROM oban_producers")

            assert await result.fetchall() == []

    @pytest.mark.oban(queues={"default": 3}, shutdown_grace_period=0.05)
    async def test_cancelling_jobs_running_past_grace_period(self, oban_instance):
        started = asyncio.Semaphore(0)
        release = asyncio.Event()
        calls = []

        telemetry.attach(
            "test-shutdown",
            ["oban.queue.shutdown"],
            lambda _name, meta: calls.append(meta),
        )

        @worker()
        class EndlessWorker:
            async def process(self, job):
                started.release()
                await asyncio.sleep(10)

        @worker()
        class FinishingWorker:
            async def process(self, job):
                started.release()

                try:
                    await asyncio.sleep(10)
                except asyncio.CancelledError:
                    pass

        @worker()
        class StubbornWorker:
            async def process(self, job):
                started.release()

                try:
                    await asyncio.sleep(10)
                except asyncio.CancelledError:
                    await release.wait()

        oban = oban_instance()

        async with oban:
            endless = await oban.enqueue(EndlessWorker.new())
            finished = await oban.enqueue(FinishingWorker.new())
            stubborn = await oban.enqueue(StubbornWorker.new())

            for _ in range(3):
                await asyncio.wait_for(started.acquire(), timeout=1.0)

        release.set()

        assert (await oban.get_job(endless.id)).state == "executing"
        assert (await oban.get_job(finished.id)).state == "completed"
        assert (await oban.get_job(stubborn.id)).state == "executing"

        [meta] = calls

        assert meta["queue"] == "default"
        assert meta["orphaned"] == [endless.id, stubborn.id]
        assert meta["duration"] > 0


class TestProducerCancellation:
    async def test_loop_exits_when_cancellation_surfaces_as_another_error(self):
        producer = Producer(name="Oban", node="worker", query=None)
        producing = asyncio.Event()

        async def produce():
            producing.set()

            try:
                await asyncio.sleep(1)
            except asyncio.CancelledError:
                raise RuntimeError("connection closed")

        producer._produce = produce
        producer.notify()

        task = asyncio.create_task(producer._loop())
        await asyncio.wait_for(producing.wait(), timeout=1.0)
        task.cancel()

        await asyncio.wait([task], timeout=0.1)

        assert task.done()

    async def test_debounce_skipped_without_running_jobs(self):
        producer = Producer(
            debounce_interval=1.0, name="Oban", node="worker", query=None
        )

        producer._last_fetch_time = asyncio.get_running_loop().time()

        await asyncio.wait_for(producer._debounce(), timeout=0.05)

        producer._running_jobs[1] = (None, None)

        with pytest.raises(TimeoutError):
            await asyncio.wait_for(producer._debounce(), timeout=0.05)

    async def test_delegates_cancellation_to_dispatcher(self):
        class TestDispatcher:
            def __init__(self):
                self.cancelled = []

            def dispatch(self, producer, job):
                raise NotImplementedError

            def cancel(self, producer, job):
                self.cancelled.append((producer, job))

        dispatcher = TestDispatcher()
        producer = Producer(
            dispatcher=dispatcher,
            limit=1,
            name="Oban",
            node="worker",
            query=None,
            queue="default",
        )
        job = Job(worker="Worker", id=1)
        producer._running_jobs[job.id] = (job, None)

        producer.cancel(job.id)

        assert dispatcher.cancelled == [(producer, job)]

    async def test_delivers_messages_to_running_jobs(self, oban_instance):
        received = asyncio.Future()

        @worker()
        class InboxWorker:
            async def process(self, job):
                received.set_result(await job._inbox.get())

        async with oban_instance(queues={"default": 1}) as oban:
            job = await oban.enqueue(InboxWorker.new())

            await with_backoff(
                lambda: job.id in oban._producers["default"]._running_jobs
            )

            # Messages for jobs that aren't running here are dropped
            oban._producers["default"].send(job.id + 1, {"hello": "nobody"})
            oban._producers["default"].send(job.id, {"hello": "world"})

            assert await asyncio.wait_for(received, timeout=1.0) == {"hello": "world"}

    def test_local_dispatcher_sets_cancellation_event(self):
        job = Job(worker="Worker")
        job._cancellation = asyncio.Event()

        LocalDispatcher().cancel(None, job)

        assert job.cancelled()

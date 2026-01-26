import asyncio
import pytest

from oban import telemetry, worker
from oban._producer import Producer
from .helpers import with_backoff


async def all_producers(conn):
    result = await conn.execute("""
        SELECT uuid, name, node, queue, meta
        FROM oban_producers
        ORDER BY queue
    """)

    return await result.fetchall()


class TestProducerValidation:
    def validate(self, **opts):
        base = {"name": "Oban", "node": "worker", "notifier": None, "query": None}

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
        async with oban_instance() as oban:
            async with oban._connection() as conn:
                alpha, gamma = await all_producers(conn)

                assert alpha[0]
                assert alpha[1] == "oban"
                assert alpha[2] == "work-1"
                assert alpha[3] == "alpha"
                assert alpha[4]["local_limit"] == 1

                assert gamma[0]
                assert gamma[1] == "oban"
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

            # Give producer loop time to flush the ACK
            await asyncio.sleep(0.1)

            fetched = await oban.get_job(job.id)
            assert fetched is not None
            assert fetched.state == "completed"


import asyncio
from datetime import UTC, datetime, timedelta

from oban import Job, worker
from oban._unique import unique_lock_key


@worker()
class Worker:
    async def process(self, job):
        pass


@worker(unique=True)
class UniqueWorker:
    async def process(self, job):
        pass


def conflicted(job):
    return job.extra["conflicted"]


def seconds_ago(seconds):
    return datetime.now(UTC) - timedelta(seconds=seconds)


class TestUniqueInsert:
    async def test_inserting_a_job_with_uniqueness_applied(self, oban_instance):
        async with oban_instance() as oban:
            job_1 = await oban.enqueue(Worker.new({"ref": 1}, unique={"period": 60}))
            job_2 = await oban.enqueue(Worker.new({"ref": 1}, unique={"period": 60}))

            assert not conflicted(job_1)
            assert conflicted(job_2)
            assert job_2.id is None

    async def test_preventing_duplicate_jobs_between_processes(self, oban_instance):
        async with oban_instance() as oban:
            jobs = await asyncio.gather(
                *(oban.enqueue(UniqueWorker.new({"ref": 1})) for _ in range(3))
            )

            assert sorted(conflicted(job) for job in jobs) == [False, True, True]

    async def test_preventing_duplicates_within_a_bulk_insert(self, oban_instance):
        async with oban_instance() as oban:
            jobs = await oban.enqueue_many(
                UniqueWorker.new({"id": 1}),
                UniqueWorker.new({"id": 2}),
                UniqueWorker.new({"id": 1}),
                Worker.new({"id": 1}),
            )

            assert [conflicted(job) for job in jobs] == [False, False, True, False]

    async def test_inserting_unique_jobs_within_a_transaction(self, oban_instance):
        async with oban_instance() as oban:
            async with oban._connection() as conn, conn.transaction():
                job_1 = await oban.enqueue(UniqueWorker.new({"id": 1}), conn=conn)
                job_2 = await oban.enqueue(UniqueWorker.new({"id": 1}), conn=conn)

            assert not conflicted(job_1)
            assert conflicted(job_2)

    async def test_scoping_uniqueness_to_specific_fields(self, oban_instance):
        async with oban_instance() as oban:
            unique = {"fields": ["worker"]}

            job_1 = await oban.enqueue(Job("A", unique=unique))
            job_2 = await oban.enqueue(Job("B", unique=unique))
            job_3 = await oban.enqueue(Job("A", args={"id": 1}, unique=unique))

            assert not conflicted(job_1)
            assert not conflicted(job_2)
            assert conflicted(job_3)

    async def test_scoping_uniqueness_to_specific_argument_keys(self, oban_instance):
        async with oban_instance() as oban:

            async def insert(args, keys):
                return await oban.enqueue(Worker.new(args, unique={"keys": keys}))

            assert not conflicted(await insert({"id": 1, "xd": 1}, ["id"]))
            assert not conflicted(await insert({"id": 2, "xd": 2}, ["id"]))
            assert conflicted(await insert({"id": 3, "xd": 1}, ["xd"]))
            assert conflicted(await insert({"id": 1, "xd": 3}, ["id"]))

    async def test_considering_empty_args_distinct_from_non_empty(self, oban_instance):
        async with oban_instance() as oban:
            assert not conflicted(await oban.enqueue(UniqueWorker.new({"id": 1})))
            assert not conflicted(await oban.enqueue(UniqueWorker.new({})))
            assert conflicted(await oban.enqueue(UniqueWorker.new({})))

    async def test_considering_all_args_to_establish_uniqueness(self, oban_instance):
        async with oban_instance() as oban:
            assert not conflicted(await oban.enqueue(UniqueWorker.new({"id": 1})))
            assert not conflicted(
                await oban.enqueue(UniqueWorker.new({"id": 1, "extra": "cool"}))
            )

            assert not conflicted(
                await oban.enqueue(UniqueWorker.new({"id": 2, "extra": "cool"}))
            )
            assert not conflicted(await oban.enqueue(UniqueWorker.new({"id": 2})))

    async def test_considering_none_arg_values_for_uniqueness(self, oban_instance):
        async with oban_instance() as oban:
            args = {"id": 1, "tenant": None}

            assert not conflicted(await oban.enqueue(UniqueWorker.new(args)))
            assert conflicted(await oban.enqueue(UniqueWorker.new(args)))
            assert not conflicted(await oban.enqueue(UniqueWorker.new({"id": 1})))

    async def test_scoping_uniqueness_by_specific_meta_keys(self, oban_instance):
        async with oban_instance() as oban:
            unique = {"fields": ["meta"], "keys": ["slug"]}

            async def insert(slug, ref):
                meta = {"slug": slug, "ref": ref}

                return await oban.enqueue(Worker.new({}, meta=meta, unique=unique))

            assert not conflicted(await insert("abc123", 1))
            assert not conflicted(await insert("def456", 2))
            assert conflicted(await insert("abc123", 3))
            assert conflicted(await insert("def456", 4))

    async def test_scoping_uniqueness_by_state_group(self, oban_instance):
        async with oban_instance() as oban:
            for id, state in enumerate(["scheduled", "executing", "completed"]):
                await oban.enqueue(Worker.new({"id": id}, state=state))

            async def insert(id, group):
                return await oban.enqueue(
                    Worker.new({"id": id}, unique={"group": group})
                )

            assert conflicted(await insert(0, "scheduled"))
            assert conflicted(await insert(1, "incomplete"))
            assert not conflicted(await insert(1, "scheduled"))
            assert conflicted(await insert(2, "successful"))
            assert not conflicted(await insert(2, "incomplete"))

    async def test_scoping_uniqueness_by_period_from_scheduled_at(self, oban_instance):
        async with oban_instance() as oban:
            for id, seconds in enumerate([240, 540]):
                await oban.enqueue(
                    Worker.new({"id": id}, scheduled_at=seconds_ago(seconds))
                )

            async def insert(id, period):
                return await oban.enqueue(
                    Worker.new({"id": id}, unique={"period": period})
                )

            assert conflicted(await insert(0, 241))
            assert not conflicted(await insert(0, 239))
            assert conflicted(await insert(1, timedelta(minutes=10)))
            assert conflicted(await insert(1, None))


class TestUniqueLockKey:
    def test_serializing_compact_json_with_sorted_keys(self):
        job = Job(
            worker="Worker",
            args={"b": 2, "a": {"d": [1, "x"], "c": None}},
            unique={"fields": ["worker", "args"]},
        )

        assert unique_lock_key("public", job) == (
            '["public","all",null,[["args",{"a":{"c":null,"d":[1,"x"]},"b":2}],["worker","Worker"]]]'
        )

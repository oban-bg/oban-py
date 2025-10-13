import pytest


async def insert_executing_job(conn, node, uuid):
    rows = await conn.execute(
        """
        INSERT INTO oban_jobs (state, worker, attempted_by)
        VALUES ('executing', 'Worker', %s)
        RETURNING id
        """,
        ([node, uuid],),
    )

    (id,) = await rows.fetchone()

    return id


async def get_job(conn, job_id):
    rows = await conn.execute(
        "SELECT id, state, meta FROM oban_jobs WHERE id = %s", (job_id,)
    )

    return await rows.fetchone()


async def insert_producer(conn, node, queue, uuid):
    await conn.execute(
        """
        INSERT INTO oban_producers (uuid, node, queue, updated_at)
        VALUES (%s, %s, %s, timezone('UTC', now()))
        """,
        (uuid, node, queue),
    )


class TestLifeline:
    @pytest.mark.oban(leadership=True, queues={"alpha": 1})
    async def test_lifeline_rescues_jobs_without_live_producers(self, oban_instance):
        oban = oban_instance()

        async with oban._connection() as conn:
            async with conn.transaction():
                job_id = await insert_executing_job(conn, "dead-node", "dead-uuid")

        await oban.start()

        # Force synchronous rescue
        await oban._lifeline._rescue()

        async with oban._connection() as conn:
            job = await get_job(conn, job_id)

        assert job is not None
        assert job[1] == "available"
        assert job[2]["rescued"] == 1

        await oban.stop()

    @pytest.mark.oban(leadership=True, queues={"alpha": 1})
    async def test_lifeline_skips_jobs_with_live_producers(self, oban_instance):
        oban = oban_instance()

        await oban.start()

        live_uuid = oban._producers["alpha"]._uuid
        live_node = oban._node

        async with oban._connection() as conn:
            async with conn.transaction():
                job_id = await insert_executing_job(conn, live_node, live_uuid)

        await oban._lifeline._rescue()

        async with oban._connection() as conn:
            job = await get_job(conn, job_id)

        assert job is not None
        assert job[1] == "executing"
        assert "rescued" not in job[2]

        await oban.stop()

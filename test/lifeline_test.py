import pytest

from oban._lifeline import Lifeline


async def insert_executing_job(
    conn,
    node="dead-node",
    uuid="dead-uuid",
    attempt=1,
    max_attempts=20,
    old_attempt=False,
):
    attempted_at = (
        "timezone('UTC', now()) - interval '10 minutes'"
        if old_attempt
        else "timezone('UTC', now())"
    )

    rows = await conn.execute(
        f"""
        INSERT INTO oban_jobs (state, worker, attempted_by, attempt, max_attempts, attempted_at)
        VALUES ('executing', 'Worker', %s, %s, %s, {attempted_at})
        RETURNING id
        """,
        ([node, uuid], attempt, max_attempts),
    )

    (id,) = await rows.fetchone()

    return id


async def insert_available_job(conn, attempt=20, max_attempts=20):
    rows = await conn.execute(
        """
        INSERT INTO oban_jobs (state, worker, attempt, max_attempts)
        VALUES ('available', 'Worker', %s, %s)
        RETURNING id
        """,
        (attempt, max_attempts),
    )

    (id,) = await rows.fetchone()

    return id


async def get_job(conn, job_id):
    rows = await conn.execute(
        "SELECT id, state, meta, discarded_at FROM oban_jobs WHERE id = %s", (job_id,)
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


class TestLifelineValidation:
    def test_valid_config_passes(self):
        Lifeline._validate(interval=60.0, rescue_after=300.0)

    def test_interval_must_be_numeric(self):
        with pytest.raises(TypeError, match="interval must be a number"):
            Lifeline._validate(interval="not a number", rescue_after=300.0)

    def test_interval_must_be_positive(self):
        with pytest.raises(ValueError, match="interval must be positive"):
            Lifeline._validate(interval=0, rescue_after=300.0)

        with pytest.raises(ValueError, match="interval must be positive"):
            Lifeline._validate(interval=-1.0, rescue_after=300.0)

    def test_rescue_after_must_be_numeric(self):
        with pytest.raises(TypeError, match="rescue_after must be a number"):
            Lifeline._validate(interval=60.0, rescue_after="not a number")

    def test_rescue_after_must_be_positive(self):
        with pytest.raises(ValueError, match="rescue_after must be positive"):
            Lifeline._validate(interval=60.0, rescue_after=0)

        with pytest.raises(ValueError, match="rescue_after must be positive"):
            Lifeline._validate(interval=60.0, rescue_after=-1.0)


class TestLifeline:
    @pytest.mark.oban(leadership=True, queues={"alpha": 1})
    async def test_lifeline_rescues_old_executing_jobs(self, oban_instance):
        oban = oban_instance()

        async with oban._connection() as conn:
            async with conn.transaction():
                job_id = await insert_executing_job(conn, old_attempt=True)

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
    async def test_lifeline_skips_recent_executing_jobs(self, oban_instance):
        oban = oban_instance()

        await oban.start()

        async with oban._connection() as conn:
            async with conn.transaction():
                job_id = await insert_executing_job(conn, old_attempt=False)

        await oban._lifeline._rescue()

        async with oban._connection() as conn:
            job = await get_job(conn, job_id)

        assert job is not None
        assert job[1] == "executing"
        assert "rescued" not in job[2]

        await oban.stop()

    @pytest.mark.oban(leadership=True, queues={"alpha": 1})
    async def test_lifeline_discards_jobs_without_remaining_attempts(
        self, oban_instance
    ):
        oban = oban_instance()

        async with oban._connection() as conn:
            async with conn.transaction():
                job_id = await insert_executing_job(
                    conn, attempt=1, max_attempts=1, old_attempt=True
                )

        await oban.start()

        # Force synchronous rescue
        await oban._lifeline._rescue()

        async with oban._connection() as conn:
            job = await get_job(conn, job_id)

        assert job is not None
        assert job[1] == "discarded"
        assert "rescued" not in job[2]
        assert job[3] is not None

        await oban.stop()

    @pytest.mark.oban(leadership=True, queues={"alpha": 1})
    async def test_lifeline_discards_stranded_available_jobs(self, oban_instance):
        oban = oban_instance()

        async with oban._connection() as conn:
            async with conn.transaction():
                stranded = await insert_available_job(conn, attempt=20, max_attempts=20)
                ready = await insert_available_job(conn, attempt=0, max_attempts=20)

        await oban.start()

        # Force synchronous rescue
        await oban._lifeline._rescue()

        async with oban._connection() as conn:
            stranded_job = await get_job(conn, stranded)
            ready_job = await get_job(conn, ready)

        # The maxed-out available job is unfetchable, so it's discarded outright.
        assert stranded_job[1] == "discarded"
        assert "rescued" not in stranded_job[2]
        assert stranded_job[3] is not None

        # A fetchable available job is left untouched.
        assert ready_job[1] == "available"
        assert ready_job[3] is None

        await oban.stop()

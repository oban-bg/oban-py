import pytest

from oban._pruner import Pruner


async def insert_job(conn, state, ago):
    ts_field = f"{state}_at"

    rows = await conn.execute(
        f"""
            INSERT INTO oban_jobs (state, worker, {ts_field})
            VALUES (%s, 'Worker', timezone('UTC', now()) - make_interval(secs => %s))
            RETURNING id
            """,
        (state, ago),
    )

    (id,) = await rows.fetchone()

    return id


async def get_ids(conn):
    rows = await conn.execute("SELECT id FROM oban_jobs ORDER BY id")
    result = await rows.fetchall()

    return [id for (id,) in result]


class TestPrunerValidation:
    def test_valid_config_passes(self):
        Pruner._validate(max_age=86_400, interval=60.0, limit=20_000)

    def test_max_age_must_be_integer(self):
        with pytest.raises(TypeError, match="max_age must be an integer"):
            Pruner._validate(max_age=86_400.5, interval=60.0, limit=20_000)

        with pytest.raises(TypeError, match="max_age must be an integer"):
            Pruner._validate(max_age="86400", interval=60.0, limit=20_000)

    def test_max_age_must_be_positive(self):
        with pytest.raises(ValueError, match="max_age must be positive"):
            Pruner._validate(max_age=0, interval=60.0, limit=20_000)

        with pytest.raises(ValueError, match="max_age must be positive"):
            Pruner._validate(max_age=-1, interval=60.0, limit=20_000)

    def test_interval_must_be_numeric(self):
        with pytest.raises(TypeError, match="interval must be a number"):
            Pruner._validate(max_age=86_400, interval="not a number", limit=20_000)

    def test_interval_must_be_positive(self):
        with pytest.raises(ValueError, match="interval must be positive"):
            Pruner._validate(max_age=86_400, interval=0, limit=20_000)

        with pytest.raises(ValueError, match="interval must be positive"):
            Pruner._validate(max_age=86_400, interval=-1.0, limit=20_000)

    def test_limit_must_be_integer(self):
        with pytest.raises(TypeError, match="limit must be an integer"):
            Pruner._validate(max_age=86_400, interval=60.0, limit=999.5)

        with pytest.raises(TypeError, match="limit must be an integer"):
            Pruner._validate(max_age=86_400, interval=60.0, limit="20000")

    def test_limit_must_be_positive(self):
        with pytest.raises(ValueError, match="limit must be positive"):
            Pruner._validate(max_age=86_400, interval=60.0, limit=0)

        with pytest.raises(ValueError, match="limit must be positive"):
            Pruner._validate(max_age=86_400, interval=60.0, limit=-1)

    def test_boundary_values_pass(self):
        # Minimum allowed values
        Pruner._validate(max_age=60, interval=1.0, limit=1)

        # Maximum allowed limit
        Pruner._validate(max_age=86_400, interval=60.0, limit=100_000)


class TestPruner:
    @pytest.mark.oban(leadership=True, pruner={"max_age": 60})
    async def test_pruner_deletes_expired_jobs(self, oban_instance):
        async with oban_instance() as oban:
            # Insert jobs and commit them so pruner can see them
            async with oban._connection() as conn:
                async with conn.transaction():
                    await insert_job(conn, "completed", 61)
                    await insert_job(conn, "cancelled", 61)
                    await insert_job(conn, "cancelled", 61)
                    await insert_job(conn, "discarded", 61)

                    id_1 = await insert_job(conn, "scheduled", 61)
                    id_2 = await insert_job(conn, "completed", 59)
                    id_3 = await insert_job(conn, "discarded", 59)

            # Force synchronous pruning
            await oban._pruner._prune()

            async with oban._connection() as conn:
                job_ids = await get_ids(conn)

            assert [id_1, id_2, id_3] == job_ids

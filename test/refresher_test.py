import pytest

from oban._refresher import Refresher


async def all_producers(conn):
    result = await conn.execute("""
        SELECT uuid, name, node, queue
        FROM oban_producers
        ORDER BY queue
    """)

    return await result.fetchall()


async def get_updated_at(conn, queue):
    result = await conn.execute(
        "SELECT updated_at FROM oban_producers WHERE queue = %s", (queue,)
    )

    return (await result.fetchone())[0]


async def insert_stale_producer(conn, queue, max_age):
    result = await conn.execute(
        """
        INSERT INTO oban_producers (uuid, node, queue, updated_at)
        VALUES (
            gen_random_uuid(),
            'stale',
            %s,
            timezone('UTC', now()) - make_interval(secs => %s + 10)
        )
        RETURNING uuid
        """,
        (queue, max_age),
    )

    return (await result.fetchone())[0]


class TestRefresherValidation:
    def test_valid_config_passes(self):
        Refresher._validate(interval=15.0, max_age=60.0)

    def test_interval_must_be_numeric(self):
        with pytest.raises(TypeError, match="interval must be a number"):
            Refresher._validate(interval="not a number", max_age=60.0)

    def test_interval_must_be_positive(self):
        with pytest.raises(ValueError, match="interval must be positive"):
            Refresher._validate(interval=0, max_age=60.0)

        with pytest.raises(ValueError, match="interval must be positive"):
            Refresher._validate(interval=-1.0, max_age=60.0)

    def test_max_age_must_be_numeric(self):
        with pytest.raises(TypeError, match="max_age must be a number"):
            Refresher._validate(interval=15.0, max_age="not a number")

    def test_max_age_must_be_positive(self):
        with pytest.raises(ValueError, match="max_age must be positive"):
            Refresher._validate(interval=15.0, max_age=0)

        with pytest.raises(ValueError, match="max_age must be positive"):
            Refresher._validate(interval=15.0, max_age=-1.0)


class TestRefresher:
    @pytest.mark.oban(queues={"alpha": 1, "gamma": 1})
    async def test_refresher_updates_producer_timestamps(self, oban_instance):
        oban = oban_instance()

        await oban.start()

        async with oban._connection() as conn:
            initial_alpha_at = await get_updated_at(conn, "alpha")
            initial_gamma_at = await get_updated_at(conn, "gamma")

            # Call refresh directly to update timestamp
            await oban._refresher._refresh()

            updated_alpha_at = await get_updated_at(conn, "alpha")
            updated_gamma_at = await get_updated_at(conn, "gamma")

            assert updated_alpha_at > initial_alpha_at
            assert updated_gamma_at > initial_gamma_at

        await oban.stop()

    @pytest.mark.oban(queues={"alpha": 1, "gamma": 1})
    async def test_refresher_restores_missing_producers(self, oban_instance):
        oban = oban_instance()

        await oban.start()

        async with oban._connection() as conn:
            initial = await all_producers(conn)

            # Simulate unlogged tables being reset after a crash or failover
            await conn.execute("DELETE FROM oban_producers")

        await oban._refresher._refresh()

        async with oban._connection() as conn:
            assert await all_producers(conn) == initial

        await oban.stop()

    @pytest.mark.oban(leadership=True, queues={"alpha": 1})
    async def test_leader_cleans_up_expired_producers(self, oban_instance):
        oban = oban_instance(refresher={"interval": 0.05, "max_age": 0.1})

        async with oban._connection() as conn:
            uuid = await insert_stale_producer(conn, "gamma", 0.1)

        await oban.start()
        await oban._refresher._cleanup()

        async with oban._connection() as conn:
            (alpha,) = await all_producers(conn)

            assert alpha[0] != uuid
            assert alpha[3] == "alpha"

        await oban.stop()

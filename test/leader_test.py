import asyncio

import pytest

from oban._leader import Leader
from .helpers import with_backoff


async def take_over(oban, expires_in):
    async with oban._connection() as conn:
        await conn.execute(
            f"""
            UPDATE oban_leaders
            SET node = 'other-node',
                expires_at = timezone('UTC', now()) + interval '{expires_in}'
            """
        )


class TestLeaderValidation:
    def test_valid_config_passes(self):
        Leader._validate(interval=30.0)

    def test_interval_must_be_numeric(self):
        with pytest.raises(TypeError, match="interval must be a number"):
            Leader._validate(interval="not a number")

    def test_interval_must_be_positive(self):
        with pytest.raises(ValueError, match="interval must be positive"):
            Leader._validate(interval=0)

        with pytest.raises(ValueError, match="interval must be positive"):
            Leader._validate(interval=-1.0)


class FakeQuery:
    def __init__(self):
        self.failing = False
        self.in_flight = 0
        self.max_in_flight = 0

    async def attempt_leadership(self, name, node, ttl):
        self.in_flight += 1
        self.max_in_flight = max(self.max_in_flight, self.in_flight)

        try:
            await asyncio.sleep(0.01)

            if self.failing:
                raise RuntimeError("connection lost")

            return True
        finally:
            self.in_flight -= 1


class TestElection:
    @pytest.fixture
    def leader(self):
        return Leader(node="web.1", notifier=None, query=FakeQuery())

    async def test_concurrent_elections_run_serially(self, leader):
        await asyncio.gather(leader._election(), leader._election())

        assert leader.is_leader
        assert leader._query.max_in_flight == 1

    async def test_conceding_leadership_when_election_fails(self, leader):
        await leader._election()

        assert leader.is_leader

        leader._query.failing = True

        with pytest.raises(RuntimeError):
            await leader._election()

        assert not leader.is_leader

        leader._query.failing = False

        await leader._election()

        assert leader.is_leader


class TestLeadership:
    @pytest.mark.oban(leadership=True)
    async def test_single_instance_becomes_leader(self, oban_instance):
        async with oban_instance() as oban:
            assert oban.is_leader

    @pytest.mark.oban(leadership=False)
    async def test_instance_with_leadership_disabled(self, oban_instance):
        async with oban_instance() as oban:
            assert not oban.is_leader

    @pytest.mark.oban(queues={})
    async def test_client_mode_does_not_elect_leader(self, oban_instance):
        async with oban_instance() as oban:
            assert not oban.is_leader

    @pytest.mark.oban(leadership=True)
    async def test_multiple_instances_elect_single_leader(self, oban_instance):
        oban_1 = oban_instance(node="web.1")
        oban_2 = oban_instance(node="web.2")

        await oban_1.start()
        await oban_2.start()

        try:
            leaders = [oban_1.is_leader, oban_2.is_leader]

            assert list(filter(None, leaders)) == [True]
        finally:
            await oban_1.stop()
            await oban_2.stop()

    @pytest.mark.oban(leadership=True)
    async def test_retaining_leadership_while_lease_is_held(self, oban_instance):
        async with oban_instance() as oban:
            assert oban.is_leader

            await oban._leader._election()

            assert oban.is_leader

    @pytest.mark.oban(leadership=True)
    async def test_conceding_leadership_after_takeover(self, oban_instance):
        async with oban_instance() as oban:
            assert oban.is_leader

            await take_over(oban, expires_in="30 seconds")
            await oban._leader._election()

            assert not oban.is_leader

    @pytest.mark.oban(leadership=True)
    async def test_assuming_leadership_after_lease_expires(self, oban_instance):
        async with oban_instance() as oban:
            assert oban.is_leader

            await take_over(oban, expires_in="-1 seconds")
            await oban._leader._election()

            assert oban.is_leader

    @pytest.mark.oban(leadership=True)
    async def test_leader_resigns_on_stop(self, oban_instance):
        oban_1 = oban_instance(node="web.1")
        oban_2 = oban_instance(node="web.2")

        try:
            await oban_1.start()

            assert oban_1.is_leader
            assert not oban_2.is_leader

            await oban_1.stop()
            await oban_2.start()

            assert oban_2.is_leader
        finally:
            await oban_2.stop()

    @pytest.mark.oban(leadership=True)
    async def test_leader_notifies_on_shutdown(self, oban_instance):
        oban_1 = oban_instance(node="web.1")
        oban_2 = oban_instance(node="web.2")

        try:
            await oban_1.start()
            await oban_2.start()

            assert oban_1.is_leader != oban_2.is_leader

            lead = oban_1 if oban_1.is_leader else oban_2
            peer = oban_2 if oban_1.is_leader else oban_1

            await lead.stop()

            # The default interval is 30.0s, notification makes it immediate
            def assert_peer_is_leader():
                assert peer.is_leader

            await with_backoff(assert_peer_is_leader, timeout=0.5)
        finally:
            await oban_1.stop()
            await oban_2.stop()

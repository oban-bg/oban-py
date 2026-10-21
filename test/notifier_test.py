import asyncio

import pytest
from psycopg_pool import AsyncConnectionPool

from oban import Oban
from oban._notifier import PostgresNotifier, decode_payload, encode_payload


class TestEncodeDecode:
    def test_encode_payload(self):
        payload = {"queue": "default", "id": 123}
        encoded = encode_payload(payload)

        assert isinstance(encoded, str)
        assert not encoded.startswith("{")

    def test_decode_payload_roundtrip(self):
        original = {"queue": "default", "id": 123, "data": "test"}
        encoded = encode_payload(original)
        decoded = decode_payload(encoded)

        assert decoded == original

    def test_decode_payload_plain_json(self):
        payload = {"queue": "default", "id": 456}
        decoded = decode_payload('{"queue": "default", "id": 456}')

        assert decoded == payload


class TestBuildNotifier:
    async def test_building_with_options(self, oban_instance):
        notifier = oban_instance(notifier={"beat_interval": 5.0})._notifier

        assert isinstance(notifier, PostgresNotifier)
        assert notifier._beat_interval == 5.0

    async def test_rejecting_unknown_types(self, oban_instance):
        with pytest.raises(ValueError, match="unknown notifier type: 'carrier'"):
            oban_instance(notifier={"type": "carrier"})


class TestNotifier:
    async def test_connecting_with_callable_conninfo(self, test_dsn):
        received = asyncio.Queue()

        async def fetch_dsn():
            return test_dsn

        def callback(channel, payload):
            received.put_nowait((channel, payload))

        pool = AsyncConnectionPool(conninfo=fetch_dsn, open=False)

        async with pool, Oban(pool=pool) as oban:
            await oban._notifier.listen("testing", callback)
            await oban._notifier.notify("testing", {"message": "hello"})

            result = await asyncio.wait_for(received.get(), timeout=1.0)

            assert result == ("testing", {"message": "hello"})

    @pytest.mark.oban()
    async def test_listen_and_notify(self, oban_instance):
        received = asyncio.Queue()

        def callback(channel, payload):
            received.put_nowait((channel, payload))

        async with oban_instance() as oban:
            await oban._notifier.listen("testing", callback)
            await oban._notifier.notify("testing", {"message": "hello"})

            result = await asyncio.wait_for(received.get(), timeout=1.0)

            assert result == ("testing", {"message": "hello"})

    @pytest.mark.oban()
    async def test_multiple_subscribers_same_channel(self, oban_instance):
        received_1 = asyncio.Queue()
        received_2 = asyncio.Queue()

        def callback_1(channel, payload):
            received_1.put_nowait((channel, payload))

        def callback_2(channel, payload):
            received_2.put_nowait((channel, payload))

        async with oban_instance() as oban:
            await oban._notifier.listen("testing", callback_1)
            await oban._notifier.listen("testing", callback_2)
            await oban._notifier.notify("testing", {"data": "test"})

            result_1 = await asyncio.wait_for(received_1.get(), timeout=1.0)
            result_2 = await asyncio.wait_for(received_2.get(), timeout=1.0)

            assert result_1 == result_2 == ("testing", {"data": "test"})

    @pytest.mark.oban()
    async def test_unlisten_removes_subscription(self, oban_instance):
        received_1 = asyncio.Event()
        received_2 = asyncio.Queue()

        def callback_1(_channel, _payload):
            received_1.set()

        def callback_2(channel, payload):
            received_2.put_nowait((channel, payload))

        async with oban_instance() as oban:
            token = await oban._notifier.listen("testing", callback_1)
            await oban._notifier.listen("testing", callback_2)

            await oban._notifier.unlisten(token)
            await oban._notifier.notify("testing", {})

            await asyncio.wait_for(received_2.get(), timeout=1.0)

            assert not received_1.is_set()

    @pytest.mark.oban()
    async def test_callback_exception_doesnt_crash_notifier(self, oban_instance):
        received = asyncio.Queue()

        def boom_callback(_channel, _payload):
            raise ValueError("boom")

        def good_callback(channel, payload):
            received.put_nowait((channel, payload))

        async with oban_instance() as oban:
            await oban._notifier.listen("testing", boom_callback)
            await oban._notifier.listen("testing", good_callback)

            await oban._notifier.notify("testing", {"test": "data"})

            await asyncio.wait_for(received.get(), timeout=1.0)

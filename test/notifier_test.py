import asyncio
import pytest


class TestNotifier:
    # TODO: Enforce json payloads
    # TODO: Compress all payloads
    # TODO: Test stager (it should only send the active queue)
    # TODO: Notify within insert_all for the distinct queues, no trigger

    @pytest.mark.oban()
    async def test_listen_and_notify(self, oban_instance):
        received = asyncio.Queue()

        def callback(channel: str, payload: str):
            received.put_nowait((channel, payload))

        async with oban_instance() as oban:
            await oban._notifier.listen("testing", callback)
            await oban._notifier.notify("testing", "hello")

            result = await asyncio.wait_for(received.get(), timeout=1.0)

            assert result == ("testing", "hello")

    @pytest.mark.oban()
    async def test_multiple_subscribers_same_channel(self, oban_instance):
        received_1 = asyncio.Queue()
        received_2 = asyncio.Queue()

        def callback_1(channel: str, payload: str):
            received_1.put_nowait((channel, payload))

        def callback_2(channel: str, payload: str):
            received_2.put_nowait((channel, payload))

        async with oban_instance() as oban:
            await oban._notifier.listen("testing", callback_1)
            await oban._notifier.listen("testing", callback_2)
            await oban._notifier.notify("testing", "payload")

            result_1 = await asyncio.wait_for(received_1.get(), timeout=1.0)
            result_2 = await asyncio.wait_for(received_2.get(), timeout=1.0)

            assert result_1 == result_2 == ("testing", "payload")

    @pytest.mark.oban()
    async def test_unlisten_removes_subscription(self, oban_instance):
        received_1 = asyncio.Event()
        received_2 = asyncio.Queue()

        def callback_1(_channel: str, _payload: str):
            received_1.set()

        def callback_2(channel: str, payload: str):
            received_2.put_nowait((channel, payload))

        async with oban_instance() as oban:
            token = await oban._notifier.listen("testing", callback_1)
            await oban._notifier.listen("testing", callback_2)

            await oban._notifier.unlisten(token)
            await oban._notifier.notify("testing", "payload")

            await asyncio.wait_for(received_2.get(), timeout=1.0)

            assert not received_1.is_set()

    @pytest.mark.oban()
    async def test_callback_exception_doesnt_crash_notifier(self, oban_instance):
        received = asyncio.Queue()

        def boom_callback(channel: str, payload: str):
            raise ValueError("boom")

        def good_callback(channel: str, payload: str):
            received.put_nowait((channel, payload))

        async with oban_instance() as oban:
            await oban._notifier.listen("testing", boom_callback)
            await oban._notifier.listen("testing", good_callback)

            await oban._notifier.notify("testing", "test")

            await asyncio.wait_for(received.get(), timeout=1.0)

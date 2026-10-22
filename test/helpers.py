import asyncio
import time


async def with_backoff(check_fn, timeout=1.0, interval=0.01):
    """Poll a condition until it's true or timeout is reached."""
    start = time.time()
    last_error = None

    while time.time() - start < timeout:
        try:
            result = check_fn()
            if asyncio.iscoroutine(result):
                result = await result

            assert result is not False

            return
        except AssertionError as error:
            last_error = error
            await asyncio.sleep(interval)

    if last_error:
        raise last_error

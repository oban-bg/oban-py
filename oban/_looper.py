from __future__ import annotations

from typing import Protocol, runtime_checkable


@runtime_checkable
class Looper(Protocol):
    """Protocol for background components with a start/stop lifecycle driving a periodic loop.

    Implementations spawn an asyncio task in ``start`` that runs ``_loop`` until
    ``stop`` cancels it. Examples include Stager, Pruner, Scheduler, Refresher,
    Lifeline, Leader, Metrics, and Producer.
    """

    async def start(self) -> None:
        """Spawn the background loop task and register any listeners."""
        ...

    async def stop(self) -> None:
        """Cancel the background loop task and clean up listeners."""
        ...

    async def _loop(self) -> None:
        """Run the periodic work until cancelled."""
        ...

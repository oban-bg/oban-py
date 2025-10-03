from __future__ import annotations

import threading
import time
from typing import TYPE_CHECKING

from . import _query

if TYPE_CHECKING:
    from .oban import Oban
    from ._runner import Runner


class Stager:
    """Stages scheduled jobs and notifies runners when work is available.

    The stager runs in a dedicated thread and periodically:

    1. Stages scheduled jobs (makes them available for execution)
    2. Queries for queues that have available work
    3. Notifies the appropriate runners
    """

    def __init__(
        self,
        *,
        oban: Oban,
        runners: dict[str, Runner],
        stage_interval: float = 1.0,
        stage_limit: int = 10_000,
    ) -> None:
        self._oban = oban
        self._runners = runners
        self._stage_interval = stage_interval
        self._stage_limit = stage_limit
        self._stop_event = threading.Event()
        self._thread = threading.Thread(
            target=self._loop, name="oban-stager", daemon=True
        )

    def start(self) -> None:
        self._thread.start()

    def stop(self) -> None:
        self._stop_event.set()
        self._thread.join()

    def _loop(self) -> None:
        while not self._stop_event.is_set():
            try:
                self._stage()
            except Exception:
                if self._stop_event.is_set():
                    break

            time.sleep(self._stage_interval)

    def _stage(self) -> None:
        with self._oban.get_connection() as conn:
            _query.stage_jobs(conn, self._stage_limit)

            available = _query.check_available_queues(conn)

        for queue in available:
            if queue in self._runners:
                self._runners[queue].notify()

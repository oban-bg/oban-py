import threading
import time
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass
from functools import lru_cache
from typing import Callable, Any, Iterable, Optional

from . import _query
from .job import Job
from ._worker import resolve_worker


class Runner:
    """
    Shared thread pool that continuously polls for new jobs and executes them.
    - One polling thread fetches jobs and submits them to the pool.
    - A ThreadPoolExecutor runs jobs concurrently.
    - Graceful stop via .stop() (no new work; waits for in-flight to finish).
    """

    def __init__(
        self,
        *,
        oban,
        queue: str = "default",
        limit: int = 10,
        max_workers: Optional[int] = None,
        poll_interval: float = 0.1,
    ) -> None:
        self._oban = oban
        self._queue = queue
        self._limit = limit
        self._poll_interval = poll_interval

        self._executor = ThreadPoolExecutor(
            max_workers=max_workers, thread_name_prefix="oban-job"
        )
        self._stop_event = threading.Event()
        self._poller = threading.Thread(
            target=self._poll_loop, name="oban-poller", daemon=True
        )

    def start(self) -> None:
        self._poller.start()

    def stop(self) -> None:
        self._stop_event.set()
        self._poller.join()
        self._executor.shutdown(wait=True)

    def _poll_loop(self) -> None:
        while not self._stop_event.is_set():
            with self._oban.get_connection() as conn:
                jobs = _query.fetch_jobs(conn, queue=self._queue, demand=self._limit)

                for job in jobs:
                    fut = self._executor.submit(self._execute_job, job)
                    fut.add_done_callback(lambda fut, job=job: self._handle_done(job, fut))

                time.sleep(self._poll_interval)

    def _execute_job(self, job: Job) -> None:
        print(f"Executing job {job.id} ({job.worker}) with args={job.args}")

        worker_cls = resolve_worker(job.worker)
        worker = worker_cls()

        print(worker)

        worker.perform(job)

    def _handle_done(self, job: Job, fut) -> None:
        exc = fut.exception()

        # TODO: This should handle the result

        if exc:
            print(f"[done] job {job.id} failed: {exc!r}")
        else:
            print(f"[done] job {job.id} completed")

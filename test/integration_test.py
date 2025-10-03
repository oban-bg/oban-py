import pytest
import time
from datetime import datetime, timedelta, timezone

from oban import Oban, Cancel, Snooze


def with_backoff(check_fn, timeout=1.0, interval=0.01):
    """Retry a check function with exponential backoff until it passes or times out."""
    start = time.time()
    last_error = None

    while time.time() - start < timeout:
        try:
            check_fn()
            return
        except AssertionError as error:
            last_error = error
            time.sleep(interval)

    if last_error:
        raise last_error


class TestObanIntegration:
    @pytest.fixture(autouse=True)
    def setup(self, db_url):
        self.performed = set()
        self.db_url = db_url
        self.oban = None

    def teardown_method(self):
        if self.oban:
            self.oban.stop()

    def assert_performed(self, ref):
        assert ref in self.performed

    def assert_job_state(self, job_id: int, expected_state: str):
        with self.oban.get_connection() as conn:
            result = conn.execute(
                "SELECT state FROM oban_jobs WHERE id = %s", (job_id,)
            ).fetchone()
            actual_state = result[0] if result else None

        assert actual_state == expected_state

    def create_worker(self):
        """Create a worker class that tracks performed jobs."""
        performed = self.performed

        @self.oban.worker()
        class Worker:
            def perform(self, job):
                performed.add(job.args["ref"])

                match job.args:
                    case {"act": "er"}:
                        raise RuntimeError("this failed")
                    case {"act": "ca"}:
                        return Cancel("no reason")
                    case {"act": "sn"}:
                        return Snooze(1)
                    case _:
                        return None

        return Worker

    def test_inserting_and_executing_jobs(self):
        self.oban = Oban(
            pool={"url": self.db_url}, queues={"default": 2}, stage_interval=0.1
        ).start()

        Worker = self.create_worker()

        job_1 = Worker.enqueue({"act": "ok", "ref": 1})
        job_2 = Worker.enqueue({"act": "er", "ref": 2})
        job_3 = Worker.enqueue({"act": "ca", "ref": 3})
        job_4 = Worker.enqueue({"act": "sn", "ref": 4})
        job_5 = Worker.enqueue({"act": "er", "ref": 5}, max_attempts=1)

        with_backoff(lambda: self.assert_performed(1))
        with_backoff(lambda: self.assert_performed(2))
        with_backoff(lambda: self.assert_performed(3))
        with_backoff(lambda: self.assert_performed(4))
        with_backoff(lambda: self.assert_performed(5))

        with_backoff(lambda: self.assert_job_state(job_1.id, "completed"))
        with_backoff(lambda: self.assert_job_state(job_2.id, "retryable"))
        with_backoff(lambda: self.assert_job_state(job_3.id, "cancelled"))
        with_backoff(lambda: self.assert_job_state(job_4.id, "scheduled"))
        with_backoff(lambda: self.assert_job_state(job_5.id, "discarded"))

    def test_staging_scheduled_jobs(self):
        self.oban = Oban(
            pool={"url": self.db_url}, queues={"default": 2}, stage_interval=0.1
        ).start()

        Worker = self.create_worker()

        utc_now = datetime.now(timezone.utc)

        past_time = utc_now - timedelta(seconds=30)
        next_time = utc_now + timedelta(seconds=30)

        job_1 = Worker.enqueue({"ref": 1}, scheduled_at=past_time)
        job_2 = Worker.enqueue({"ref": 2}, scheduled_at=next_time)

        with_backoff(lambda: self.assert_performed(1))
        with_backoff(lambda: self.assert_job_state(job_1.id, "completed"))

        self.assert_job_state(job_2.id, "scheduled")

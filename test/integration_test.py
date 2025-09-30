import pytest
import time

from oban import Oban, Cancel, Snooze


class TestObanIntegration:
    @pytest.fixture(autouse=True)
    def setup(self, db_url):
        self.performed = set()
        self.db_url = db_url
        self.oban = None

    def teardown_method(self):
        if self.oban:
            self.oban.stop()

    def with_backoff(self, check_fn, timeout=1.0, interval=0.01):
        """Poll until check_fn returns True or timeout is reached."""
        start = time.time()
        while time.time() - start < timeout:
            if check_fn():
                return
            time.sleep(interval)
        pytest.fail(f"Condition not met within {timeout}s timeout")

    def assert_performed(self, ref):
        """Wait until a job with the given ref has been performed."""
        self.with_backoff(lambda: ref in self.performed)

    def test_inserting_and_executing_jobs(self):
        performed = self.performed
        self.oban = Oban(pool={"url": self.db_url}, queues={"default": 10}).start()

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

        Worker.enqueue({"act": "ok", "ref": 1})
        Worker.enqueue({"act": "er", "ref": 2})
        Worker.enqueue({"act": "ca", "ref": 3})
        Worker.enqueue({"act": "sn", "ref": 4})

        self.assert_performed(1)
        self.assert_performed(2)
        self.assert_performed(3)
        self.assert_performed(4)

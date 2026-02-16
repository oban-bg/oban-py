import asyncio

import pytest

from oban import worker
from oban._metrics import Metrics, _build_gauge, _build_sketch, _compute_bin
from oban._scheduler import clear_scheduled, register_scheduled


@worker()
class MetricsTestWorker:
    async def process(self, job):
        pass


@worker(cron="0 0 * * *")
class DailyScheduledWorker:
    async def process(self, job):
        pass


@worker(cron={"expr": "*/5 * * * *", "timezone": "America/Chicago"})
class FrequentScheduledWorker:
    async def process(self, job):
        pass


def build_metrics(interval=1.0):
    return Metrics(
        leader=None,
        name="Oban",
        node="worker.1",
        notifier=None,
        producers={},
        query=None,
        interval=interval,
    )


def get_full_counts(payload):
    return [met for met in payload["metrics"] if met["series"] == "full_count"]


class TestMetricsValidation:
    def test_valid_config_passes(self):
        build_metrics(interval=1.0)

    def test_interval_must_be_positive(self):
        with pytest.raises(ValueError, match="interval must be positive"):
            build_metrics(interval=0)

        with pytest.raises(ValueError, match="interval must be positive"):
            build_metrics(interval=-1.0)


class TestMetricsConfig:
    @pytest.mark.oban(queues={"alpha": 1})
    async def test_metrics_disabled_by_default(self, oban_instance):
        async with oban_instance() as oban:
            assert oban._metrics is None

    @pytest.mark.oban(queues={"alpha": 1}, metrics=True)
    async def test_metrics_enabled_with_true(self, oban_instance):
        async with oban_instance() as oban:
            assert oban._metrics is not None

    @pytest.mark.oban(queues={"alpha": 1}, metrics={"interval": 0.5})
    async def test_metrics_enabled_with_interval(self, oban_instance):
        async with oban_instance() as oban:
            assert oban._metrics is not None
            assert oban._metrics._interval == 0.5


class TestMetricsBroadcast:
    @pytest.mark.oban(queues={"alpha": 2, "gamma": 3}, metrics={"interval": 60})
    async def test_broadcasts_queue_checks_on_gossip(self, oban_instance):
        received = asyncio.Queue()

        def callback(channel, payload):
            received.put_nowait((channel, payload))

        async with oban_instance() as oban:
            await oban._notifier.listen("gossip", callback)

            # Trigger broadcast directly instead of waiting for loop
            await oban._metrics.broadcast()

            channel, payload = await asyncio.wait_for(received.get(), timeout=0.5)

            assert channel == "gossip"
            assert "checks" in payload

            checks = payload["checks"]
            assert len(checks) == 2

            queues = {check["queue"] for check in checks}
            assert queues == {"alpha", "gamma"}

            for check in checks:
                assert "node" in check
                assert "name" in check
                assert "queue" in check
                assert "local_limit" in check
                assert "paused" in check
                assert "running" in check
                assert "started_at" in check

            limits = {check["queue"]: check["local_limit"] for check in checks}
            assert limits == {"alpha": 2, "gamma": 3}

    @pytest.mark.oban(queues={"default": 5}, metrics={"interval": 0.01})
    async def test_broadcasts_periodically_via_loop(self, oban_instance):
        received = asyncio.Queue()

        def callback(_channel, payload):
            received.put_nowait(payload)

        async with oban_instance() as oban:
            await oban._notifier.listen("gossip", callback)

            payload = await asyncio.wait_for(received.get(), timeout=0.5)

            assert "checks" in payload
            assert len(payload["checks"]) == 1
            assert payload["checks"][0]["queue"] == "default"
            assert payload["checks"][0]["local_limit"] == 5


class TestGaugeAndSketch:
    def test_build_gauge_sums_values(self):
        gauge = _build_gauge([1, 2, 3, 4, 5])

        assert gauge == {"data": [15]}

    def test_build_gauge_single_value(self):
        gauge = _build_gauge([42])

        assert gauge == {"data": [42]}

    def test_build_sketch_creates_bins(self):
        sketch = _build_sketch([100, 200, 300])

        assert "data" in sketch
        assert "size" in sketch
        assert sketch["size"] == 3
        assert isinstance(sketch["data"], dict)
        assert len(sketch["data"]) > 0

    def test_build_sketch_clamps_negative_values(self):
        sketch = _build_sketch([-100, -200])

        assert sketch["size"] == 2

    def test_compute_bin_is_deterministic(self):
        bin1 = _compute_bin(1000)
        bin2 = _compute_bin(1000)

        assert bin1 == bin2

    def test_compute_bin_increases_with_value(self):
        bin_small = _compute_bin(100)
        bin_large = _compute_bin(10000)

        assert bin_large > bin_small


class TestJobMetricsBroadcast:
    @pytest.mark.oban(queues={"default": 1}, metrics={"interval": 60})
    async def test_broadcasts_job_metrics_after_execution(self, oban_instance):
        received = asyncio.Queue()

        def callback(_channel, payload):
            received.put_nowait(payload)

        async with oban_instance() as oban:
            await oban._notifier.listen("metrics", callback)

            # Enqueue and wait for job to complete
            await oban.enqueue(MetricsTestWorker.new({}))
            await asyncio.sleep(0.02)

            # Trigger metrics broadcast
            await oban._metrics.broadcast()

            payload = await asyncio.wait_for(received.get(), timeout=0.5)

            assert "metrics" in payload
            assert "name" in payload
            assert "node" in payload
            assert "time" in payload

            metrics = payload["metrics"]
            assert len(metrics) == 3  # exec_time, wait_time, exec_count

            series_names = {metric["series"] for metric in metrics}
            assert series_names == {"exec_time", "wait_time", "exec_count"}

            for metric in metrics:
                assert metric["state"] == "completed"
                assert metric["queue"] == "default"
                assert metric["worker"] == "test.metrics_test.MetricsTestWorker"
                assert "value" in metric

            # Verify exec_count uses Gauge format
            exec_count = next(
                metric for metric in metrics if metric["series"] == "exec_count"
            )
            assert "data" in exec_count["value"]
            assert exec_count["value"]["data"] == [1]

            # Verify exec_time uses Sketch format
            exec_time = next(
                metric for metric in metrics if metric["series"] == "exec_time"
            )
            assert "data" in exec_time["value"]
            assert "size" in exec_time["value"]
            assert exec_time["value"]["size"] == 1

    @pytest.mark.oban(metrics={"interval": 60}, leadership=False)
    async def test_no_metrics_broadcast_when_buffer_empty(self, oban_instance):
        received = asyncio.Queue()

        def callback(_channel, payload):
            received.put_nowait(payload)

        async with oban_instance() as oban:
            await oban._notifier.listen("metrics", callback)

            # Broadcast with empty buffer (and no leadership) should not send metrics
            await oban._metrics.broadcast()

            with pytest.raises(asyncio.TimeoutError):
                await asyncio.wait_for(received.get(), timeout=0.01)


class TestFullCountMetrics:
    @pytest.mark.oban(metrics=True, leadership=True)
    async def test_broadcasts_full_counts_when_leader(self, oban_instance):
        received = asyncio.Queue()

        def callback(_channel, payload):
            received.put_nowait(payload)

        async with oban_instance() as oban:
            await oban._notifier.listen("metrics", callback)

            await oban.enqueue(MetricsTestWorker.new({}))
            await asyncio.sleep(0.02)

            await oban._metrics.broadcast()

            payload = await asyncio.wait_for(received.get(), timeout=0.5)

            full_counts = get_full_counts(payload)
            assert len(full_counts) > 0

            for metric in full_counts:
                assert "state" in metric
                assert "queue" in metric
                assert "value" in metric
                assert "data" in metric["value"]

    @pytest.mark.oban(queues={"default": 1}, metrics={"interval": 60}, leadership=False)
    async def test_no_full_counts_when_not_leader(self, oban_instance):
        received = asyncio.Queue()

        def callback(_channel, payload):
            received.put_nowait(payload)

        async with oban_instance() as oban:
            await oban._notifier.listen("metrics", callback)

            await oban.enqueue(MetricsTestWorker.new({}))
            await asyncio.sleep(0.02)

            await oban._metrics.broadcast()

            payload = await asyncio.wait_for(received.get(), timeout=0.5)

            full_counts = get_full_counts(payload)
            assert len(full_counts) == 0


class TestEstimatedCounts:
    @pytest.mark.oban(metrics=True, leadership=True)
    async def test_estimate_counts_query(self, oban_instance):
        async with oban_instance() as oban:
            counts = await oban._query.estimate_counts(["available", "completed"])
            assert isinstance(counts, list)
            for state, queue, count in counts:
                assert state in ["available", "completed"]
                assert queue == "default"
                assert isinstance(count, int)

    @pytest.mark.oban(
        queues={"default": 1}, metrics={"estimate_limit": 1}, leadership=True
    )
    async def test_switches_to_estimates_when_over_limit(self, oban_instance):
        received = asyncio.Queue()

        def callback(_channel, payload):
            received.put_nowait(payload)

        async with oban_instance() as oban:
            await oban._notifier.listen("metrics", callback)

            # Insert jobs to exceed the limit (estimate_limit=1)
            await oban.enqueue(MetricsTestWorker.new({}))
            await oban.enqueue(MetricsTestWorker.new({}))
            await asyncio.sleep(0.02)

            # First broadcast uses exact counts (previous_counts is empty)
            await oban._metrics.broadcast()
            await asyncio.wait_for(received.get(), timeout=0.5)

            # Second broadcast should use estimates for states over limit
            await oban._metrics.broadcast()
            payload = await asyncio.wait_for(received.get(), timeout=0.5)

            full_counts = get_full_counts(payload)
            assert len(full_counts) > 0


class TestCronitorBroadcast:
    @pytest.fixture(autouse=True)
    def setup_scheduled_entries(self):
        clear_scheduled()
        register_scheduled("0 0 * * *", DailyScheduledWorker)
        register_scheduled(
            {"expr": "*/5 * * * *", "timezone": "America/Chicago"},
            FrequentScheduledWorker,
        )
        yield
        clear_scheduled()

    @pytest.mark.oban(metrics={"interval": 0.01, "cronitor_interval": 0.01})
    async def test_broadcasts_crontab_with_scheduled_entries(self, oban_instance):
        received = asyncio.Queue()

        def callback(channel, payload):
            received.put_nowait((channel, payload))

        async with oban_instance() as oban:
            await oban._notifier.listen("cronitor", callback)

            channel, payload = await asyncio.wait_for(received.get(), timeout=0.5)

            assert channel == "cronitor"
            assert "crontab" in payload
            assert "name" in payload
            assert "node" in payload

            crontab = payload["crontab"]
            assert isinstance(crontab, list)

            entries_by_worker = {entry[1]: entry for entry in crontab}

            daily = entries_by_worker.get("test.metrics_test.DailyScheduledWorker")
            assert daily is not None
            assert daily[0] == "0 0 * * *"
            assert "name" in daily[2]
            assert len(daily[2]["name"]) == 8

            frequent = entries_by_worker.get(
                "test.metrics_test.FrequentScheduledWorker"
            )
            assert frequent is not None
            assert frequent[0] == "*/5 * * * *"
            assert frequent[2]["timezone"] == "America/Chicago"
            assert "name" in frequent[2]
            assert len(frequent[2]["name"]) == 8

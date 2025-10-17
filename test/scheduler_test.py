import pytest
import random

from datetime import datetime, timezone
from zoneinfo import ZoneInfo

from oban import job, worker
from oban._scheduler import Expression, Scheduler, _scheduled_entries, scheduled_entries


class TestExpressionParse:
    def test_parsing_simple_expressions(self):
        assert isinstance(Expression.parse("* * * * *"), Expression)

        with pytest.raises(ValueError, match="incorrect number of fields"):
            Expression.parse("* * *")

    def test_parsing_nicknames(self):
        assert {0} == Expression.parse("@hourly").minutes
        assert {0} == Expression.parse("@daily").hours
        assert {1} == Expression.parse("@monthly").days

    def test_parsing_month_aliases(self):
        assert {1} == Expression.parse("* * * JAN *").months
        assert {6, 7} == Expression.parse("* * * JUN,JUL *").months

    def test_parsing_weekday_aliases(self):
        assert {1} == Expression.parse("* * * * MON").weekdays
        assert {2, 7} == Expression.parse("* * * * SUN,TUE").weekdays

    def test_parsing_upper_bounds(self):
        assert Expression.parse("59 23 31 12 7")

    def test_parsing_out_of_bounds(self):
        inputs = [
            "60 * * * *",
            "* 24 * * *",
            "* * 32 * *",
            "* * * 13 *",
            "* * * * 0",
        ]

        for input in inputs:
            with pytest.raises(ValueError, match="out of range"):
                Expression.parse(input)

    def test_parsing_unrecognized_expressions(self):
        inputs = [
            "*/0 * * * *",
            "ONE * * * *",
            "* * * jan *",
            "* * * * sun",
        ]

        for input in inputs:
            with pytest.raises(ValueError, match="unrecognized expression"):
                Expression.parse(input)

    def test_step_ranges_are_calculated_from_lowest_value(self):
        assert {0, 12} == Expression.parse("* 0/12 * * *").hours
        assert {1, 8, 15, 22} == Expression.parse("* 1/7 * * *").hours
        assert {1, 8} == Expression.parse("* 1-14/7 * * *").hours

    @pytest.mark.parametrize("seed", range(1, 20))
    def test_parsing_valid_expression_combinations(self, seed):
        random.seed(seed)

        def wildcard(_min_val, _max_val):
            return "*"

        def literal(min_val, max_val):
            return str(random.randint(min_val, max_val))

        def step_from_literal(min_val, max_val):
            base = random.randint(min_val, max_val)
            step = random.randint(2, max(2, (max_val - min_val) // 2))

            return f"{base}/{step}"

        def step_from_wildcard(min_val, max_val):
            step = random.randint(2, max(2, max_val - min_val))

            return f"*/{step}"

        def range_expr(min_val, max_val):
            start = random.randint(min_val, max_val - 1)
            end = random.randint(start + 1, max_val)

            return f"{start}-{end}"

        def range_with_step(min_val, max_val):
            start = random.randint(min_val, max_val - 2)
            end = random.randint(start + 2, max_val)
            step = 2

            return f"{start}-{end}/{step}"

        def list_expr(min_val, max_val):
            count = random.randint(2, min(5, max_val - min_val + 1))
            values = random.sample(range(min_val, max_val + 1), count)

            return ",".join(str(val) for val in sorted(values))

        generators = {
            "minute": (0, 59),
            "hour": (0, 23),
            "day": (1, 31),
            "month": (1, 12),
            "weekday": (1, 7),
        }

        patterns = [
            wildcard,
            literal,
            step_from_literal,
            step_from_wildcard,
            range_expr,
            range_with_step,
            list_expr,
        ]

        picks = random.sample(patterns, 5)
        parts = []

        for gen, (_field, (min_val, max_val)) in zip(picks, generators.items()):
            parts.append(gen(min_val, max_val))

        expression = " ".join(parts)

        assert isinstance(Expression.parse(expression), Expression)


class TestExpressionIsNow:
    @pytest.mark.parametrize("seed", range(1, 10))
    def test_matching_literal_values(self, seed):
        random.seed(seed)

        min = random.randint(1, 59)
        hrs = random.randint(1, 23)
        day = random.randint(2, 28)
        mon = random.randint(2, 12)

        time = datetime.now().replace(month=mon, day=day, hour=hrs, minute=min)
        expr = Expression.parse(f"{min} {hrs} {day} {mon} *")

        assert expr.is_now(time)
        assert not expr.is_now(time.replace(minute=min - 1))
        assert not expr.is_now(time.replace(hour=hrs - 1))
        assert not expr.is_now(time.replace(day=day - 1))
        assert not expr.is_now(time.replace(month=mon - 1))

    def test_matching_literal_weekdays(self):
        sunday = datetime.now().replace(year=2025, month=10, day=12)

        assert Expression.parse("* * * * SUN").is_now(sunday)


class TestScheduledRegistration:
    @pytest.fixture(autouse=True)
    def clear_scheduled_entries(self):
        _scheduled_entries.clear()
        yield
        _scheduled_entries.clear()

    def test_worker_with_cron_registers_entry(self):
        @worker(queue="cleanup", cron="0 0 * * *")
        class CleanupWorker:
            def process(self, job):
                pass

        entry = _scheduled_entries[0]

        assert entry
        assert entry.worker_cls == CleanupWorker
        assert entry.expression.input == "0 0 * * *"

    def test_job_with_cron_registers_entry(self):
        @job(queue="reports", cron="@daily")
        def daily_report():
            pass

        entry = _scheduled_entries[0]

        assert entry
        assert entry.expression.input == "@daily"

    def test_multiple_registrations(self):
        @worker(cron="0 0 * * *")
        class BusinessMan:
            def process(self, job):
                pass

        @job(cron="@hourly")
        def business():
            pass

        assert len(_scheduled_entries) == 2

    def test_scheduled_entries_returns_copy(self):
        @worker(cron="@daily")
        class DailyWorker:
            def process(self, job):
                pass

        entries = scheduled_entries()

        assert len(entries) == 1

        # Verify it's a copy, not the original list
        entries.clear()
        assert len(scheduled_entries()) == 1


class TestSchedulerEvaluate:
    @pytest.fixture(autouse=True)
    def clear_scheduled_entries(self):
        _scheduled_entries.clear()
        yield
        _scheduled_entries.clear()

    @pytest.fixture
    def mock_query(self):
        class MockQuery:
            def __init__(self):
                self.enqueued_jobs = []

            async def enqueue_many(self, jobs):
                self.enqueued_jobs.extend(jobs)

        return MockQuery()

    @pytest.fixture
    def scheduler(self, mock_query):
        return Scheduler(leader=None, query=mock_query)

    @pytest.mark.asyncio
    async def test_enqueues_jobs_for_matching_expressions(self, scheduler, mock_query):
        @worker(queue="minute", cron="* * * * *")
        class EveryMinuteWorker:
            def process(self, job):
                pass

        await scheduler._evaluate()

        job = mock_query.enqueued_jobs[0]

        assert job
        assert job.queue == "minute"
        assert job.worker.endswith("EveryMinuteWorker")

    @pytest.mark.asyncio
    async def test_does_not_enqueue_non_matching_expressions(
        self, scheduler, mock_query
    ):
        @worker(cron="0 0 1 1 *")
        class NewYearWorker:
            def process(self, job):
                pass

        await scheduler._evaluate()

        # We shouldn't be running tests at midnight on New Years Eve...
        assert len(mock_query.enqueued_jobs) == 0

    @pytest.mark.asyncio
    async def test_enqueues_multiple_matching_jobs(self, scheduler, mock_query):
        @worker(queue="first", cron="* * * * *")
        class FirstWorker:
            def process(self, job):
                pass

        @job(queue="second", cron="* * * * *")
        def second_job():
            pass

        await scheduler._evaluate()

        assert len(mock_query.enqueued_jobs) == 2

    @pytest.mark.asyncio
    async def test_injects_cron_metadata(self, scheduler, mock_query):
        @worker(queue="meta", cron="* * * * *")
        class MetaWorker:
            def process(self, job):
                pass

        await scheduler._evaluate()

        job = mock_query.enqueued_jobs[0]

        assert job.meta["cron"] is True
        assert job.meta["cron_expr"] == "* * * * *"
        assert "cron_name" in job.meta

    @pytest.mark.asyncio
    async def test_uses_configured_timezone(self, mock_query):
        chi_tz = ZoneInfo("America/Chicago")
        chi_now = datetime.now(chi_tz)
        utc_now = datetime.now(timezone.utc)

        scheduler = Scheduler(leader=None, query=mock_query, timezone="America/Chicago")

        @worker(queue="chi", cron=f"* {chi_now.hour} * * *")
        class ChiWorker:
            def process(self, job):
                pass

        @worker(queue="utc", cron=f"* {utc_now.hour} * * *")
        class UtcWorker:
            def process(self, job):
                pass

        await scheduler._evaluate()

        assert len(mock_query.enqueued_jobs) == 1
        assert mock_query.enqueued_jobs[0].queue == "chi"

    @pytest.mark.asyncio
    async def test_per_job_timezone_override(self, mock_query):
        chi_tz = ZoneInfo("America/Chicago")
        los_tz = ZoneInfo("America/Los_Angeles")
        chi_now = datetime.now(chi_tz)
        los_now = datetime.now(los_tz)

        scheduler = Scheduler(
            leader=None, query=mock_query, timezone="America/Los_Angeles"
        )

        @worker(cron={"expr": f"* {chi_now.hour} * * *", "timezone": "America/Chicago"})
        class ChiWorker:
            def process(self, job):
                pass

        @worker(cron=f"* {los_now.hour} * * *")
        class LosWorker:
            def process(self, job):
                pass

        await scheduler._evaluate()

        assert len(mock_query.enqueued_jobs) == 2


class TestSchedulerTimeToNextMinute:
    @pytest.fixture
    def cron(self):
        return Scheduler(leader=None, query=None)

    def time_to_next_minute(self, cron, *, hour=12, minute=34, second=0, microsecond=0):
        time = datetime.now(timezone.utc).replace(
            hour=hour, minute=minute, second=second, microsecond=microsecond
        )

        return cron._time_to_next_minute(time)

    def test_seconds_until_next_minute(self, cron):
        assert self.time_to_next_minute(cron, second=0) == 60.0
        assert self.time_to_next_minute(cron, second=1) == 59.0
        assert self.time_to_next_minute(cron, second=30) == 30.0
        assert self.time_to_next_minute(cron, second=59) == 1.0

    def test_at_end_of_hour(self, cron):
        assert self.time_to_next_minute(cron, minute=59, second=45) == 15.0

    def test_at_end_of_day(self, cron):
        assert self.time_to_next_minute(cron, hour=23, minute=59, second=30) == 30.0

    @pytest.mark.parametrize("second", [0, 15, 30, 45, 59])
    @pytest.mark.parametrize("micro", [0, 500000, 999999])
    def test_always_returns_positive_value_in_range(self, cron, second, micro):
        result = self.time_to_next_minute(cron, second=second, microsecond=micro)

        assert 0 < result <= 60.0

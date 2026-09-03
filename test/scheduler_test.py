import asyncio
import pytest
import random

from datetime import datetime, timedelta, timezone

from oban import job, worker
from oban._scheduler import (
    Expression,
    Scheduler,
    clear_scheduled,
    scheduled_entries,
)


class TestExpressionParse:
    def test_parsing_simple_expressions(self):
        assert isinstance(Expression.parse("* * * * *"), Expression)

        with pytest.raises(ValueError, match="incorrect number of fields"):
            Expression.parse("* * *")

    def test_parsing_nicknames(self):
        assert {0} == Expression.parse("@hourly").minutes
        assert {0} == Expression.parse("@daily").hours
        assert {1} == Expression.parse("@monthly").days
        assert {0} == Expression.parse("@weekly").weekdays

    def test_parsing_month_aliases(self):
        assert {1} == Expression.parse("* * * JAN *").months
        assert {6, 7} == Expression.parse("* * * JUN,JUL *").months

    def test_parsing_weekday_aliases(self):
        assert {1} == Expression.parse("* * * * MON").weekdays
        assert {0, 2} == Expression.parse("* * * * SUN,TUE").weekdays

    def test_parsing_upper_bounds(self):
        assert Expression.parse("59 23 31 12 7")

    def test_parsing_out_of_bounds(self):
        inputs = [
            "60 * * * *",
            "* 24 * * *",
            "* * 32 * *",
            "* * * 13 *",
            "* * * * 8",
        ]

        for input in inputs:
            with pytest.raises(ValueError, match="out of range"):
                Expression.parse(input)

    def test_parsing_sunday_as_0_or_7(self):
        assert {0} == Expression.parse("* * * * 0").weekdays
        assert {0} == Expression.parse("* * * * 7").weekdays
        assert {0, 1} == Expression.parse("* * * * 0,1").weekdays
        assert {0, 1} == Expression.parse("* * * * 7,1").weekdays

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
    def clear_scheduled(self):
        clear_scheduled()
        yield
        clear_scheduled()

    def test_worker_with_cron_registers_entry(self):
        @worker(queue="cleanup", cron="0 0 * * *")
        class CleanupWorker:
            async def process(self, job):
                pass

        entry = scheduled_entries()[0]

        assert entry
        assert entry.worker_cls == CleanupWorker
        assert entry.expression.input == "0 0 * * *"

    def test_job_with_cron_registers_entry(self):
        @job(queue="reports", cron="@daily")
        def daily_report():
            pass

        entry = scheduled_entries()[0]

        assert entry
        assert entry.expression.input == "@daily"

    def test_multiple_registrations(self):
        @worker(cron="0 0 * * *")
        class BusinessMan:
            async def process(self, job):
                pass

        @job(cron="@weekly")
        def business():
            pass

        assert len(scheduled_entries()) == 2

    def test_scheduled_entries_returns_copy(self):
        @worker(cron="@daily")
        class DailyWorker:
            async def process(self, job):
                pass

        entries = scheduled_entries()

        assert len(entries) == 1

        # Verify it's a copy, not the original list
        entries.clear()
        assert len(scheduled_entries()) == 1


TARGET = datetime(2026, 8, 26, 22, 30, tzinfo=timezone.utc)


class FakeLeader:
    def __init__(self, is_leader=True):
        self.is_leader = is_leader


class TestSchedulerEvaluate:
    @pytest.fixture(autouse=True)
    def clear_scheduled(self):
        clear_scheduled()
        yield
        clear_scheduled()

    @pytest.fixture
    def mock_query(self):
        class MockQuery:
            def __init__(self):
                self.enqueued_jobs = []

            async def insert_jobs(self, jobs):
                self.enqueued_jobs.extend(jobs)
                return jobs

        return MockQuery()

    @pytest.fixture
    def mock_notifier(self):
        class MockNotifier:
            async def notify(self, channel, payload):
                pass

        return MockNotifier()

    @pytest.fixture
    def scheduler(self, mock_query, mock_notifier):
        return Scheduler(leader=None, notifier=mock_notifier, query=mock_query)

    async def test_enqueues_jobs_for_matching_expressions(self, scheduler, mock_query):
        @worker(queue="minute", cron="* * * * *")
        class EveryMinuteWorker:
            async def process(self, job):
                pass

        await scheduler._evaluate(TARGET)

        job = mock_query.enqueued_jobs[0]

        assert job
        assert job.queue == "minute"
        assert job.worker.endswith("EveryMinuteWorker")

    async def test_does_not_enqueue_non_matching_expressions(
        self, scheduler, mock_query
    ):
        @worker(cron="0 0 1 1 *")
        class NewYearWorker:
            async def process(self, job):
                pass

        await scheduler._evaluate(TARGET)

        assert len(mock_query.enqueued_jobs) == 0

    async def test_enqueues_multiple_matching_jobs(self, scheduler, mock_query):
        @worker(queue="first", cron="* * * * *")
        class FirstWorker:
            async def process(self, job):
                pass

        @job(queue="second", cron="* * * * *")
        def second_job():
            pass

        await scheduler._evaluate(TARGET)

        assert len(mock_query.enqueued_jobs) == 2

    async def test_injects_cron_metadata(self, scheduler, mock_query):
        @worker(queue="meta", cron="* * * * *")
        class MetaWorker:
            async def process(self, job):
                pass

        await scheduler._evaluate(TARGET)

        job = mock_query.enqueued_jobs[0]

        assert job.meta["cron"] is True
        assert job.meta["cron_at"] == "2026-08-26T22:30:00+00:00"
        assert job.meta["cron_expr"] == "* * * * *"
        assert "cron_name" in job.meta

    async def test_uses_configured_timezone(self, mock_query, mock_notifier):
        scheduler = Scheduler(
            leader=None,
            notifier=mock_notifier,
            query=mock_query,
            timezone="America/Chicago",
        )

        # 22:30 UTC is 17:30 in Chicago
        @worker(queue="chi", cron="30 17 * * *")
        class ChiWorker:
            async def process(self, job):
                pass

        @worker(queue="utc", cron="30 22 * * *")
        class UtcWorker:
            async def process(self, job):
                pass

        await scheduler._evaluate(TARGET)

        assert len(mock_query.enqueued_jobs) == 1
        assert mock_query.enqueued_jobs[0].queue == "chi"

    async def test_per_job_timezone_override(self, mock_query, mock_notifier):
        scheduler = Scheduler(
            leader=None,
            notifier=mock_notifier,
            query=mock_query,
            timezone="America/Los_Angeles",
        )

        # 22:30 UTC is 17:30 in Chicago and 15:30 in Los Angeles
        @worker(cron={"expr": "30 17 * * *", "timezone": "America/Chicago"})
        class ChiWorker:
            async def process(self, job):
                pass

        @worker(cron="30 15 * * *")
        class LosWorker:
            async def process(self, job):
                pass

        await scheduler._evaluate(TARGET)

        assert len(mock_query.enqueued_jobs) == 2


class TestSchedulerNextMinute:
    @pytest.fixture
    def cron(self):
        return Scheduler(leader=None, notifier=None, query=None)

    def test_truncates_to_the_following_minute(self, cron):
        time = TARGET.replace(second=15, microsecond=250000)

        assert cron._next_minute(time) == TARGET + timedelta(minutes=1)

    def test_on_the_boundary(self, cron):
        assert cron._next_minute(TARGET) == TARGET + timedelta(minutes=1)

    def test_at_end_of_day(self, cron):
        time = TARGET.replace(hour=23, minute=59, second=30)

        assert cron._next_minute(time) == datetime(2026, 8, 27, tzinfo=timezone.utc)


class TestSchedulerSleepUntil:
    async def test_sleeping_until_the_clock_reaches_the_target(self):
        cron = Scheduler(leader=None, notifier=None, query=None)
        target = cron._now() + timedelta(milliseconds=20)

        await cron._sleep_until(target)

        assert cron._now() >= target

    async def test_resleeping_after_an_early_wake(self):
        cron = Scheduler(leader=None, notifier=None, query=None)
        clock = iter(
            [
                TARGET - timedelta(milliseconds=1),
                TARGET - timedelta(microseconds=200),
                TARGET + timedelta(microseconds=100),
            ]
        )

        cron._now = lambda: next(clock)

        await cron._sleep_until(TARGET)

        assert next(clock, None) is None


class TestSchedulerLoop:
    async def run_ticks(self, leader, wakes, on_wake=None, on_evaluate=None):
        """Drive the loop once per wake, waking with the clock at target plus the offset."""
        cron = Scheduler(leader=leader, notifier=None, query=None)
        clock = [TARGET - timedelta(seconds=30)]
        wakes = iter(wakes)
        evaluated = []

        async def sleep_until(target):
            try:
                clock[0] = target + timedelta(seconds=next(wakes))
            except StopIteration:
                raise asyncio.CancelledError

            if on_wake:
                on_wake(target)

        async def evaluate(target):
            evaluated.append(target)

            if on_evaluate:
                clock[0] = on_evaluate(target)

        cron._now = lambda: clock[0]
        cron._sleep_until = sleep_until
        cron._evaluate = evaluate

        await cron._loop()

        return evaluated

    async def test_evaluating_each_target_minute(self):
        evaluated = await self.run_ticks(FakeLeader(), wakes=[0.001, 0.002, 0.001])

        assert evaluated == [TARGET + timedelta(minutes=offset) for offset in range(3)]

    async def test_skipping_evaluation_without_leadership(self):
        evaluated = await self.run_ticks(FakeLeader(is_leader=False), wakes=[0.001])

        assert evaluated == []

    async def test_evaluating_only_the_current_target_after_gaining_leadership(self):
        leader = FakeLeader(is_leader=False)

        def gain_leadership(target):
            leader.is_leader = target > TARGET

        evaluated = await self.run_ticks(
            leader, wakes=[0.001, 0.001], on_wake=gain_leadership
        )

        assert evaluated == [TARGET + timedelta(minutes=1)]

    async def test_skipping_evaluated_targets_after_a_backward_clock_step(self):
        def step_back(target):
            if target == TARGET:
                return target - timedelta(seconds=90)

            return target

        evaluated = await self.run_ticks(
            FakeLeader(), wakes=[0.001, 0.001, 0.001, 0.001], on_evaluate=step_back
        )

        assert evaluated == [TARGET, TARGET + timedelta(minutes=1)]

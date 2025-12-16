from __future__ import annotations

import asyncio
import re
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone, tzinfo
from typing import TYPE_CHECKING
from zoneinfo import ZoneInfo

from . import telemetry
from ._worker import worker_name

if TYPE_CHECKING:
    from .job import Job
    from ._leader import Leader
    from ._notifier import Notifier
    from ._query import Query

DOW_ALIASES = {
    "MON": "1",
    "TUE": "2",
    "WED": "3",
    "THU": "4",
    "FRI": "5",
    "SAT": "6",
    "SUN": "7",
}

MON_ALIASES = {
    "JAN": "1",
    "FEB": "2",
    "MAR": "3",
    "APR": "4",
    "MAY": "5",
    "JUN": "6",
    "JUL": "7",
    "AUG": "8",
    "SEP": "9",
    "OCT": "10",
    "NOV": "11",
    "DEC": "12",
}

MIN_SET = frozenset(range(0, 60))
HRS_SET = frozenset(range(0, 24))
DAY_SET = frozenset(range(1, 32))
MON_SET = frozenset(range(1, 13))
DOW_SET = frozenset(range(1, 8))

NICKNAMES = {
    "@annually": "0 0 1 1 *",
    "@yearly": "0 0 1 1 *",
    "@monthly": "0 0 1 * *",
    "@weekly": "0 0 * * 0",
    "@midnight": "0 0 * * *",
    "@daily": "0 0 * * *",
    "@hourly": "0 * * * *",
}


@dataclass(slots=True, frozen=True)
class ScheduledEntry:
    expression: Expression
    worker_cls: type
    timezone: tzinfo | None = None


_scheduled_entries: list[ScheduledEntry] = []


def scheduled_entries() -> list[ScheduledEntry]:
    """Return a copy of all registered scheduled entries.

    Returns:
        A list of all scheduled entries registered via @worker or @job decorators
        with cron expressions.

    Example:
        >>> from oban._scheduler import scheduled_entries
        >>> entries = scheduled_entries()
        >>> for entry in entries:
        ...     print(f"{entry.expression.input} -> {entry.worker_cls.__name__}")
    """
    return _scheduled_entries.copy()


def register_scheduled(cron: str | dict, worker_cls: type) -> None:
    """Register a worker or job for periodic execution.

    Args:
        cron: Either a cron expression string (e.g., "0 0 * * *" or "@daily")
              or a dict with "expr" and optional "timezone" keys.
              The "timezone" must be a string timezone name (e.g., "America/Chicago").
        worker_cls: The worker class to execute

    Raises:
        ValueError: If the cron expression is invalid

    Examples:
        >>> register_scheduled("0 0 * * *", MyWorker)
        >>> register_scheduled({"expr": "@daily", "timezone": "America/Chicago"}, MyWorker)
    """
    if isinstance(cron, str):
        expression = cron
        tz = None
    else:
        expression = cron["expr"]
        tz_name = cron.get("timezone")
        tz = ZoneInfo(tz_name) if tz_name else None

    parsed = Expression.parse(expression)
    entry = ScheduledEntry(expression=parsed, worker_cls=worker_cls, timezone=tz)

    _scheduled_entries.append(entry)


@dataclass(slots=True, frozen=True)
class Expression:
    input: str
    minutes: set
    hours: set
    days: set
    months: set
    weekdays: set

    @staticmethod
    def _replace_aliases(expression: str, aliases: dict[str, str]) -> str:
        for name, value in aliases.items():
            expression = expression.replace(name, value)

        return expression

    @staticmethod
    def _parse_literal(part: str) -> set[int]:
        return {int(part)}

    @staticmethod
    def _parse_step(part: str, allowed: set[int]) -> set[int]:
        step = int(part.replace("*/", ""))

        return set(range(min(allowed), max(allowed) + 1, step))

    @staticmethod
    def _parse_range(part: str, allowed: set[int]) -> set[int]:
        match part.split("-"):
            case [rmin]:
                rmin = int(rmin)
                return set(range(rmin, max(allowed) + 1))
            case [rmin, rmax]:
                rmin = int(rmin)
                rmax = int(rmax)

                if rmin > rmax:
                    raise ValueError(
                        f"min of range ({rmin}) must be less than or equal to max"
                    )

                return set(range(rmin, rmax + 1))
            case _:
                raise ValueError(f"unrecognized range: {part}")

    @staticmethod
    def _parse_range_step(part: str, allowed: set[int]) -> set[int]:
        range_part, step_value = part.split("/")
        range_set = Expression._parse_range(range_part, allowed)
        step_part = f"*/{step_value}"

        return Expression._parse_step(step_part, range_set)

    @staticmethod
    def _parse_part(part: str, allowed: set[int]) -> set[int]:
        if part == "*":
            return allowed
        elif re.match(r"^\d+$", part):
            return Expression._parse_literal(part)
        elif re.match(r"^\*\/[1-9]\d?$", part):
            return Expression._parse_step(part, allowed)
        elif re.match(r"^\d+(\-\d+)?\/[1-9]\d?$", part):
            return Expression._parse_range_step(part, allowed)
        elif re.match(r"^\d+\-\d+$", part):
            return Expression._parse_range(part, allowed)
        else:
            raise ValueError(f"unrecognized expression: {part}")

    @staticmethod
    def _parse_field(field: str, allowed: set[int]) -> set[int]:
        parsed = set()

        for part in re.split(r"\s*,\s*", field):
            parsed.update(Expression._parse_part(part, allowed))

        if not parsed.issubset(allowed):
            raise ValueError(f"field {field} is out of range: {allowed}")

        return parsed

    @classmethod
    def parse(cls, expression: str) -> Expression:
        """Parse a crontab expression into an Expression object.

        Supports standard cron syntax with five fields: minute, hour, day, month,
        weekday. Each field can contain:

        - Literal values: "5"
        - Wildcards: "*"
        - Ranges: "1-5"
        - Steps: "*/10" or "1-30/5"
        - Lists: "1,2,5,10"

        Also supports nickname expressions:

        - @hourly, @daily, @midnight, @weekly, @monthly, @yearly, @annually

        Month and weekday names are case-sensitive and must be uppercase:

        - Months: JAN, FEB, MAR, APR, MAY, JUN, JUL, AUG, SEP, OCT, NOV, DEC
        - Weekdays: MON, TUE, WED, THU, FRI, SAT, SUN

        Args:
            expression: A cron expression string (e.g., "0 0 * * *" or "@daily")

        Returns:
            An Expression object with parsed minute, hour, day, month, and weekday sets

        Raises:
            ValueError: If the expression format is invalid or values are out of range

        Examples:
            >>> Expression.parse("0 0 * * *") # Daily at midnight
            >>> Expression.parse("*/15 * * * *") # Every 15 minutes
            >>> Expression.parse("0 9-17 * * MON-FRI") # 9am-5pm on weekdays
            >>> Expression.parse("@hourly") # Every hour
        """
        normalized = NICKNAMES.get(expression, expression)

        match re.split(r"\s+", normalized):
            case [min_part, hrs_part, day_part, mon_part, dow_part]:
                mon_part = cls._replace_aliases(mon_part, MON_ALIASES)
                dow_part = cls._replace_aliases(dow_part, DOW_ALIASES)

                return cls(
                    input=expression,
                    minutes=cls._parse_field(min_part, MIN_SET),
                    hours=cls._parse_field(hrs_part, HRS_SET),
                    days=cls._parse_field(day_part, DAY_SET),
                    months=cls._parse_field(mon_part, MON_SET),
                    weekdays=cls._parse_field(dow_part, DOW_SET),
                )
            case _:
                raise ValueError(f"incorrect number of fields: {expression}")

    def is_now(self, time: None | datetime = None) -> bool:
        """Check whether a cron expression matches the current date and time."""
        time = time or datetime.now(timezone.utc)

        return (
            time.isoweekday() in self.weekdays
            and time.month in self.months
            and time.day in self.days
            and time.hour in self.hours
            and time.minute in self.minutes
        )


class Scheduler:
    """Manages periodic job scheduling based on cron expressions.

    This class is managed internally by Oban and shouldn't be constructed directly.
    Instead, configure scheduling via the Oban constructor:

        >>> async with Oban(
        ...     conn=conn,
        ...     queues={"default": 10},
        ...     scheduler={"timezone": "America/Chicago"}
        ... ) as oban:
        ...     # Scheduler runs automatically in the background
    """

    def __init__(
        self,
        *,
        leader: Leader,
        notifier: Notifier,
        query: Query,
        timezone: str = "UTC",
    ) -> None:
        self._leader = leader
        self._notifier = notifier
        self._query = query
        self._timezone = ZoneInfo(timezone)

        self._loop_task = None

    async def start(self) -> None:
        self._loop_task = asyncio.create_task(self._loop(), name="oban-cron")

    async def stop(self) -> None:
        if not self._loop_task:
            return

        self._loop_task.cancel()

        try:
            await self._loop_task
        except asyncio.CancelledError:
            pass

    async def _loop(self) -> None:
        while True:
            try:
                await asyncio.sleep(self._time_to_next_minute())

                if self._leader.is_leader:
                    await self._evaluate()
            except asyncio.CancelledError:
                break

    async def _evaluate(self) -> None:
        with telemetry.span("oban.scheduler.evaluate", {}) as context:
            jobs = [
                self._build_job(entry)
                for entry in _scheduled_entries
                if self._is_now(entry)
            ]

            context.add({"enqueued_count": len(jobs)})

            if jobs:
                result = await self._query.insert_jobs(jobs)
                queues = {job.queue for job in result}

                await self._notifier.notify(
                    "insert", [{"queue": queue} for queue in queues]
                )

    def _is_now(self, entry: ScheduledEntry) -> bool:
        now = datetime.now(entry.timezone or self._timezone)

        return entry.expression.is_now(now)

    def _build_job(self, entry: ScheduledEntry) -> Job:
        work_name = worker_name(entry.worker_cls)
        cron_name = str(hash((entry.expression.input, work_name)))

        job = entry.worker_cls.new()  # type: ignore[attr-defined]

        job.meta = {
            "cron": True,
            "cron_expr": entry.expression.input,
            "cron_name": cron_name,
        }

        return job

    def _time_to_next_minute(self, time: None | datetime = None) -> float:
        time = time or datetime.now(timezone.utc)
        next_minute = (time + timedelta(minutes=1)).replace(second=0, microsecond=0)

        return (next_minute - time).total_seconds()

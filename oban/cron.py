from __future__ import annotations

import asyncio
import re
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from .job import Job
    from .leader import Leader
    from ._query import Query

_DOW_ALIASES = {
    "MON": "1",
    "TUE": "2",
    "WED": "3",
    "THU": "4",
    "FRI": "5",
    "SAT": "6",
    "SUN": "7",
}

_MON_ALIASES = {
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

_MIN_SET = frozenset(range(0, 60))
_HRS_SET = frozenset(range(0, 24))
_DAY_SET = frozenset(range(1, 32))
_MON_SET = frozenset(range(1, 13))
_DOW_SET = frozenset(range(1, 8))

_NICKNAMES = {
    "@annually": "0 0 1 1 *",
    "@yearly": "0 0 1 1 *",
    "@monthly": "0 0 1 * *",
    "@weekly": "0 0 * * 0",
    "@midnight": "0 0 * * *",
    "@daily": "0 0 * * *",
    "@hourly": "0 * * * *",
}

# TODO: Type this, and get a real structure.
_table = []


def _replace_aliases(expression: str, aliases: dict[str, str]) -> str:
    for name, value in aliases.items():
        expression = expression.replace(name, value)

    return expression


def _parse_field(field: str, allowed: set[int]) -> set[int]:
    parsed = set()

    for part in re.split(r"\s*,\s*", field):
        parsed.update(_parse_part(part, allowed))

    if not parsed.issubset(allowed):
        raise ValueError(f"field {field} is out of range: {allowed}")

    return parsed


def _parse_part(part: str, allowed: set[int]) -> set[int]:
    if part == "*":
        return allowed
    elif re.match(r"^\d+$", part):
        return _parse_literal(part)
    elif re.match(r"^\*\/[1-9]\d?$", part):
        return _parse_step(part, allowed)
    elif re.match(r"^\d+(\-\d+)?\/[1-9]\d?$", part):
        return _parse_range_step(part, allowed)
    elif re.match(r"^\d+\-\d+$", part):
        return _parse_range(part, allowed)
    else:
        raise ValueError(f"unrecognized expression: {part}")


def _parse_literal(part: str) -> set[int]:
    return {int(part)}


def _parse_step(part: str, allowed: set[int]) -> set[int]:
    step = int(part.replace("*/", ""))

    return set(range(min(allowed), max(allowed) + 1, step))


def _parse_range_step(part: str, allowed: set[int]) -> set[int]:
    range_part, step_value = part.split("/")
    range_set = _parse_range(range_part, allowed)
    step_part = f"*/{step_value}"

    return _parse_step(step_part, range_set)


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


@dataclass(slots=True, frozen=True)
class Expression:
    input: str
    minutes: set
    hours: set
    days: set
    months: set
    weekdays: set

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
        normalized = _NICKNAMES.get(expression, expression)

        match re.split(r"\s+", normalized):
            case [min_part, hrs_part, day_part, mon_part, dow_part]:
                mon_part = _replace_aliases(mon_part, _MON_ALIASES)
                dow_part = _replace_aliases(dow_part, _DOW_ALIASES)

                return cls(
                    input=expression,
                    minutes=_parse_field(min_part, _MIN_SET),
                    hours=_parse_field(hrs_part, _HRS_SET),
                    days=_parse_field(day_part, _DAY_SET),
                    months=_parse_field(mon_part, _MON_SET),
                    weekdays=_parse_field(dow_part, _DOW_SET),
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
    def __init__(
        self,
        *,
        leader: Leader,
        query: Query,
    ) -> None:
        self._leader = leader
        self._query = query

        self._loop_task = None

    async def start(self) -> None:
        self._loop_task = asyncio.create_task(self._loop(), name="oban-cron")

    async def stop(self) -> None:
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
        jobs = [self._build(*entry) for entry in _table if entry[0].is_now]

        # TODO: Actually enqueue them. We want to use `oban.enqueue_many` to get the
        # notifications.
        print(jobs)

    # TODO: Figure out what we're actually using to enqueue here
    def _build(expr, thingy, opts) -> Job:
        pass

    def _time_to_next_minute(self, time: None | datetime = None) -> float:
        time = time or datetime.now(timezone.utc)
        next_minute = (time + timedelta(minutes=1)).replace(second=0, microsecond=0)

        return (next_minute - time).total_seconds()

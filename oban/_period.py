from __future__ import annotations

import re
from datetime import timedelta
from typing import Any

# Matches Elixir's Oban.Period units, where a month is always 30 days
UNITS = {
    "second": 1,
    "minute": 60,
    "hour": 3_600,
    "day": 86_400,
    "week": 604_800,
    "month": 2_592_000,
}

PATTERN = re.compile(rf"(\d+)(?:\s*({'|'.join(UNITS)})s?)?")


def to_seconds(value: Any) -> int:
    if isinstance(value, timedelta):
        return int(value.total_seconds())

    if isinstance(value, (int, float)) and not isinstance(value, bool):
        return int(value)

    if isinstance(value, str) and (match := PATTERN.fullmatch(value.strip())):
        amount, unit = match.groups()

        return int(amount) * (UNITS[unit] if unit else 1)

    raise ValueError(
        f'duration must be seconds, a timedelta, or a period like "7 days", got {value!r}'
    )

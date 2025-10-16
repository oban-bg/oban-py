from __future__ import annotations

import re

from dataclasses import dataclass

DOW_DICT = {
    "SUN": "0",
    "MON": "1",
    "TUE": "2",
    "WED": "3",
    "THU": "4",
    "FRI": "5",
    "SAT": "6",
}

MON_DICT = {
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
DAY_SET = frozenset(range(1, 31))
MON_SET = frozenset(range(1, 12))
DOW_SET = frozenset(range(0, 7))

NICKNAMES = {
    "@annually": "0 0 1 1 *",
    "@yearly": "0 0 1 1 *",
    "@monthly": "0 0 1 * *",
    "@weekly": "0 0 * * 0",
    "@midnight": "0 0 * * *",
    "@daily": "0 0 * * *",
    "@hourly": "0 * * * *",
}


def _trans_field(input: str, mapper: dict) -> str:
    for val in mapper:
        if val in input:
            input = input.replace(val, mapper[val])

    return input


def _parse_field(field: str, all: set) -> set(int):
    parsed = set()

    for part in re.split(r"\s*,\s*", field):
        parsed.update(_parse_part(part, all))

    if not parsed.issubset(all):
        raise ValueError(f"field {field} is out of range: {all}")

    return parsed


def _parse_part(part: str, all: set) -> set(int):
    if part == "*":
        return all
    elif re.match(r"^\d+$", part):
        return _parse_literal(part)
    elif re.match(r"^\*\/[1-9]\d?$", part):
        return _parse_step(part, all)
    elif re.match(r"^\d+(\-\d+)?\/[1-9]\d?$", part):
        return _parse_range_step(part, all)
    elif re.match(r"^\d+\-\d+$", part):
        return _parse_range(part, all)
    else:
        raise ValueError(f"unrecognized expression: {part}")


def _parse_literal(part: str) -> set(int):
    return {int(part)}


def _parse_step(part: str, all: set) -> set(int):
    step = int(part.replace("*/", ""))

    return set(range(min(all), max(all) + 1, step))


def _parse_range_step(part: str, all: set) -> set(int):
    (sub_range, part) = part.split("/")

    return _parse_step(part, _parse_range(sub_range, all))


def _parse_range(part: str, all: set) -> set(int):
    match part.split("-"):
        case [rmin]:
            rmin = int(rmin)

            return set(range(rmin, max(all) + 1))
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
    def parse(cls, input: str) -> Expression:
        if input in NICKNAMES:
            input = NICKNAMES[input]

        match re.split(r"\s+", input):
            case [mip, hrp, dap, mop, wdp]:
                mop = _trans_field(mop, MON_DICT)
                wdp = _trans_field(wdp, DOW_DICT)

                return cls(
                    input=input,
                    minutes=_parse_field(mip, MIN_SET),
                    hours=_parse_field(hrp, HRS_SET),
                    days=_parse_field(dap, DAY_SET),
                    months=_parse_field(mop, MON_SET),
                    weekdays=_parse_field(wdp, DOW_SET),
                )
            case _:
                raise ValueError(f"incorrect number of fields: {input}")

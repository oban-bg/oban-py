from __future__ import annotations

from datetime import timedelta
from enum import StrEnum
from typing import TYPE_CHECKING, Any, TypedDict

import orjson

if TYPE_CHECKING:
    from .job import Job


class UniqueField(StrEnum):
    """Fields used to determine job uniqueness."""

    ARGS = "args"
    META = "meta"
    QUEUE = "queue"
    WORKER = "worker"


class UniqueGroup(StrEnum):
    """State groups to check when evaluating uniqueness."""

    ALL = "all"
    INCOMPLETE = "incomplete"
    SCHEDULED = "scheduled"
    SUCCESSFUL = "successful"


class UniqueOptions(TypedDict, total=False):
    """Options for controlling job uniqueness."""

    fields: list[UniqueField]
    group: UniqueGroup
    keys: list[str] | None
    period: int | timedelta | None


DEFAULT_UNIQUE = {
    "fields": ["queue", "worker", "args"],
    "group": "successful",
    "keys": None,
    "period": None,
}

UNIQUE_FIELDS = {elem.value for elem in UniqueField}
UNIQUE_GROUPS = {elem.value for elem in UniqueGroup}

INCOMPLETE_STATES = ["scheduled", "available", "executing", "retryable", "suspended"]

GROUP_STATES = {
    "all": [*INCOMPLETE_STATES, "completed", "cancelled", "discarded"],
    "incomplete": INCOMPLETE_STATES,
    "scheduled": ["scheduled"],
    "successful": [*INCOMPLETE_STATES, "completed"],
}


def normalize_unique(unique: Any) -> dict[str, Any] | None:
    if unique is None or unique is False:
        return None

    if unique is True:
        unique = {}

    if not isinstance(unique, dict):
        raise TypeError(f"unique must be a bool or dict, got {unique!r}")

    if unknown := set(unique) - set(DEFAULT_UNIQUE):
        raise ValueError(f"unique has unknown options: {sorted(unknown)}")

    unique = {**DEFAULT_UNIQUE, **unique}

    if isinstance(unique["period"], timedelta):
        unique["period"] = int(unique["period"].total_seconds())

    _validate_unique(unique)

    return unique


def _validate_unique(unique: dict[str, Any]) -> None:
    fields = unique["fields"]
    group = unique["group"]
    keys = unique["keys"]
    period = unique["period"]

    if not isinstance(fields, list):
        raise TypeError(f"unique fields must be a list, got {fields!r}")

    if invalid := set(fields) - UNIQUE_FIELDS:
        raise ValueError(f"unique has invalid fields: {sorted(invalid)}")

    if group not in UNIQUE_GROUPS:
        raise ValueError(
            f"unique group must be one of {sorted(UNIQUE_GROUPS)}, got {group!r}"
        )

    if keys is not None:
        if not isinstance(keys, list) or not all(isinstance(key, str) for key in keys):
            raise ValueError(f"unique keys must be a list of strings, got {keys!r}")

        if keys and not {"args", "meta"} & set(fields):
            raise ValueError("unique keys require args or meta in fields")

    if period is not None and (isinstance(period, bool) or not isinstance(period, int)):
        raise ValueError(f"unique period must be an integer, got {period!r}")


def unique_values(job: Job) -> list[tuple[str, Any]]:
    unique = job.extra["unique"]
    keys = unique["keys"]
    values = []

    for field in sorted(unique["fields"]):
        value = getattr(job, field)

        if keys and field in ("args", "meta"):
            value = {key: val for key, val in value.items() if key in keys}

        values.append((field, value))

    return values


def unique_lock_key(prefix: str, job: Job) -> str:
    unique = job.extra["unique"]
    data = [prefix, unique["group"], unique["keys"], unique_values(job)]

    return orjson.dumps(data, option=orjson.OPT_SORT_KEYS).decode()


def unique_conditions(job: Job) -> tuple[str, dict[str, Any]]:
    unique = job.extra["unique"]
    params = {"states": GROUP_STATES[unique["group"]], "period": unique["period"]}
    conditions = []

    if unique["period"] is not None:
        conditions.append(
            "scheduled_at >= timezone('UTC', now()) - make_interval(secs => %(period)s)"
        )

    for field, value in unique_values(job):
        params[field] = value

        if field in ("queue", "worker"):
            conditions.append(f"{field} = %({field})s")
        elif not value:
            conditions.append(f"{field} <@ %({field})s")
        elif unique["keys"]:
            conditions.append(f"{field} @> %({field})s")
        else:
            conditions.append(f"{field} @> %({field})s AND {field} <@ %({field})s")

    return "".join(f" AND {cond}" for cond in conditions), params

import logging
import warnings
from typing import Any

import orjson

from . import core

# Successful work is logged when it stops, so only job starts are worth logging on their own
EVENTS = [
    name
    for name in core.EVENTS
    if name == "oban.job.start" or not name.endswith(".start")
]

JOB_FIELDS = [
    "id",
    "worker",
    "queue",
    "attempt",
    "max_attempts",
    "args",
    "meta",
    "tags",
]


class _LoggerHandler:
    def __init__(self):
        self.logger = logging.getLogger("oban")

    def _handle_event(self, name: str, meta: dict[str, Any]) -> None:
        if name == "oban.queue.shutdown" and not meta["orphaned"]:
            return

        level = self._get_level(name)

        if not self.logger.isEnabledFor(level):
            return

        data = self._format_event(name, meta)
        message = orjson.dumps(data).decode()

        self.logger.log(level, message)

    def _format_event(self, name: str, meta: dict[str, Any]) -> dict[str, Any]:
        if name.startswith("oban.job."):
            return self._format_job_event(name, meta)
        elif name.startswith("oban.jobs."):
            return self._format_jobs_event(name, meta)
        elif name == "oban.queue.shutdown":
            return self._format_shutdown_event(name, meta)
        else:
            return self._format_loop_event(name, meta)

    def _format_job_event(self, name: str, meta: dict[str, Any]) -> dict[str, Any]:
        job = meta.get("job")

        data = {field: getattr(job, field) for field in JOB_FIELDS}
        data["event"] = name

        match name:
            case "oban.job.start":
                pass

            case "oban.job.stop":
                data["state"] = meta["state"]
                data["duration"] = self._to_ms(meta["duration"])
                data["queue_time"] = self._to_ms(meta["queue_time"])

            case "oban.job.exception":
                data["state"] = meta["state"]
                data["error_type"] = meta["error_type"]
                data["error_message"] = meta["error_message"]
                data["duration"] = self._to_ms(meta["duration"])
                data["queue_time"] = self._to_ms(meta["queue_time"])

        return data

    def _format_jobs_event(self, name: str, meta: dict[str, Any]) -> dict[str, Any]:
        data = {"event": name}

        if "duration" in meta:
            data["duration"] = self._to_ms(meta["duration"])

        if name.endswith(".stop"):
            data["count"] = meta["count"]
            data["job_ids"] = [job.id for job in meta["jobs"]]
        else:
            if name.endswith(".exception"):
                data["error_type"] = meta["error_type"]
                data["error_message"] = meta["error_message"]

            if "job_ids" in meta:
                data["job_ids"] = meta["job_ids"]

        return data

    def _format_shutdown_event(self, name: str, meta: dict[str, Any]) -> dict[str, Any]:
        return {
            "duration": self._to_ms(meta["duration"]),
            "event": name,
            "message": "jobs were orphaned because they didn't finish executing in the allotted time",
            "orphaned": meta["orphaned"],
            "queue": meta["queue"],
        }

    def _format_loop_event(self, name: str, meta: dict[str, Any]) -> dict[str, Any]:
        data = {key: val for key, val in meta.items() if key != "system_time"}

        if "duration" in data:
            data["duration"] = self._to_ms(data["duration"])

        data["event"] = name

        return data

    def _get_level(self, name: str) -> int:
        if name.startswith("oban.job."):
            return logging.INFO
        elif name == "oban.queue.shutdown":
            return logging.WARNING
        elif name.endswith(".exception"):
            return logging.ERROR
        else:
            return logging.DEBUG

    def _to_ms(self, value: int) -> float:
        return round(value / 1_000_000, 2)


def attach(**_opts) -> None:
    """Deprecated, and does nothing. Events are always logged to the `oban` logger.

    Configure what's shown through Python's `logging` instead, such as with
    `logging.getLogger("oban").setLevel(logging.DEBUG)`. Will be removed in v1.0.
    """
    warnings.warn(
        "oban.telemetry.logger.attach() is deprecated and does nothing, events are always "
        "logged to the 'oban' logger",
        DeprecationWarning,
        stacklevel=2,
    )


def detach() -> None:
    """Deprecated, and does nothing. Silence the `oban` logger through `logging` instead.

    Will be removed in v1.0.
    """
    warnings.warn(
        "oban.telemetry.logger.detach() is deprecated and does nothing, configure the 'oban' "
        "logger instead",
        DeprecationWarning,
        stacklevel=2,
    )


core.attach("oban-logger", EVENTS, _LoggerHandler()._handle_event)

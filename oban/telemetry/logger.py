import logging
from typing import Any, List

import orjson

from . import core

EVENTS = [
    "oban.job.start",
    "oban.job.stop",
    "oban.job.exception",
    "oban.leader.election.stop",
    "oban.leader.election.exception",
    "oban.stager.stage.stop",
    "oban.stager.stage.exception",
    "oban.lifeline.rescue.stop",
    "oban.lifeline.rescue.exception",
    "oban.pruner.prune.stop",
    "oban.pruner.prune.exception",
    "oban.refresher.refresh.stop",
    "oban.refresher.refresh.exception",
    "oban.refresher.cleanup.stop",
    "oban.refresher.cleanup.exception",
    "oban.scheduler.evaluate.stop",
    "oban.scheduler.evaluate.exception",
    "oban.producer.fetch.stop",
    "oban.producer.fetch.exception",
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
    def __init__(
        self,
        *,
        level: int = logging.DEBUG,
        logger: logging.Logger | None = None,
    ):
        self.level = level
        self.logger = logger or logging.getLogger("oban")

    def _handle_event(self, name: str, meta: dict[str, Any]) -> None:
        data = self._format_event(name, meta)
        level = self._get_level(name)
        message = orjson.dumps(data).decode()

        self.logger.log(level, message)

    def _format_event(self, name: str, meta: dict[str, Any]) -> dict[str, Any]:
        if name.startswith("oban.job"):
            return self._format_job_event(name, meta)
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

    def _format_loop_event(self, name: str, meta: dict[str, Any]) -> dict[str, Any]:
        data = {key: val for key, val in meta.items() if key != "system_time"}

        data["duration"] = self._to_ms(data["duration"])
        data["event"] = name

        return data

    def _get_level(self, name: str) -> int:
        # TODO: This is a mess.
        if name.endswith(".exception") and name != "oban.job.exception":
            return logging.ERROR
        elif name.startswith("oban.job"):
            return self.level
        else:
            return logging.DEBUG

    def _to_ms(self, value: int) -> float:
        return round(value / 1_000_000, 2)


def attach(
    *,
    level: int = logging.INFO,
    logger: logging.Logger | None = None,
    events: List[str] | None = None,
) -> None:
    """Attach a logger handler to telemetry events.

    Emits structured logs for Oban telemetry events using Python's standard
    logging module.

    Args:
        level: Logging level for .stop events (default: INFO)
        logger: Custom logger instance (default: logging.getLogger("oban"))
        events: Specific events to log (default: all job events)

    Example:
        >>> import logging
        >>>
        >>> # Configure Python logging
        >>> logging.basicConfig(level=logging.INFO)
        >>>
        >>> # Attach logger to telemetry events
        >>> oban.telemetry.logger.attach()
    """
    handler = _LoggerHandler(level=level, logger=logger)
    events = events or EVENTS

    core.attach("oban-logger", events, handler._handle_event)


def detach() -> None:
    """Detach the logger handler from telemetry events.

    Example:
        >>> oban.telemetry.logger.detach()
    """
    core.detach("oban-logger")

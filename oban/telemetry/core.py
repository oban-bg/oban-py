"""
Lightweight telemetry tooling for agnostic instrumentation.

Provides event emission and handler attachment for instrumentation,
similar to Elixir's `:telemetry` library, but tailored to Oban's needs.
"""

import logging
import time
import traceback
from collections import defaultdict
from collections.abc import Callable
from contextlib import contextmanager
from contextvars import ContextVar
from threading import RLock
from typing import Any

Handler = Callable[[str, dict[str, Any]], None]
Metadata = dict[str, Any]

logger = logging.getLogger(__name__)

# Attaching to an event that's never emitted fails silently, so integrations can validate their
# subscriptions against this list.
EVENTS = [
    "oban.job.exception",
    "oban.job.start",
    "oban.job.stop",
    "oban.jobs.cancel.exception",
    "oban.jobs.cancel.start",
    "oban.jobs.cancel.stop",
    "oban.jobs.delete.exception",
    "oban.jobs.delete.start",
    "oban.jobs.delete.stop",
    "oban.jobs.enqueue.exception",
    "oban.jobs.enqueue.start",
    "oban.jobs.enqueue.stop",
    "oban.jobs.retry.exception",
    "oban.jobs.retry.start",
    "oban.jobs.retry.stop",
    "oban.jobs.update.exception",
    "oban.jobs.update.start",
    "oban.jobs.update.stop",
    "oban.leader.election.exception",
    "oban.leader.election.start",
    "oban.leader.election.stop",
    "oban.lifeline.rescue.exception",
    "oban.lifeline.rescue.start",
    "oban.lifeline.rescue.stop",
    "oban.metrics.broadcast.exception",
    "oban.metrics.broadcast.start",
    "oban.metrics.broadcast.stop",
    "oban.producer.ack.exception",
    "oban.producer.ack.start",
    "oban.producer.ack.stop",
    "oban.producer.get.exception",
    "oban.producer.get.start",
    "oban.producer.get.stop",
    "oban.pruner.prune.exception",
    "oban.pruner.prune.start",
    "oban.pruner.prune.stop",
    "oban.queue.shutdown",
    "oban.refresher.cleanup.exception",
    "oban.refresher.cleanup.start",
    "oban.refresher.cleanup.stop",
    "oban.refresher.refresh.exception",
    "oban.refresher.refresh.start",
    "oban.refresher.refresh.stop",
    "oban.scheduler.evaluate.exception",
    "oban.scheduler.evaluate.start",
    "oban.scheduler.evaluate.stop",
    "oban.stager.stage.exception",
    "oban.stager.stage.start",
    "oban.stager.stage.stop",
]

_handlers = defaultdict(list)
_lock = RLock()

# Tasks copy the context they're created in, so everything an instance starts tags its events
_instance_name: ContextVar[str | None] = ContextVar("oban_instance_name", default=None)


class Collector:
    """Collector for adding metadata during span execution.

    Yielded by span() to allow adding additional metadata that will be
    included in the stop or exception event.
    """

    def __init__(self):
        self._metadata: dict[str, Any] = {}

    def add(self, metadata: dict[str, Any]) -> None:
        """Add metadata to be included in the stop or exception event.

        Args:
            metadata: Dictionary of metadata to merge into the event
        """
        self._metadata.update(metadata)

    def get_all(self) -> dict[str, Any]:
        return self._metadata.copy()


def attach(id: str, events: list[str], handler: Handler) -> None:
    """Attach a handler function to one or more telemetry events.

    Args:
        id: Unique identifier for this handler (used for detaching)
        events: List of event names to handle (e.g., ["oban.job.start"])
        handler: Function called with (name, metadata) for each event

    Example:
        def log_events(name, metadata):
            print(f"{name}: {metadata}")

        telemetry.attach("my-logger", ["oban.job.stop"], log_events)
    """
    with _lock:
        for name in events:
            _handlers[name].append((id, handler))


def detach(id: str) -> None:
    """Remove all handlers with the given ID.

    Args:
        id: The handler ID to remove

    Example:
        telemetry.detach("my-logger")
    """
    with _lock:
        for name in list(_handlers.keys()):
            _handlers[name] = [
                (handler_id, handler)
                for handler_id, handler in _handlers[name]
                if handler_id != id
            ]


def execute(name: str, metadata: Metadata) -> None:
    """Execute all handlers registered for an event.

    Handlers are called synchronously. Exceptions in handlers are caught
    and logged to prevent breaking instrumented code.

    Args:
        name: Name of the event (e.g., "oban.job.start")
        metadata: Event metadata (job_id, duration, etc.)

    Example:
        telemetry.execute("myapp.report.start", {"report_id": 123})
    """
    with _lock:
        handlers = [handler for (_id, handler) in _handlers.get(name, [])]

    if instance_name := _instance_name.get():
        metadata = {"name": instance_name, **metadata}

    for handler in handlers:
        try:
            handler(name, metadata.copy())
        except Exception:
            logger.exception("Error in telemetry handler for event '%s'", name)


@contextmanager
def scope(instance_name: str):
    token = _instance_name.set(instance_name)

    try:
        yield
    finally:
        _instance_name.reset(token)


@contextmanager
def span(prefix: str, start_metadata: Metadata):
    """Context manager that emits start/stop/exception events.

    Automatically measures duration and emits three possible events:
    - {prefix}.start - When entering the span
    - {prefix}.stop - When exiting normally
    - {prefix}.exception - When an exception is raised

    Args:
        prefix: Event name prefix (e.g., "oban.pruner.prune")
        start_metadata: Metadata included in all events

    Yields:
        Collector: Object for adding additional metadata during execution

    Example:
        with telemetry.span("myapp.report", {"report_id": 123}) as collector:
            rows = generate_report()
            collector.add({"row_count": len(rows)})

        # Emits:
        # - "myapp.report.start" with report_id and system_time
        # - "myapp.report.stop" with report_id, row_count, duration, and system_time
    """
    start_time = time.monotonic_ns()

    execute(f"{prefix}.start", {"system_time": time.time_ns(), **start_metadata})

    collector = Collector()

    try:
        yield collector

        end_time = time.monotonic_ns()

        execute(
            f"{prefix}.stop",
            {
                "system_time": time.time_ns(),
                "duration": end_time - start_time,
                **start_metadata,
                **collector.get_all(),
            },
        )

    except Exception as error:
        end_time = time.monotonic_ns()

        execute(
            f"{prefix}.exception",
            {
                "system_time": time.time_ns(),
                "duration": end_time - start_time,
                "error_message": str(error),
                "error_type": type(error).__name__,
                "traceback": traceback.format_exc(),
                **start_metadata,
                **collector.get_all(),
            },
        )

        raise

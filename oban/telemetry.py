"""
Lightweight telemetry tooling for agnostic instrumentation.

Provides event emission and handler attachment for instrumentation,
similar to Elixir's `:telemetry` library, but tailored to Oban's needs.
"""

import logging
import time
import traceback

from collections import defaultdict
from contextlib import contextmanager
from threading import RLock
from typing import Any, Callable, Dict, List

Handler = Callable[[str, Dict[str, Any]], None]
Metadata = Dict[str, Any]

logger = logging.getLogger(__name__)

_handlers = defaultdict(list)
_lock = RLock()


class Collector:
    """Collector for adding metadata during span execution.

    Yielded by span() to allow adding additional metadata that will be
    included in the stop or exception event.
    """

    def __init__(self):
        self._metadata: Dict[str, Any] = {}

    def add(self, metadata: Dict[str, Any]) -> None:
        """Add metadata to be included in the stop or exception event.

        Args:
            metadata: Dictionary of metadata to merge into the event
        """
        self._metadata.update(metadata)

    def get_all(self) -> Dict[str, Any]:
        return self._metadata.copy()


def attach(id: str, events: List[str], handler: Handler) -> None:
    """Attach a handler function to one or more telemetry events.

    Args:
        id: Unique identifier for this handler (used for detaching)
        events: List of event names to handle (e.g., ["oban.job.execute.start"])
        handler: Function called with (name, metadata) for each event

    Example:
        def log_events(name, metadata):
            print(f"{name}: {metadata}")

        telemetry.attach("my-logger", ["oban.job.execute.stop"], log_events)
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
        name: Name of the event (e.g., "oban.job.execute.start")
        metadata: Event metadata (job_id, duration, etc.)

    Example:
        telemetry.execute("oban.job.execute.start", {"job_id": 123, "queue": "default"})
    """
    with _lock:
        handlers = [handler for (_id, handler) in _handlers.get(name, [])]

    for handler in handlers:
        try:
            handler(name, metadata)
        except Exception as error:
            logger.exception("Telemetry handler error for event '%s': %s", name, error)


@contextmanager
def span(prefix: str, start_metadata: Metadata):
    """Context manager that emits start/stop/exception events.

    Automatically measures duration and emits three possible events:
    - {prefix}.start - When entering the span
    - {prefix}.stop - When exiting normally
    - {prefix}.exception - When an exception is raised

    Args:
        prefix: Event name prefix (e.g., "oban.job.execute")
        start_metadata: Metadata included in all events

    Yields:
        Collector: Object for adding additional metadata during execution

    Example:
        with telemetry.span("oban.job.execute", {"job_id": 123}) as collector:
            result = process_job()
            collector.add({"state": result.state})

        # Emits:
        # - "oban.job.execute.start" with job_id and monotonic_time
        # - "oban.job.execute.stop" with job_id, state, duration, and monotonic_time
    """
    start_time = time.monotonic_ns()

    execute(f"{prefix}.start", {"monotonic_time": start_time, **start_metadata})

    collector = Collector()

    try:
        yield collector

        end_time = time.monotonic_ns()

        execute(
            f"{prefix}.stop",
            {
                "monotonic_time": end_time,
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
                "monotonic_time": end_time,
                "duration": end_time - start_time,
                "error_message": str(error),
                "error_type": type(error).__name__,
                "traceback": traceback.format_exc(),
                **start_metadata,
                **collector.get_all(),
            },
        )

        raise

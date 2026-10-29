"""
Lightweight telemetry tooling for agnostic instrumentation.

Provides event emission and handler attachment for instrumentation,
similar to Elixir's `:telemetry` library, but tailored to Oban's needs.
"""

from .core import Collector, attach, detach, execute, span

# Attaching to an event that's never emitted fails silently, so integrations can validate their
# subscriptions against this list.
EVENTS = [
    "oban.job.exception",
    "oban.job.start",
    "oban.job.stop",
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

__all__ = ["EVENTS", "Collector", "attach", "detach", "execute", "span"]

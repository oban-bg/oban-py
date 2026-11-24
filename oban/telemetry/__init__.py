"""
Lightweight telemetry tooling for agnostic instrumentation.

Provides event emission and handler attachment for instrumentation,
similar to Elixir's `:telemetry` library, but tailored to Oban's needs.
"""

from . import logger as logger
from .core import EVENTS, Collector, attach, detach, execute, span

__all__ = ["EVENTS", "Collector", "attach", "detach", "execute", "span"]

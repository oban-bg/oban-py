from importlib.metadata import version

from .decorators import job, worker
from .job import Cancel, Job, Record, Snooze
from .oban import Oban
from .worker import Worker

try:
    import oban_pro  # noqa: F401  # ty: ignore[unresolved-import]
except ImportError:
    pass

__all__ = [
    "Cancel",
    "Job",
    "Oban",
    "Record",
    "Snooze",
    "Worker",
    "job",
    "worker",
]

__version__ = version("oban")

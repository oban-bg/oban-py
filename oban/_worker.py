import importlib
from typing import Any

from .job import Job
from .types import Result

_worker_registry: dict[str, type] = {}


def resolve_worker(path: str) -> type:
    """Resolve a worker class by its path.

    First checks the worker registry for dynamically defined workers,
    then falls back to importing the module.
    """
    if path in _worker_registry:
        return _worker_registry[path]

    parts = path.split(".")
    mod_name, cls_name = ".".join(parts[:-1]), parts[-1]

    # TODO: Add try/except checks
    mod = importlib.import_module(mod_name)
    cls = getattr(mod, cls_name)

    return cls


def worker(*, oban=None, **overrides):
    """
    Decorate a class to make it a viable worker.
    """

    def decorate(cls: type) -> type:
        if not hasattr(cls, "perform"):

            def perform(self, job: Job) -> Result[Any]:
                raise NotImplementedError("Worker must implement perform method")

            setattr(cls, "perform", perform)

        @classmethod
        def new(cls, args: dict[str, Any], /, **overrides) -> Job:
            cfg = {**cls._opts, **overrides}
            return Job(worker=f"{cls.__module__}.{cls.__qualname__}", args=args, **cfg)

        @classmethod
        def enqueue(cls, args: dict[str, Any], /, **overrides) -> Job:
            cfg = {**cls._opts, **overrides}
            job = Job(worker=f"{cls.__module__}.{cls.__qualname__}", args=args, **cfg)

            return oban.enqueue(job)

        setattr(cls, "_opts", overrides)
        setattr(cls, "new", new)
        setattr(cls, "enqueue", enqueue)

        # Register the worker class
        worker_path = f"{cls.__module__}.{cls.__qualname__}"
        _worker_registry[worker_path] = cls

        return cls

    return decorate

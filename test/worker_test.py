import pytest

from oban.worker import (
    WorkerResolutionError,
    register_worker,
    resolve_worker,
    worker_name,
)


class SampleWorker:
    async def process(self, job):
        return job.args


class TestResolveWorker:
    def test_resolve_from_registry(self):
        path = worker_name(SampleWorker)

        register_worker(SampleWorker)

        assert resolve_worker(path) is SampleWorker

    def test_resolve_from_module_import(self):
        path = "collections.OrderedDict"

        assert resolve_worker(path).__name__ == "OrderedDict"

    def test_resolve_nested_module(self):
        path = "oban.worker.WorkerResolutionError"

        assert resolve_worker(path) is WorkerResolutionError

    def test_nonexistent_module_raises_error(self):
        with pytest.raises(WorkerResolutionError, match="Module .* not found"):
            resolve_worker("nonexistent_module.Worker")

    def test_nonexistent_class_raises_error(self):
        with pytest.raises(WorkerResolutionError, match="Class .* not found in module"):
            resolve_worker("oban.job.NonExistentWorker")

    def test_non_class_attribute_raises_error(self):
        with pytest.raises(WorkerResolutionError, match="expected a class"):
            resolve_worker("oban.worker.worker_name")

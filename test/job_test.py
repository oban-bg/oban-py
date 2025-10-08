import pytest

from oban.job import Job


class TestJobValidation:
    def test_queue_validation(self):
        assert Job.new(worker="test.Worker", queue="default")

        with pytest.raises(ValueError, match="queue"):
            Job.new(worker="test.Worker", queue="")

        with pytest.raises(ValueError, match="queue"):
            Job.new(worker="test.Worker", queue="a" * 129)

    def test_worker_validation(self):
        assert Job.new(worker="test.Worker")

        with pytest.raises(ValueError, match="worker"):
            Job.new(worker="")

        with pytest.raises(ValueError, match="worker"):
            Job.new(worker="a" * 129)

    def test_max_attempts_validation(self):
        assert Job.new(worker="test.Worker", max_attempts=1)
        assert Job.new(worker="test.Worker", max_attempts=20)

        with pytest.raises(ValueError, match="max_attempts"):
            Job.new(worker="test.Worker", max_attempts=0)

        with pytest.raises(ValueError, match="max_attempts"):
            Job.new(worker="test.Worker", max_attempts=-1)

    def test_priority_validation(self):
        assert Job.new(worker="test.Worker", priority=0)

        with pytest.raises(ValueError, match="priority"):
            Job.new(worker="test.Worker", priority=-1)

        with pytest.raises(ValueError, match="priority"):
            Job.new(worker="test.Worker", priority=10)


class TestJobNormalization:
    def test_empty_and_whitespace_tags_are_removed(self):
        job = Job.new(worker="test.Worker", tags=["", " ", "\n"])
        assert job.tags == []

    def test_whitespace_is_trimmed(self):
        job = Job.new(worker="test.Worker", tags=[" ", "\nalpha\n"])
        assert job.tags == ["alpha"]

    def test_tags_are_lowercased_and_deduplicated(self):
        job = Job.new(worker="test.Worker", tags=["ALPHA", " alpha "])
        assert job.tags == ["alpha"]

    def test_tags_are_converted_to_strings(self):
        job = Job.new(worker="test.Worker", tags=[None, 1, 2])
        assert job.tags == ["1", "2"]

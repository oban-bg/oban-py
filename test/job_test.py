from datetime import UTC, datetime, timedelta

import pytest

from oban._recorded import decode_recorded
from oban.job import Job, Record


class TestJobValidation:
    def test_queue_validation(self):
        assert Job(worker="Worker", queue="default")

        with pytest.raises(ValueError, match="queue"):
            Job(worker="Worker", queue="")

        with pytest.raises(ValueError, match="queue"):
            Job(worker="Worker", queue="   ")

    def test_worker_validation(self):
        assert Job(worker="Worker")

        with pytest.raises(ValueError, match="worker"):
            Job(worker="")

        with pytest.raises(ValueError, match="worker"):
            Job(worker="   ")

    def test_max_attempts_validation(self):
        assert Job(worker="Worker", max_attempts=1)
        assert Job(worker="Worker", max_attempts=20)

        with pytest.raises(ValueError, match="max_attempts"):
            Job(worker="Worker", max_attempts=0)

        with pytest.raises(ValueError, match="max_attempts"):
            Job(worker="Worker", max_attempts=-1)

    def test_priority_validation(self):
        assert Job(worker="Worker", priority=0)

        with pytest.raises(ValueError, match="priority"):
            Job(worker="Worker", priority=-1)

        with pytest.raises(ValueError, match="priority"):
            Job(worker="Worker", priority=10)


class TestJobNormalization:
    def test_empty_and_whitespace_tags_are_removed(self):
        job = Job(worker="Worker", tags=["", " ", "\n"])
        assert job.tags == []

    def test_whitespace_is_trimmed(self):
        job = Job(worker="Worker", tags=[" ", "\nalpha\n"])
        assert job.tags == ["alpha"]

    def test_tags_are_lowercased_and_deduplicated(self):
        job = Job(worker="Worker", tags=["ALPHA", " alpha "])
        assert job.tags == ["alpha"]

    def test_tags_are_converted_to_strings(self):
        job = Job(worker="Worker", tags=[None, 1, 2])
        assert job.tags == ["1", "2"]


class TestScheduleIn:
    def test_schedule_in_with_timedelta(self):
        now = datetime.now(UTC)
        top = now + timedelta(minutes=5, seconds=1)
        job = Job(worker="Worker", schedule_in=timedelta(minutes=5))

        assert now < job.scheduled_at < top

    def test_schedule_in_with_seconds_as_int(self):
        now = datetime.now(UTC)
        top = now + timedelta(seconds=61)
        job = Job(worker="Worker", schedule_in=60)

        assert now < job.scheduled_at < top

    def test_schedule_in_with_seconds_as_float(self):
        now = datetime.now(UTC)
        top = now + timedelta(seconds=31)
        job = Job(worker="Worker", schedule_in=30.5)

        assert now < job.scheduled_at < top

    def test_schedule_in_overrides_scheduled_at(self):
        fixed_time = datetime.now(UTC) + timedelta(hours=2)
        now = datetime.now(UTC)
        top = now + timedelta(minutes=5, seconds=1)

        job = Job(
            worker="Worker",
            scheduled_at=fixed_time,
            schedule_in=timedelta(minutes=5),
        )

        assert now < job.scheduled_at < top


class TestRecord:
    def test_record_stores_encoded_value(self):
        record = Record({"key": "value"})

        assert record.encoded
        assert decode_recorded(record.encoded) == {"key": "value"}

    def test_record_encodes_various_types(self):
        assert decode_recorded(Record("string").encoded) == "string"
        assert decode_recorded(Record(123).encoded) == 123
        assert decode_recorded(Record([1, 2, 3]).encoded) == [1, 2, 3]
        assert decode_recorded(Record(None).encoded) is None
        assert decode_recorded(Record({"nested": {"a": 1}}).encoded) == {
            "nested": {"a": 1}
        }

    def test_record_raises_on_exceeding_limit(self):
        large_value = "x" * 100

        with pytest.raises(ValueError, match="exceeds limit"):
            Record(large_value, limit=10)

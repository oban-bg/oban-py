import pytest
from datetime import datetime, timedelta, timezone

from oban.job import Job


class TestJobValidation:
    def test_queue_validation(self):
        assert Job.new(worker="Worker", queue="default")

        with pytest.raises(ValueError, match="queue"):
            Job.new(worker="Worker", queue="")

        with pytest.raises(ValueError, match="queue"):
            Job.new(worker="Worker", queue="   ")

    def test_worker_validation(self):
        assert Job.new(worker="Worker")

        with pytest.raises(ValueError, match="worker"):
            Job.new(worker="")

        with pytest.raises(ValueError, match="worker"):
            Job.new(worker="   ")

    def test_max_attempts_validation(self):
        assert Job.new(worker="Worker", max_attempts=1)
        assert Job.new(worker="Worker", max_attempts=20)

        with pytest.raises(ValueError, match="max_attempts"):
            Job.new(worker="Worker", max_attempts=0)

        with pytest.raises(ValueError, match="max_attempts"):
            Job.new(worker="Worker", max_attempts=-1)

    def test_priority_validation(self):
        assert Job.new(worker="Worker", priority=0)

        with pytest.raises(ValueError, match="priority"):
            Job.new(worker="Worker", priority=-1)

        with pytest.raises(ValueError, match="priority"):
            Job.new(worker="Worker", priority=10)


class TestJobNormalization:
    def test_empty_and_whitespace_tags_are_removed(self):
        job = Job.new(worker="Worker", tags=["", " ", "\n"])
        assert job.tags == []

    def test_whitespace_is_trimmed(self):
        job = Job.new(worker="Worker", tags=[" ", "\nalpha\n"])
        assert job.tags == ["alpha"]

    def test_tags_are_lowercased_and_deduplicated(self):
        job = Job.new(worker="Worker", tags=["ALPHA", " alpha "])
        assert job.tags == ["alpha"]

    def test_tags_are_converted_to_strings(self):
        job = Job.new(worker="Worker", tags=[None, 1, 2])
        assert job.tags == ["1", "2"]


class TestScheduleIn:
    def test_schedule_in_with_timedelta(self):
        now = datetime.now(timezone.utc)
        top = now + timedelta(minutes=5, seconds=1)
        job = Job.new(worker="Worker", schedule_in=timedelta(minutes=5))

        assert now < job.scheduled_at < top

    def test_schedule_in_with_seconds_as_int(self):
        now = datetime.now(timezone.utc)
        top = now + timedelta(seconds=61)
        job = Job.new(worker="Worker", schedule_in=60)

        assert now < job.scheduled_at < top

    def test_schedule_in_with_seconds_as_float(self):
        now = datetime.now(timezone.utc)
        top = now + timedelta(seconds=31)
        job = Job.new(worker="Worker", schedule_in=30.5)

        assert now < job.scheduled_at < top

    def test_schedule_in_overrides_scheduled_at(self):
        fixed_time = datetime.now(timezone.utc) + timedelta(hours=2)
        now = datetime.now(timezone.utc)
        top = now + timedelta(minutes=5, seconds=1)

        job = Job.new(
            worker="Worker",
            scheduled_at=fixed_time,
            schedule_in=timedelta(minutes=5),
        )

        assert now < job.scheduled_at < top


class TestUniqueOptions:
    def test_unique_true_normalized_to_defaults(self):
        job = Job.new(worker="Worker", unique=True)

        assert job.unique == {
            "period": None,
            "fields": ["queue", "worker", "args"],
            "keys": None,
            "group": "all",
        }

    def test_unique_partial_dict_gets_defaults(self):
        job = Job.new(worker="Worker", unique={"period": 60})

        assert job.unique == {
            "period": 60,
            "fields": ["queue", "worker", "args"],
            "keys": None,
            "group": "all",
        }

    def test_unique_period_accepts_int(self):
        assert Job.new(worker="Worker", unique={"period": 300})

    def test_unique_period_accepts_none(self):
        assert Job.new(worker="Worker", unique={"period": None})

    def test_unique_fields_accepts_valid_fields(self):
        assert Job.new(worker="Worker", unique={"fields": ["worker", "queue"]})
        assert Job.new(worker="Worker", unique={"fields": ["args", "meta"]})

    def test_unique_fields_rejects_invalid_fields(self):
        with pytest.raises(ValueError, match="fields"):
            Job.new(worker="Worker", unique={"fields": ["tags"]})

        with pytest.raises(ValueError, match="fields"):
            Job.new(worker="Worker", unique={"fields": ["invalid"]})

    def test_unique_keys_accepts_list_of_strings(self):
        assert Job.new(worker="Worker", unique={"keys": ["user_id", "action"]})

    def test_unique_group_accepts_valid_groups(self):
        for group in ["all", "incomplete", "scheduled", "successful"]:
            assert Job.new(worker="Worker", unique={"group": group})

    def test_unique_group_rejects_invalid_groups(self):
        with pytest.raises(ValueError, match="group"):
            Job.new(worker="Worker", unique={"group": "invalid"})

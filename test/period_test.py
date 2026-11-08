from datetime import timedelta

import pytest

from oban._period import to_seconds


class TestToSeconds:
    @pytest.mark.parametrize(
        "value,seconds",
        [
            (90, 90),
            (90.5, 90),
            ("90", 90),
            (timedelta(minutes=5), 300),
            ("1 second", 1),
            ("5minutes", 300),
            ("2  hours", 7_200),
            ("7 days", 604_800),
            ("2 weeks", 1_209_600),
            ("1 month", 2_592_000),
        ],
    )
    def test_converting_periods_to_seconds(self, value, seconds):
        assert to_seconds(value) == seconds

    @pytest.mark.parametrize(
        "value",
        ["", "days", "7 fortnights", "-7 days", "7.5 days", "7 days ago", True, None],
    )
    def test_rejecting_invalid_periods(self, value):
        with pytest.raises(ValueError, match="period like"):
            to_seconds(value)

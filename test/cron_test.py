import pytest

from oban.cron import Expression


class TestExpression:
    def test_parsing_simple_expressions(self):
        assert isinstance(Expression.parse("* * * * *"), Expression)

        with pytest.raises(ValueError, match="incorrect number of fields"):
            Expression.parse("* * *")

    def test_parsing_nicknames(self):
        assert "0 * * * *" == Expression.parse("@hourly").input
        assert "0 0 * * *" == Expression.parse("@daily").input
        assert "0 0 1 * *" == Expression.parse("@monthly").input

    def test_parsing_month_aliases(self):
        assert {1} == Expression.parse("* * * JAN *").months
        assert {6, 7} == Expression.parse("* * * JUN,JUL *").months

    def test_parsing_weekday_aliases(self):
        assert {1} == Expression.parse("* * * * MON").weekdays
        assert {0, 2} == Expression.parse("* * * * SUN,TUE").weekdays

    def test_parsing_out_of_bounds(self):
        inputs = [
            "60 * * * *",
            "* 24 * * *",
            "* * 32 * *",
            "* * * 13 *",
            "* * * * 7",
        ]

        for input in inputs:
            with pytest.raises(ValueError, match="out of range"):
                Expression.parse(input)

    def test_parsing_unrecognized_expressions(self):
        inputs = [
            "*/0 * * * *",
            "ONE * * * *",
            "* * * jan *",
            "* * * * sun",
        ]

        for input in inputs:
            with pytest.raises(ValueError, match="unrecognized expression"):
                Expression.parse(input)

    def test_step_ranges_are_calculated_from_lowest_value(self):
        assert {0, 12} == Expression.parse("* 0/12 * * *").hours
        assert {1, 8, 15, 22} == Expression.parse("* 1/7 * * *").hours
        assert {1, 8} == Expression.parse("* 1-14/7 * * *").hours

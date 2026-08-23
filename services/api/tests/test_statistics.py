from datetime import date
from decimal import Decimal

from app.application.statistics import (
    StatisticsPeriod,
    estimated_one_rep_max,
    statistics_window,
)


def test_estimated_one_rep_max_boundaries_and_zero_load() -> None:
    assert estimated_one_rep_max(Decimal("100"), 1) == Decimal("100")
    assert estimated_one_rep_max(Decimal("100"), 2) == Decimal("106.6666666666666666666666667")
    assert estimated_one_rep_max(Decimal("90"), 12) == Decimal("126.0")
    assert estimated_one_rep_max(Decimal("90"), 13) is None
    assert estimated_one_rep_max(None, 8) is None
    assert estimated_one_rep_max(Decimal("0"), 8) == Decimal("0E-27")


def test_statistics_windows_are_inclusive() -> None:
    today = date(2026, 8, 23)
    assert statistics_window(StatisticsPeriod.FOUR_WEEKS, today).from_date == date(2026, 7, 27)
    assert statistics_window(StatisticsPeriod.TWELVE_WEEKS, today).from_date == date(2026, 6, 1)
    assert statistics_window(StatisticsPeriod.ONE_YEAR, today).from_date == date(2025, 8, 24)
    assert statistics_window(StatisticsPeriod.ALL, today).from_date is None

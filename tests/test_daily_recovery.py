"""The recovery series as pure functions (ADR 0013, M6e): hand-computed means, SDs and flags,
the window edges and the minimum day counts. Synthetic values only."""

import math
import random
from datetime import date, timedelta

import pytest

from garminagent.analytics.daily_recovery import (
    MIN_BASELINE_DAYS,
    build_series,
    recent,
    window,
)

D0 = date(2026, 1, 5)
# With 60 baseline days alternating a and b, the sample SD is (b - a)/2 x sqrt(60/59).
SD_FACTOR = math.sqrt(60 / 59)


def day(n: int) -> date:
    return D0 + timedelta(days=n)


def test_the_baseline_is_the_60_single_days_before_the_week() -> None:
    # Value = day index. Day 80's baseline is days 14..73: mean 43.5, sample SD sqrt(60 x 61 / 12)
    # (a population SD would give sqrt(60 x 59 / 12) instead). The recent mean is 77.
    values = {day(n): float(n) for n in range(81)}
    w = window(values, day(80))
    assert (w.days, w.mean) == (7, pytest.approx(77.0))
    assert w.baseline_days == 60
    assert w.baseline_mean == pytest.approx(43.5)
    assert w.baseline_sd == pytest.approx(math.sqrt(305))
    assert w.trend == "above"


def test_resting_hr_band_is_half_an_sd_either_side_of_the_baseline() -> None:
    # Baseline days 6..65 alternate 48/52: mean 50, SD 2 x 1.0084, band 50 +- 1.0084.
    values = {day(n): 48.0 if n % 2 == 0 else 52.0 for n in range(66)}
    for level, trend in ((48.9, "below"), (49.1, "within"), (50.9, "within"), (51.1, "above")):
        w = window(values | {day(66 + n): level for n in range(7)}, day(72))
        assert w.mean == pytest.approx(level)
        assert w.baseline_mean == pytest.approx(50.0)
        assert w.baseline_sd == pytest.approx(2.0 * SD_FACTOR)
        assert w.trend == trend, level


def test_hrv_is_judged_on_the_log_scale() -> None:
    # Baseline 40/60 ms: ln-mean ln(sqrt(2400)) = ln 48.99, SD ln(1.5)/2 x 1.0084 = 0.2045, so
    # the band in ms is 48.99 x exp(+-0.1022): 44.2 to 54.3.
    hrv = {day(n): 40 if n % 2 == 0 else 60 for n in range(66)}
    for recent_ms, trend in ((44, "below"), (45, "within"), (54, "within"), (55, "above")):
        row = build_series(hrv | {day(66 + n): recent_ms for n in range(7)}, {})[day(72)]
        assert row.hrv_ln_mean == pytest.approx(math.log(recent_ms))
        assert row.hrv_baseline_ln_mean == pytest.approx(math.log(math.sqrt(2400)))
        assert row.hrv_baseline_ln_sd == pytest.approx(math.log(1.5) / 2 * SD_FACTOR)
        assert row.hrv_trend == trend, recent_ms
        assert (row.rhr_days, row.rhr_mean, row.rhr_trend) == (0, None, None)


def test_a_value_on_the_band_edge_is_within() -> None:
    values = {day(n): 50.0 for n in range(73)}  # SD 0: the band is the mean itself
    assert window(values, day(72)).trend == "within"
    assert window(values | {day(72): 50.07}, day(72)).trend == "above"
    assert window(values | {day(72): 49.93}, day(72)).trend == "below"


def test_the_baseline_ends_the_day_before_the_recent_window() -> None:
    # A spike on day 59 is in the recent window up to day 65, then in the baseline from day 66
    # (days 0-59) to day 125 (days 59-118); day 126's baseline (days 60-119) no longer has it.
    values = {day(n): 50.0 for n in range(130)}
    values[day(59)] = 80.0
    assert window(values, day(65)).mean == pytest.approx(50.0 + 30 / 7)
    assert window(values, day(65)).baseline_mean == pytest.approx(50.0)
    assert window(values, day(66)).mean == pytest.approx(50.0)
    assert window(values, day(66)).baseline_mean == pytest.approx(50.0 + 30 / 60)
    assert window(values, day(125)).baseline_mean == pytest.approx(50.0 + 30 / 60)
    assert window(values, day(126)).baseline_mean == pytest.approx(50.0)


@pytest.mark.parametrize(
    ("first", "recent_days", "baseline_days", "has_mean", "has_baseline"),
    [(40, 4, 30, True, True), (40, 3, 30, False, True), (41, 4, 29, True, False), (41, 3, 29, False, False)],
)
def test_minimum_days_or_null(
    first: int, recent_days: int, baseline_days: int, has_mean: bool, has_baseline: bool
) -> None:
    # Nights from day `first` to 69 fill day 76's baseline window (days 10..69).
    values = {day(n): 48.0 + 4 * (n % 2) for n in range(first, 70)}
    values |= {day(70 + n): 50.0 for n in range(recent_days)}
    w = window(values, day(76))
    assert (w.days, w.baseline_days) == (recent_days, baseline_days)
    assert (w.mean is not None, w.baseline_mean is not None, w.baseline_sd is not None) == (
        has_mean,
        has_baseline,
        has_baseline,
    )
    assert (w.trend is not None) == (has_mean and has_baseline)


def test_a_recent_mean_needs_four_nights() -> None:
    values = {day(n): 50.0 for n in (0, 2, 4)}
    assert recent(values, day(6)) == (3, None)
    assert recent(values | {day(6): 54.0}, day(6)) == (4, pytest.approx(51.0))


def test_hrv_and_resting_hr_are_judged_separately() -> None:
    rhr = {day(n): 50 for n in range(73)}
    hrv = {day(n): 50 for n in range(66)}  # no HRV in day 72's recent window
    row = build_series(hrv, rhr)[day(72)]
    assert (row.hrv_days, row.hrv_ln_mean, row.hrv_trend) == (0, None, None)
    assert row.hrv_baseline_days == 60
    assert (row.rhr_days, row.rhr_trend) == (7, "within")


def test_the_series_covers_every_day_from_the_first_to_the_last_input() -> None:
    series = build_series({day(3): 50, day(10): 52}, {day(5): 48, day(12): 49})
    assert list(series) == [day(n) for n in range(3, 13)]
    assert series[day(7)].hrv_days == 1 and series[day(7)].rhr_days == 1
    assert all(r.hrv_trend is None and r.rhr_trend is None for r in series.values())
    assert build_series({}, {}) == {}


def test_the_first_flag_comes_on_day_36_of_full_data() -> None:
    # Day d's baseline window is days d-66..d-7, so with nights from day 0 it holds d-6 of them.
    series = build_series({}, {day(n): 50 + n % 3 for n in range(40)})
    assert series[day(35)].rhr_baseline_days == MIN_BASELINE_DAYS - 1
    assert series[day(35)].rhr_trend is None
    assert series[day(36)].rhr_baseline_days == MIN_BASELINE_DAYS
    assert series[day(36)].rhr_trend is not None


def test_zero_hrv_is_dropped() -> None:
    row = build_series({day(n): 0 if n == 65 else 50 for n in range(67)}, {})[day(66)]
    assert row.hrv_days == 6


def test_the_series_is_identical_for_any_input_order() -> None:
    rng = random.Random(7)  # noqa: S311 - reproducible synthetic nights, not a secret
    hrv = {day(n): rng.randint(30, 90) for n in range(200) if rng.random() < 0.8}
    rhr = {day(n): rng.randint(40, 60) for n in range(200) if rng.random() < 0.8}
    expected = build_series(hrv, rhr)
    assert any(r.hrv_trend for r in expected.values()) and any(r.rhr_trend for r in expected.values())
    for _ in range(3):
        h, r = list(hrv.items()), list(rhr.items())
        rng.shuffle(h)
        rng.shuffle(r)
        assert build_series(dict(h), dict(r)) == expected

"""Per-activity metric formulas against hand-computed values (ADR 0013). Synthetic samples only.

Each expected value is worked out in the comment next to it, so a reviewer can check the
arithmetic without running the code. Metric correctness is a training-safety issue.
"""

import math

import pytest

from garminagent.analytics.metrics import (
    MAX_GAP_S,
    Point,
    Second,
    Thresholds,
    compute,
    decoupling,
    efficiency,
    expand,
    hr_load,
    hr_zone_seconds,
    power_level_seconds,
    power_load,
    three_zone_split,
    weighted_power,
)

NO_THRESHOLDS = Thresholds(ftp_w=None, lthr=None, max_hr=None, resting_hr=None, zone_floors=None)
# Resting 50, max 190, LTHR 162: HR reserve 140, LTHR at reserve fraction 112/140 = 0.8.
HR_THRESHOLDS = Thresholds(
    ftp_w=250.0, lthr=162.0, max_hr=190.0, resting_hr=50.0, zone_floors=(100, 120, 140, 160, 180)
)


def steady(seconds: int, hr: int | None = None, power: int | None = None, speed: float | None = None) -> list[Point]:
    return [Point(t, hr, power, speed) for t in range(seconds)]


def from_seconds(values: list[Second]) -> list[Point]:
    return [Point(t, s.heart_rate, s.power_w, s.speed_mps) for t, s in enumerate(values)]


# --- the gap rule ---


def test_one_hertz_samples_cover_a_second_each() -> None:
    segments = expand(steady(3600, hr=120))
    assert [len(s) for s in segments] == [3600]  # 3600 samples at 1 Hz are an hour


def test_gaps_up_to_ten_seconds_carry_forward_and_longer_ones_are_pauses() -> None:
    assert MAX_GAP_S == 10
    points = [Point(0, 100, None, None), Point(10, 110, None, None), Point(21, 120, None, None)]
    segments = expand(points)
    # 0 -> 10 is a 10 s gap: sample 0 covers 10 s. 10 -> 21 is 11 s: a pause, so sample 10
    # covers its own second only. The last sample covers one second: 11 + 1 = 12 s moving.
    assert [len(s) for s in segments] == [11, 1]
    assert [s.heart_rate for s in segments[0]] == [100] * 10 + [110]
    assert segments[1][0].heart_rate == 120


def test_samples_are_ordered_before_expanding() -> None:
    segments = expand([Point(2, 3, None, None), Point(0, 1, None, None), Point(1, 2, None, None)])
    assert [s.heart_rate for s in segments[0]] == [1, 2, 3]


def test_no_samples_no_segments() -> None:
    assert expand([]) == []


# --- weighted power ---


def test_weighted_power_of_constant_power_is_that_power() -> None:
    assert weighted_power(expand(steady(60, power=200))) == pytest.approx(200.0)


def test_weighted_power_starts_at_second_thirty() -> None:
    # 30 s at 100 W then 1 s at 400 W: two windows, means 100 and (29*100 + 400)/30 = 110.
    # ((100^4 + 110^4) / 2)^(1/4) = (123,205,000)^(1/4) = 105.355...
    points = [*steady(30, power=100), Point(30, None, 400, None)]
    assert weighted_power(expand(points)) == pytest.approx(105.3555, abs=1e-4)


def test_weighted_power_of_the_adr_square_wave() -> None:
    # ADR 0013: a 100/300 W 30 s square wave has weighted power ~221.8 W. Its 30 s rolling mean is
    # a triangle wave between 100 and 300, so mean of r^4 ~ (300^5 - 100^5) / (5 * 200) and
    # WP ~ 221.80 (221.85 with whole seconds).
    points = from_seconds([Second(None, p, None) for p in ([100] * 30 + [300] * 30) * 60])
    assert weighted_power(expand(points)) == pytest.approx(221.8, abs=0.1)


def test_weighted_power_windows_cross_neither_pauses_nor_missing_power() -> None:
    paused = steady(20, power=200) + [Point(t, None, 200, None) for t in range(40, 60)]  # 21 s gap
    assert weighted_power(expand(paused)) is None  # two 20 s runs: no 30 s window
    dropout = steady(20, power=200) + [Point(20, None, None, None)] + [Point(t, None, 200, None) for t in range(21, 41)]
    assert weighted_power(expand(dropout)) is None
    assert weighted_power(expand(steady(29, power=200))) is None


# --- power load and intensity ---


def test_an_hour_at_ftp_is_100() -> None:
    assert power_load(3600, 250.0, 250.0) == pytest.approx(100.0)


def test_power_load_hand_computed() -> None:
    # 30 min at 200 W, FTP 250: I = 0.8; 1800 * 200 * 0.8 / (250 * 3600) * 100 = 32.0
    assert power_load(1800, 200.0, 250.0) == pytest.approx(32.0)


# --- HR load ---


@pytest.mark.parametrize("b", [1.92, 1.67])
def test_an_hour_at_lthr_is_100_and_half_an_hour_50(b: float) -> None:
    assert hr_load([162] * 3600, 50, 190, 162, b) == pytest.approx(100.0)
    assert hr_load([162] * 1800, 50, 190, 162, b) == pytest.approx(50.0)


@pytest.mark.parametrize(
    ("b", "expected"),
    [
        # HR 120: x = 70/140 = 0.5; LTHR x = 0.8. 100 * 0.5 e^(0.5b) / (0.8 e^(0.8b)) = 62.5 e^(-0.3b)
        (1.92, 35.1339),  # 62.5 * e^-0.576 = 62.5 * 0.562142
        (1.67, 37.8703),  # 62.5 * e^-0.501 = 62.5 * 0.605924
    ],
)
def test_hr_load_hand_computed(b: float, expected: float) -> None:
    assert hr_load([120] * 3600, 50, 190, 162, b) == pytest.approx(expected, abs=1e-4)


def test_hr_below_resting_adds_nothing_and_above_max_still_counts() -> None:
    assert hr_load([40] * 3600, 50, 190, 162, 1.92) == 0.0
    above = hr_load([200] * 3600, 50, 190, 162, 1.92)  # x = 150/140 > 1, not capped
    at_max = hr_load([190] * 3600, 50, 190, 162, 1.92)
    assert above is not None and at_max is not None and above > at_max


def test_hr_load_needs_a_reserve_and_lthr_above_resting() -> None:
    assert hr_load([150] * 60, 190, 190, 162, 1.92) is None
    assert hr_load([150] * 60, 50, 190, 50, 1.92) is None


# --- zones, levels, the 3-zone split ---


def test_hr_zone_seconds_use_floors_as_lower_bounds() -> None:
    floors = (100, 120, 140, 160, 180)
    # 90 below zone 1; 100 and 119 zone 1; 120 zone 2; 150 zone 3; 179 zone 4; 180, 200 zone 5
    assert hr_zone_seconds([90, 100, 119, 120, 150, 179, 180, 200], floors) == [1, 2, 1, 1, 1, 2]


def test_three_zone_split_folds_garmins_zones() -> None:
    # low = below zone 1 + zone 1 + zone 2; mid = zone 3; high = zones 4 and 5
    assert three_zone_split([1, 2, 1, 1, 1, 2]) == (4, 1, 3)


def test_power_levels_are_coggans_with_inclusive_upper_bounds() -> None:
    # FTP 200: 100 (50 %) and 110 (55 %) L1; 111 and 150 (75 %) L2; 180 (90 %) L3;
    # 210 (105 %) L4; 240 (120 %) L5; 300 (150 %) L6; 301 L7
    powers = [100.0, 110.0, 111.0, 150.0, 180.0, 210.0, 240.0, 300.0, 301.0]
    assert power_level_seconds(powers, 200.0) == [2, 2, 1, 1, 1, 1, 1]


# --- efficiency and decoupling ---


def test_efficiency_factor_power_and_speed() -> None:
    assert efficiency([Second(140, 200, None)] * 60, "power") == pytest.approx(200 / 140)  # 1.428571
    assert efficiency([Second(150, None, 3.0)] * 60, "speed") == pytest.approx(0.02)  # 3.0 m/s / 150 bpm


def test_efficiency_needs_half_the_seconds_paired() -> None:
    seconds = [Second(140, 200, None)] * 4 + [Second(None, 200, None)] * 6
    assert efficiency(seconds, "power") is None


def test_decoupling_hand_computed() -> None:
    # 10 min excluded, then 30 min at 200 W / 140 bpm and 30 min at 200 W / 147 bpm:
    # EF1 = 200/140, EF2 = 200/147, 1 - EF2/EF1 = 1 - 140/147 = 0.047619
    seconds = [Second(100, 100, None)] * 600 + [Second(140, 200, None)] * 1800 + [Second(147, 200, None)] * 1800
    assert decoupling(seconds, "power") == pytest.approx(1 - 140 / 147)
    assert decoupling(seconds, "power") == pytest.approx(0.047619, abs=1e-6)


def test_decoupling_needs_an_hour() -> None:
    assert decoupling([Second(140, 200, None)] * 3599, "power") is None


# --- the whole activity ---


def test_ride_with_power_and_ftp_uses_power_load() -> None:
    metrics = compute(steady(3600, hr=162, power=250), "ride", HR_THRESHOLDS, 1.92)
    assert metrics.moving_s == 3600
    assert (metrics.hr_coverage, metrics.power_coverage, metrics.speed_coverage) == (1.0, 1.0, 0.0)
    assert metrics.weighted_power_w == pytest.approx(250.0)
    assert metrics.intensity == pytest.approx(1.0)
    assert metrics.power_load == pytest.approx(100.0)
    assert metrics.hr_load == pytest.approx(100.0)
    assert (metrics.load_method, metrics.load) == ("power", metrics.power_load)
    assert metrics.power_level_s == [0, 0, 0, 3600, 0, 0, 0]  # 250/250 = 100 % -> L4
    assert metrics.hr_zone_s == [0, 0, 0, 0, 3600, 0]  # 162 bpm: zone 4 starts at 160
    assert metrics.three_zone_s == (0, 0, 3600)
    assert metrics.ef_basis == "power"
    assert metrics.efficiency_factor == pytest.approx(250 / 162)
    assert metrics.decoupling == pytest.approx(0.0)


def test_running_load_is_hr_only_even_with_power_and_ftp() -> None:
    metrics = compute(steady(3600, hr=120, power=250, speed=3.0), "run", HR_THRESHOLDS, 1.92)
    assert metrics.weighted_power_w == pytest.approx(250.0)  # still reported
    assert (metrics.intensity, metrics.power_load, metrics.power_level_s) == (None, None, None)
    assert metrics.load_method == "hr"
    assert metrics.load == pytest.approx(35.1339, abs=1e-4)  # as in test_hr_load_hand_computed


def test_hr_load_needs_half_the_moving_time_with_hr() -> None:
    points = steady(40, hr=150) + [Point(t, None, None, 3.0) for t in range(40, 100)]
    metrics = compute(points, "run", HR_THRESHOLDS, 1.92)
    assert metrics.hr_coverage == pytest.approx(0.4)
    assert (metrics.hr_load, metrics.load, metrics.load_method) == (None, None, "none")
    assert metrics.hr_zone_s == [0, 0, 0, 40, 0, 0]  # zones still count the seconds with HR
    assert metrics.efficiency_factor is None


def test_without_the_coefficient_hr_load_is_not_computed_and_flagged() -> None:
    metrics = compute(steady(600, hr=150), "run", HR_THRESHOLDS, None)
    assert (metrics.hr_load, metrics.load_method, metrics.hr_load_not_configured) == (None, "none", True)


def test_without_hr_thresholds_there_is_no_hr_load() -> None:
    metrics = compute(steady(600, hr=150), "strength", NO_THRESHOLDS, 1.92)
    assert (metrics.hr_load, metrics.load_method, metrics.hr_load_not_configured) == (None, "none", False)
    assert (metrics.hr_zone_s, metrics.three_zone_s) == (None, None)


def test_ride_without_ftp_falls_back_to_hr_load() -> None:
    thresholds = Thresholds(ftp_w=None, lthr=162.0, max_hr=190.0, resting_hr=50.0, zone_floors=None)
    metrics = compute(steady(3600, hr=162, power=200), "ride", thresholds, 1.67)
    assert metrics.weighted_power_w == pytest.approx(200.0)
    assert (metrics.load_method, metrics.load) == ("hr", pytest.approx(100.0))


def test_no_samples_no_metrics() -> None:
    metrics = compute([], "run", HR_THRESHOLDS, 1.92)
    assert (metrics.moving_s, metrics.load, metrics.load_method, metrics.hr_coverage) == (None, None, "none", None)


def test_pauses_count_toward_no_total() -> None:
    # Two 30 min halves at LTHR with a 20 min pause between: 3600 s moving, HR load 100.
    points = steady(1800, hr=162) + [Point(t, 162, None, None) for t in range(3000, 4800)]
    metrics = compute(points, "run", HR_THRESHOLDS, 1.92)
    assert metrics.moving_s == 3600
    assert metrics.hr_load == pytest.approx(100.0)
    assert math.isclose(metrics.hr_coverage or 0, 1.0)

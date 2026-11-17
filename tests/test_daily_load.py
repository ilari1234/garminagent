"""The daily load series (ADR 0013, M6c) against hand-computed values. Pure: no database."""

from datetime import date, timedelta

import pytest

from garminagent.analytics.daily_load import RELIABLE_DAYS, ActivityLoad, build_series
from garminagent.analytics.sports import SportGroup

D0 = date(2026, 3, 1)


def act(
    day: int,
    load: float | None = 100.0,
    group: SportGroup = "run",
    *,
    moving_s: int | None = 3600,
    backfilled: bool = False,
    pending: bool = False,
) -> ActivityLoad:
    return ActivityLoad(D0 + timedelta(days=day), group, load, moving_s, backfilled, pending)


def day(n: int) -> date:
    return D0 + timedelta(days=n)


def test_constant_load_gives_the_hand_computed_fitness_and_fatigue() -> None:
    # ADR 0013's check value: a constant daily load of 100 gives fitness ≈ 63.65 after 42 days.
    series = build_series([act(n) for n in range(42)])
    assert series[day(41), "all"].fitness == pytest.approx(100 * (1 - (41 / 42) ** 42))
    assert series[day(41), "all"].fitness == pytest.approx(63.65, abs=0.01)
    assert series[day(6), "all"].fatigue == pytest.approx(100 * (1 - (6 / 7) ** 7))


def test_form_is_the_previous_days_fitness_minus_fatigue() -> None:
    series = build_series([act(n) for n in range(10)])
    assert series[day(0), "all"].form == 0.0  # the day before the series is all zeros
    for n in range(1, 10):
        before = series[day(n - 1), "all"]
        assert series[day(n), "all"].form == pytest.approx(before.fitness - before.fatigue)
    assert series[day(9), "all"].form < 0  # steady training: fatigue above fitness


def test_ramp_is_the_seven_day_fitness_change_and_null_before() -> None:
    series = build_series([act(n, load=50.0 + n) for n in range(20)])
    for n in range(7):
        assert series[day(n), "all"].ramp is None
    for n in range(7, 20):
        expected = series[day(n), "all"].fitness - series[day(n - 7), "all"].fitness
        assert series[day(n), "all"].ramp == pytest.approx(expected)


def test_rest_days_have_rows_with_zero_load_and_recover() -> None:
    series = build_series([act(0), act(10)])
    assert len([k for k in series if k[1] == "all"]) == 11
    rest = series[day(5), "all"]
    assert (rest.load, rest.sessions, rest.moving_s) == (0.0, 0, 0)
    # Fatigue decays by 6/7 a day, fitness by 41/42, so form rises through the rest days.
    assert series[day(5), "all"].fatigue == pytest.approx(series[day(4), "all"].fatigue * 6 / 7)
    assert series[day(9), "all"].form > series[day(2), "all"].form


def test_the_series_runs_to_the_latest_day_with_data() -> None:
    series = build_series([act(0)], last_data_day=day(30))
    assert max(d for d, _ in series) == day(30)
    assert build_series([act(5)], last_data_day=day(2)).keys() == {(day(5), "all"), (day(5), "run")}


def test_all_is_the_total_and_only_trained_groups_get_rows_over_the_same_range() -> None:
    series = build_series([act(0, 60.0, "run"), act(0, 40.0, "ride"), act(3, 30.0, "strength")])
    assert {g for _, g in series} == {"all", "run", "ride", "strength"}
    for group in ("all", "run", "ride", "strength"):
        assert sorted(d for d, g in series if g == group) == [day(n) for n in range(4)]
    assert series[day(0), "all"].load == 100.0
    assert series[day(0), "all"].sessions == 2
    assert series[day(0), "run"].fitness == pytest.approx(60 / 42)
    assert series[day(3), "ride"].load == 0.0  # the group exists, so its rest days are rows too


def test_missing_load_counts_as_zero_and_is_counted() -> None:
    series = build_series(
        [act(0, None, moving_s=1800), act(0, None, moving_s=None, pending=True), act(0, 70.0, backfilled=True)]
    )
    row = series[day(0), "all"]
    assert (row.load, row.sessions, row.moving_s) == (70.0, 3, 5400)
    assert (row.activities_unloaded, row.activities_pending) == (1, 1)
    assert row.thresholds_backfilled


def test_fitness_is_reliable_from_126_days_of_history() -> None:
    series = build_series([act(0)], last_data_day=day(RELIABLE_DAYS))
    assert RELIABLE_DAYS == 126
    assert series[day(RELIABLE_DAYS - 2), "all"].history_days == RELIABLE_DAYS - 1
    assert not series[day(RELIABLE_DAYS - 2), "all"].fitness_reliable
    assert series[day(RELIABLE_DAYS - 1), "all"].fitness_reliable


def test_constant_load_gives_the_hand_computed_form_and_ramp() -> None:
    series = build_series([act(n) for n in range(10)])
    # Day 2's form is day 1's fitness minus fatigue: 100/42 - 100/7.
    assert series[day(1), "all"].form == pytest.approx(100 / 42 - 100 / 7)
    assert series[day(1), "all"].form == pytest.approx(-11.905, abs=0.001)
    # Day 8's ramp: fitness 100 * (1 - r^8) minus day 1's 100 * (1 - r), r = 41/42.
    assert series[day(7), "all"].ramp == pytest.approx(100 * (41 / 42 - (41 / 42) ** 8))
    assert series[day(7), "all"].ramp == pytest.approx(15.15, abs=0.01)


def test_the_series_does_not_depend_on_input_order() -> None:
    # Loads whose running float sum depends on order (0.1 + 0.2 + 0.3 != 0.3 + 0.2 + 0.1), several
    # a day in one group: the stored rows must come out identical, or unchanged days get rewritten.
    loads = [0.1, 0.2, 0.3, 1e16, 1.0, -1e16, 7.7, 33.3]
    acts = [act(n % 5, load, "run" if n % 3 else "strength") for n, load in enumerate(loads * 3)]
    assert build_series(acts) == build_series(list(reversed(acts)))
    assert build_series(acts) == build_series(sorted(acts, key=lambda a: a.load or 0.0))


def test_only_a_load_that_counts_marks_the_day_backfilled() -> None:
    series = build_series([act(0, None, backfilled=True), act(1, 50.0, backfilled=True)])
    assert not series[day(0), "all"].thresholds_backfilled
    assert series[day(1), "all"].thresholds_backfilled


def test_no_activities_no_series() -> None:
    assert build_series([], last_data_day=D0) == {}

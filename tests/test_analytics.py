"""Sport groups and the threshold timeline, without a database. Synthetic values only."""

from datetime import date, timedelta

import pytest

from garminagent.analytics.sports import GROUPS, SPORT_GROUPS, is_mapped, sport_group
from garminagent.analytics.thresholds import (
    DAILY_RESTING_MIN_DAYS,
    DAILY_RESTING_WINDOW_DAYS,
    PRECEDENCE,
    VALUE_TYPES,
    ActivityThresholds,
    DerivedCounts,
    ThresholdOutOfRange,
    ThresholdRow,
    ThresholdTimeline,
    check_range,
    derive_from_activities,
    derive_from_zones,
)
from garminagent.analytics.zones import ZoneHistory, ZoneRow

D1, D2, D3, D4 = date(2026, 1, 1), date(2026, 2, 1), date(2026, 3, 1), date(2026, 4, 1)


# --- sport groups ---


@pytest.mark.parametrize(
    ("key", "group"),
    [
        ("running", "run"),
        ("trail_running", "run"),
        ("cycling", "ride"),
        ("gravel_cycling", "ride"),
        ("road_biking", "ride"),
        ("mountain_biking", "ride"),
        ("skate_skiing_ws", "ski"),
        ("cross_country_skiing_ws", "ski"),
        ("strength_training", "strength"),
        ("walking", "other"),
        ("indoor_rowing", "other"),
        (None, "other"),
        ("", "other"),
    ],
)
def test_sport_groups_follow_adr_0013(key: str | None, group: str) -> None:
    assert sport_group(key) == group
    assert is_mapped(key) == (group != "other")


def test_every_mapped_key_lands_in_a_named_group() -> None:
    assert set(SPORT_GROUPS.values()) == set(GROUPS) - {"other"}


# --- range checks ---


@pytest.mark.parametrize(
    ("kind", "value"),
    [("lthr", 50.0), ("max_hr", 300.0), ("resting_hr", 7.5), ("ftp", 2000.0), ("threshold_speed", 36.0)],
)
def test_out_of_range_thresholds_are_refused_without_echoing_the_value(kind: str, value: float) -> None:
    with pytest.raises(ThresholdOutOfRange) as caught:
        check_range(kind, value)  # pyright: ignore[reportArgumentType]
    assert f"{value:g}" not in str(caught.value)
    assert kind in str(caught.value)


def test_in_range_thresholds_pass() -> None:
    assert check_range("lthr", 168.0) == 168.0
    assert check_range("threshold_speed", 3.6) == 3.6


# --- deriving the timeline ---


def test_zones_become_change_points_and_null_is_not_a_change() -> None:
    counts = DerivedCounts()
    rows = derive_from_zones(
        [
            ("DEFAULT", D1, 190, 48, 168),
            ("DEFAULT", D2, 190, 47, None),  # resting HR changed; LTHR missing: 168 still holds
            ("DEFAULT", D3, 191, 47, 170),
            ("RUNNING", D2, None, None, 172),
            ("SWIMMING", D2, 180, 48, 150),  # no sport group for swimming
        ],
        counts,
    )
    by_key = {(r.value_type, r.sport, r.valid_from): r.value for r in rows}
    assert by_key == {
        ("max_hr", "all", D1): 190,
        ("max_hr", "all", D3): 191,
        ("resting_hr", "all", D1): 48,
        ("resting_hr", "all", D2): 47,
        ("lthr", "all", D1): 168,
        ("lthr", "all", D3): 170,
        ("lthr", "run", D2): 172,
    }
    assert all(r.source == "garmin_zones" for r in rows)
    assert counts.zones_sports_skipped == 1


def act(
    day: date, key: str, lthr: float | None = None, speed: float | None = None, ftp: float | None = None
) -> ActivityThresholds:
    return ActivityThresholds(day, key, lthr, speed, ftp)


def test_activity_values_keep_changes_only_and_the_last_value_of_a_day() -> None:
    rows = derive_from_activities(
        [
            act(D1, "running", lthr=165),
            act(D1, "trail_running", lthr=166),  # same day, same group: the later one wins
            act(D2, "running", lthr=166),  # unchanged: no row
            act(D3, "running", lthr=168),
            act(D3, "skate_skiing_ws", lthr=160),
        ]
    )
    assert sorted((r.sport, r.valid_from, r.value) for r in rows) == [
        ("run", D1, 166),
        ("run", D3, 168),
        ("ski", D3, 160),
    ]
    assert all(r.source == "activity" and r.value_type == "lthr" for r in rows)


def test_same_day_revert_drops_the_change() -> None:
    rows = derive_from_activities(
        [act(D1, "running", lthr=165), act(D2, "running", lthr=170), act(D2, "running", lthr=165)]
    )
    assert [(r.valid_from, r.value) for r in rows] == [(D1, 165)]


def test_ftp_counts_only_from_rides_and_threshold_speed_only_from_runs() -> None:
    rows = derive_from_activities(
        [
            act(D1, "skate_skiing_ws", ftp=200, speed=5.0),  # Garmin's FTP is a cycling value
            act(D1, "gravel_cycling", ftp=250, speed=8.0),
            act(D1, "running", ftp=260, speed=3.6),
        ]
    )
    assert sorted((r.value_type, r.sport, r.value) for r in rows) == [
        ("ftp", "ride", 250),
        ("threshold_speed", "run", 3.6),
    ]


# --- resolving ---


def row(kind: str, sport: str, source: str, day: date, value: float) -> ThresholdRow:
    return ThresholdRow(kind, sport, source, day, value)  # pyright: ignore[reportArgumentType]


def test_precedence_is_manual_then_garmin_zones_then_activity() -> None:
    assert PRECEDENCE == ("manual", "garmin_zones", "activity")
    timeline = ThresholdTimeline(
        [
            row("lthr", "run", "activity", D1, 160),
            row("lthr", "all", "garmin_zones", D2, 165),
            row("lthr", "all", "manual", D3, 170),
        ]
    )
    before = timeline.at(date(2025, 12, 31), "run")["lthr"]  # before any row: the first one, filled back
    assert before is not None and (before.value, before.source, before.backfilled) == (160, "activity", True)
    activity = timeline.at(D1, "run")["lthr"]
    assert activity is not None and (activity.value, activity.source) == (160, "activity")
    zones = timeline.at(D2, "run")["lthr"]
    assert zones is not None and (zones.value, zones.source, zones.sport) == (165, "garmin_zones", "all")
    manual = timeline.at(D4, "run")["lthr"]
    assert manual is not None and (manual.value, manual.source, manual.valid_from) == (170, "manual", D3)


def test_a_value_holds_until_the_next_change() -> None:
    timeline = ThresholdTimeline([row("ftp", "ride", "manual", D1, 240), row("ftp", "ride", "manual", D3, 255)])
    values = [timeline.at(day, "ride")["ftp"] for day in (D1, D2, D3, D4)]
    assert [v.value if v else None for v in values] == [240, 240, 255, 255]


def test_group_value_beats_all_within_a_source_but_not_across_sources() -> None:
    timeline = ThresholdTimeline(
        [
            row("max_hr", "all", "garmin_zones", D1, 190),
            row("max_hr", "run", "garmin_zones", D1, 195),
            row("max_hr", "ski", "activity", D1, 185),
        ]
    )
    run, ride, ski = (timeline.at(D2, g)["max_hr"] for g in ("run", "ride", "ski"))
    assert run is not None and (run.value, run.sport) == (195, "run")
    assert ride is not None and (ride.value, ride.sport) == (190, "all")
    # Garmin's zones outrank an activity value, even when the zones are for `all`.
    assert ski is not None and (ski.value, ski.source) == (190, "garmin_zones")


def test_manual_for_all_outranks_garmin_zones_for_the_group() -> None:
    timeline = ThresholdTimeline([row("lthr", "run", "garmin_zones", D1, 172), row("lthr", "all", "manual", D1, 168)])
    resolved = timeline.at(D2, "run")["lthr"]
    assert resolved is not None and (resolved.value, resolved.source) == (168, "manual")


def test_resolving_for_all_ignores_group_rows() -> None:
    timeline = ThresholdTimeline([row("lthr", "run", "manual", D1, 172)])
    assert timeline.at(D2, "all")["lthr"] is None


def test_every_value_type_is_resolved() -> None:
    assert set(ThresholdTimeline([]).at(D1, "run")) == set(VALUE_TYPES)


def test_cli_choices_match_the_code() -> None:
    # cli.py refuses to import if they drift; importing it here proves they match.
    from garminagent import cli

    assert [c.value for c in cli.SportChoice] == ["all", *GROUPS]


# --- the owner's fallbacks (ADR 0013, 2026-11-07) ---


def test_backward_fill_uses_the_groups_earliest_row_else_alls() -> None:
    timeline = ThresholdTimeline(
        [
            row("lthr", "run", "activity", D2, 165),  # first detection on a run
            row("lthr", "run", "activity", D3, 168),
            row("lthr", "all", "garmin_zones", D3, 170),
            row("max_hr", "all", "garmin_zones", D3, 190),  # first zones fetch
        ]
    )
    on_d1 = timeline.at(D1, "run")
    assert on_d1["lthr"] is not None and (on_d1["lthr"].value, on_d1["lthr"].valid_from) == (165, D2)
    assert on_d1["lthr"].backfilled
    assert on_d1["max_hr"] is not None and (on_d1["max_hr"].value, on_d1["max_hr"].sport) == (190, "all")
    assert on_d1["max_hr"].backfilled
    ski = timeline.at(D1, "ski")["lthr"]  # no ski rows: `all`'s earliest
    assert ski is not None and (ski.value, ski.sport, ski.backfilled) == (170, "all", True)
    assert timeline.at(D1, "run")["ftp"] is None  # no row of the type at all: nothing to fill


def test_backward_fill_never_hides_a_dated_value() -> None:
    timeline = ThresholdTimeline([row("lthr", "all", "garmin_zones", D1, 170), row("lthr", "run", "manual", D3, 160)])
    on_d2 = timeline.at(D2, "run")["lthr"]
    assert on_d2 is not None and (on_d2.value, on_d2.source, on_d2.backfilled) == (170, "garmin_zones", False)


def test_backward_fill_takes_precedence_among_rows_starting_the_same_day() -> None:
    timeline = ThresholdTimeline([row("lthr", "run", "activity", D2, 165), row("lthr", "run", "manual", D2, 160)])
    filled = timeline.at(D1, "run")["lthr"]
    assert filled is not None and (filled.value, filled.source) == (160, "manual")


def resting_days(day: date, values: list[float | None]) -> dict[date, float]:
    """values[0] is the day before `day`, values[1] two days before, and so on."""
    return {day - timedelta(days=n + 1): v for n, v in enumerate(values) if v is not None}


def test_resting_hr_is_the_mean_of_the_seven_days_before() -> None:
    assert (DAILY_RESTING_WINDOW_DAYS, DAILY_RESTING_MIN_DAYS) == (7, 3)
    daily = resting_days(D4, [50, 52, None, 54, None, None, 48, 99])  # the 8th day back is outside
    daily[D4] = 30  # the activity's own day is still partial: not used
    resolved = ThresholdTimeline([], daily).at(D4, "run")["resting_hr"]
    # (50 + 52 + 54 + 48) / 4 = 51.0
    assert resolved is not None and (resolved.value, resolved.source, resolved.backfilled) == (
        51.0,
        "daily_summary",
        False,
    )
    assert resolved.valid_from == D4 - timedelta(days=1)


def test_resting_hr_needs_three_days_in_the_window() -> None:
    daily = resting_days(D4, [50, None, None, None, None, None, 52])
    assert ThresholdTimeline([], daily).at(D4, "run")["resting_hr"] is None


def test_daily_resting_hr_ranks_below_threshold_rows_and_above_the_backward_fill() -> None:
    daily = resting_days(D2, [50, 51, 52])
    with_zones = ThresholdTimeline([row("resting_hr", "all", "garmin_zones", D1, 45)], daily).at(D2, "run")
    assert with_zones["resting_hr"] is not None and with_zones["resting_hr"].source == "garmin_zones"
    later_manual = ThresholdTimeline([row("resting_hr", "all", "manual", D3, 45)], daily).at(D2, "run")
    resting = later_manual["resting_hr"]
    assert resting is not None and (resting.value, resting.source) == (51.0, "daily_summary")


# --- HR zone floors ---

FLOORS_A = (100, 120, 140, 160, 180)
FLOORS_B = (101, 121, 141, 161, 181)
FLOORS_RUN = (105, 125, 145, 165, 185)


def test_zone_floors_prefer_the_groups_sport_then_default() -> None:
    history = ZoneHistory(
        [ZoneRow("DEFAULT", D1, FLOORS_A), ZoneRow("DEFAULT", D3, FLOORS_B), ZoneRow("RUNNING", D2, FLOORS_RUN)]
    )
    assert history.at(D1, "run") == history.at(D1, "ski")
    on_d1 = history.at(D1, "run")
    assert on_d1 is not None and (on_d1.zone_sport, on_d1.floors, on_d1.backfilled) == ("DEFAULT", FLOORS_A, False)
    run = history.at(D4, "run")
    assert run is not None and (run.zone_sport, run.valid_from, run.floors) == ("RUNNING", D2, FLOORS_RUN)
    ride = history.at(D4, "ride")  # no CYCLING rows
    assert ride is not None and (ride.zone_sport, ride.floors) == ("DEFAULT", FLOORS_B)


def test_zone_floors_fill_back_and_skip_incomplete_rows() -> None:
    history = ZoneHistory([ZoneRow("DEFAULT", D2, FLOORS_A), ZoneRow("DEFAULT", D3, (100, None, 140, 160, 180))])
    early = history.at(D1, "ride")
    assert early is not None and (early.valid_from, early.backfilled) == (D2, True)
    late = history.at(D4, "ride")  # the incomplete row is not a change
    assert late is not None and (late.valid_from, late.floors) == (D2, FLOORS_A)
    assert ZoneHistory([]).at(D1, "run") is None

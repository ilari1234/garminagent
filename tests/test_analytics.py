"""Sport groups and the threshold timeline, without a database. Synthetic values only."""

from datetime import date

import pytest

from garminagent.analytics.sports import GROUPS, SPORT_GROUPS, is_mapped, sport_group
from garminagent.analytics.thresholds import (
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
    assert timeline.at(date(2025, 12, 31), "run")["lthr"] is None  # nothing yet
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

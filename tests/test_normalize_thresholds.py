"""Threshold fields on activities and the HR zones payload (M6a). Synthetic values only."""

from typing import Any

import pytest

from garminagent.normalize import THRESHOLD_RANGES, parse_activities, parse_hr_zones


def activity(**fields: Any) -> dict[str, Any]:
    return {"activityId": 1, "startTimeGMT": "2026-10-01 04:00:00", **fields}


def test_activity_thresholds_are_read_and_speed_converted_to_mps() -> None:
    (record,) = parse_activities(
        [
            activity(
                lactateThresholdBpm=165.0,
                getlactateThresholdSpeed=0.36,  # cm/ms per the spec: 3.6 m/s
                maxFtp=250.0,
                trainingStressScore=120.5,
                intensityFactor=0.82,
            )
        ]
    ).records
    assert record.lactate_threshold_hr == 165.0
    assert record.threshold_speed_mps == pytest.approx(3.6)
    assert record.ftp_w == 250.0
    assert record.garmin_tss == 120.5
    assert record.garmin_if == 0.82


def test_activity_thresholds_default_to_none() -> None:
    (record,) = parse_activities([activity()]).records
    assert (record.lactate_threshold_hr, record.threshold_speed_mps, record.ftp_w) == (None, None, None)
    assert (record.garmin_tss, record.garmin_if) == (None, None)


@pytest.mark.parametrize(
    ("field", "value", "attribute"),
    [
        ("lactateThresholdBpm", -1.0, "lactate_threshold_hr"),  # a "no data" marker
        ("lactateThresholdBpm", 40.0, "lactate_threshold_hr"),
        ("lactateThresholdBpm", 260.0, "lactate_threshold_hr"),
        ("getlactateThresholdSpeed", 3.6, "threshold_speed_mps"),  # already m/s: 36 m/s is not a threshold
        ("getlactateThresholdSpeed", 0.0, "threshold_speed_mps"),
        ("maxFtp", 0.0, "ftp_w"),
        ("maxFtp", 5000.0, "ftp_w"),
        ("trainingStressScore", -2.0, "garmin_tss"),
        ("intensityFactor", 82.0, "garmin_if"),  # a percentage, not the spec's fraction
    ],
)
def test_out_of_range_activity_thresholds_become_none(field: str, value: float, attribute: str) -> None:
    (record,) = parse_activities([activity(**{field: value})]).records
    assert getattr(record, attribute) is None


def zones_item(**fields: Any) -> dict[str, Any]:
    return {
        "sport": "DEFAULT",
        "trainingMethod": "LACTATE_THRESHOLD",
        "zone1Floor": 100,
        "zone2Floor": 120,
        "zone3Floor": 140,
        "zone4Floor": 155,
        "zone5Floor": 170,
        "maxHeartRateUsed": 190,
        "restingHeartRateUsed": 48,
        "lactateThresholdHeartRateUsed": 168,
        "restingHrAutoUpdateUsed": True,
        **fields,
    }


def test_hr_zones_per_sport() -> None:
    result = parse_hr_zones([zones_item(), zones_item(sport="RUNNING", trainingMethod="HR_RESERVE")])
    assert result.skipped == 0
    default, running = result.records
    assert default.sport == "DEFAULT"
    assert default.training_method == "LACTATE_THRESHOLD"
    assert (default.zone1_floor, default.zone5_floor) == (100, 170)
    assert (default.max_hr, default.resting_hr, default.lthr) == (190, 48, 168)
    assert running.sport == "RUNNING"
    assert running.training_method == "HR_RESERVE"


def test_hr_zones_empty_or_no_content() -> None:
    assert parse_hr_zones([]).records == []
    assert parse_hr_zones(None).records == []
    assert parse_hr_zones(None).skipped == 0


@pytest.mark.parametrize("payload", [{"sport": "DEFAULT"}, "not json we expected", 42])
def test_hr_zones_payload_of_the_wrong_shape_is_one_skip(payload: Any) -> None:
    result = parse_hr_zones(payload)
    assert result.records == []
    assert result.skipped == 1


def test_malformed_hr_zone_items_are_skipped_and_counted() -> None:
    result = parse_hr_zones(
        [
            zones_item(sport="SKIING"),  # not one of the spec's sports
            zones_item(sport="Running"),  # codes are exact
            zones_item(sport=None),
            zones_item(sport="RUNNING", zone3Floor=110),  # floors not ascending
            zones_item(sport="CYCLING", zone2Floor="fast"),
            "not an object",
            zones_item(),
            zones_item(maxHeartRateUsed=200),  # DEFAULT twice: which one is today's?
        ]
    )
    assert [r.sport for r in result.records] == ["DEFAULT"]
    assert result.skipped == 7


def test_unknown_training_method_becomes_none_and_free_text_is_dropped() -> None:
    (record,) = parse_hr_zones([zones_item(trainingMethod="Coach said so")]).records
    assert record.training_method is None
    assert record.zone1_floor == 100


def test_out_of_range_zone_values_become_none() -> None:
    (record,) = parse_hr_zones(
        [
            zones_item(
                maxHeartRateUsed=-1,
                restingHeartRateUsed=0,
                lactateThresholdHeartRateUsed=999,
                zone1Floor=None,
                zone5Floor=300,
            )
        ]
    ).records
    assert (record.max_hr, record.resting_hr, record.lthr) == (None, None, None)
    assert (record.zone1_floor, record.zone5_floor) == (None, None)
    assert record.zone2_floor == 120


def test_threshold_ranges_cover_every_value_type() -> None:
    assert set(THRESHOLD_RANGES) == {"ftp", "lthr", "max_hr", "resting_hr", "threshold_speed"}
    assert all(low < high for low, high in THRESHOLD_RANGES.values())

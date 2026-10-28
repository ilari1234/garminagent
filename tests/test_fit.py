from datetime import UTC, datetime, timedelta

import pytest
from garmin_fit_sdk import Encoder

from garminagent import fit
from garminagent.fit import FitParseError, Sample, parse_fit
from tests.garmin.fakes import make_activity_fit, make_fit

START = datetime(2026, 10, 1, 5, 0, tzinfo=UTC)


def test_parses_samples_and_laps() -> None:
    parsed = parse_fit(make_activity_fit(START, seconds=60))
    assert parsed.start_time == START
    assert [s.elapsed_s for s in parsed.samples] == list(range(60))
    first, last = parsed.samples[0], parsed.samples[-1]
    assert (first.heart_rate, first.cadence, first.power_w, first.temperature_c) == (140, 85, 220, 15)
    assert first.speed_mps == pytest.approx(3.0)
    assert first.altitude_m == pytest.approx(100.0)
    assert last.distance_m == pytest.approx(177.0)
    (lap,) = parsed.laps
    assert (lap.lap_index, lap.start_offset_s, lap.avg_hr) == (0, 0, 140)
    assert lap.elapsed_s == pytest.approx(59.0)
    assert lap.distance_m == pytest.approx(177.0)


def test_samples_never_carry_positions() -> None:
    # The file contains position_lat/position_long; nothing positional may come out.
    fields = set(Sample.__dataclass_fields__)
    assert not {f for f in fields if "lat" in f or "lon" in f or "position" in f}


def test_implausible_values_become_none() -> None:
    (sample, *_) = parse_fit(make_activity_fit(START, seconds=3, heart_rate=10)).samples
    assert sample.heart_rate is None
    assert sample.power_w == 220  # the rest of the record is kept


def test_several_records_in_one_second_keep_the_last() -> None:
    encoder = Encoder()
    encoder.on_mesg(0, {"type": "activity", "manufacturer": "development", "time_created": START})
    for hr in (120, 130):
        encoder.on_mesg(20, {"timestamp": START, "heart_rate": hr})
    encoder.on_mesg(20, {"timestamp": START + timedelta(seconds=1), "heart_rate": 140})
    samples = parse_fit(encoder.close()).samples
    assert [(s.elapsed_s, s.heart_rate) for s in samples] == [(0, 130), (1, 140)]


def test_corrupted_file_is_rejected_before_reading() -> None:
    data = bytearray(make_activity_fit(START, seconds=10))
    data[40] ^= 0xFF
    with pytest.raises(FitParseError) as excinfo:
        parse_fit(bytes(data))
    assert excinfo.value.reason == "integrity"


@pytest.mark.parametrize("data", [b"", b"<html>not fit</html>", b"x" * 64], ids=["empty", "html", "garbage"])
def test_non_fit_input_is_rejected(data: bytes) -> None:
    with pytest.raises(FitParseError) as excinfo:
        parse_fit(data)
    assert excinfo.value.reason == "not_fit"


def test_header_only_file_is_rejected() -> None:
    with pytest.raises(FitParseError) as excinfo:
        parse_fit(make_fit())  # valid signature, no real records
    assert excinfo.value.reason in {"integrity", "decode", "not_fit"}


def test_file_without_records_is_rejected() -> None:
    encoder = Encoder()
    encoder.on_mesg(0, {"type": "activity", "manufacturer": "development", "time_created": START})
    with pytest.raises(FitParseError) as excinfo:
        parse_fit(encoder.close())
    assert excinfo.value.reason == "no_records"


def test_size_and_record_caps(monkeypatch: pytest.MonkeyPatch) -> None:
    data = make_activity_fit(START, seconds=20)
    monkeypatch.setattr(fit, "MAX_RECORDS", 10)
    with pytest.raises(FitParseError) as excinfo:
        parse_fit(data)
    assert excinfo.value.reason == "too_many_records"
    monkeypatch.setattr(fit, "MAX_FIT_BYTES", 100)
    with pytest.raises(FitParseError) as excinfo:
        parse_fit(data)
    assert excinfo.value.reason == "too_large"


def test_error_reasons_are_fixed_codes() -> None:
    assert str(FitParseError("integrity")) == "FIT file rejected: integrity"
    assert set(FitParseError.REASONS) >= {"too_large", "not_fit", "integrity", "decode", "no_records"}


@pytest.mark.parametrize("method", ["is_fit", "check_integrity", "read"])
def test_any_sdk_exception_becomes_a_decode_error(monkeypatch: pytest.MonkeyPatch, method: str) -> None:
    def explode(*args: object, **kwargs: object) -> None:
        raise IndexError("hostile input")

    monkeypatch.setattr(fit.Decoder, method, explode)
    with pytest.raises(FitParseError) as excinfo:
        parse_fit(make_activity_fit(START, seconds=5))
    assert excinfo.value.reason == "decode"

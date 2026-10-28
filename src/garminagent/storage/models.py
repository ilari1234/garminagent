"""Database schema.

Two layers:
- raw: every Garmin response verbatim (`raw_payload`) and every downloaded activity file
  (`activity_file`, bytes in the blob store), so data can be re-parsed without re-fetching.
- normalized: typed rows for analysis (`activity`, `daily_summary`). Deliberately no GPS
  coordinates and no free text (activity names, descriptions): those stay in the raw layer and
  never feed analytics or agents.

Every data table carries `athlete_id`; the app is single-user, but the schema isn't.
"""

from datetime import date, datetime
from typing import Any

from sqlalchemy import (
    BigInteger,
    CheckConstraint,
    Date,
    DateTime,
    Float,
    ForeignKey,
    ForeignKeyConstraint,
    Index,
    Integer,
    MetaData,
    SmallInteger,
    String,
    UniqueConstraint,
    func,
)
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column

NAMING_CONVENTION = {
    "ix": "ix_%(column_0_label)s",
    "uq": "uq_%(table_name)s_%(column_0_name)s",
    "ck": "ck_%(table_name)s_%(constraint_name)s",
    "fk": "fk_%(table_name)s_%(column_0_name)s_%(referred_table_name)s",
    "pk": "pk_%(table_name)s",
}


class Base(DeclarativeBase):
    metadata = MetaData(naming_convention=NAMING_CONVENTION)


def _now() -> Any:
    return func.now()


class Athlete(Base):
    __tablename__ = "athlete"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=_now())


class RawPayload(Base):
    """One distinct response per (endpoint, request). A refetch returning the same content only
    bumps `last_fetched_at`; changed content (Garmin revises recent days) adds a row."""

    __tablename__ = "raw_payload"

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True)
    athlete_id: Mapped[int] = mapped_column(ForeignKey("athlete.id", ondelete="CASCADE"))
    endpoint: Mapped[str] = mapped_column(String(64))
    request_key: Mapped[str] = mapped_column(String(256))
    status_code: Mapped[int] = mapped_column(SmallInteger)
    payload: Mapped[Any | None] = mapped_column(JSONB, nullable=True)  # None: no content (204)
    sha256: Mapped[str] = mapped_column(String(64))
    first_fetched_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    last_fetched_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))

    __table_args__ = (
        UniqueConstraint("athlete_id", "endpoint", "request_key", "sha256", name="uq_raw_payload_content"),
        Index("ix_raw_payload_lookup", "athlete_id", "endpoint", "request_key", "last_fetched_at"),
    )


class ActivityFile(Base):
    """A downloaded activity file. Bytes live in the blob store under a key derived from the
    content hash; `member_name` (from inside the zip) is stored as data, never used as a path."""

    __tablename__ = "activity_file"

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True)
    athlete_id: Mapped[int] = mapped_column(ForeignKey("athlete.id", ondelete="CASCADE"))
    activity_id: Mapped[int] = mapped_column(BigInteger)
    kind: Mapped[str] = mapped_column(String(16))
    member_name: Mapped[str | None] = mapped_column(String(255), nullable=True)
    blob_key: Mapped[str] = mapped_column(String(512), unique=True)
    sha256: Mapped[str] = mapped_column(String(64))
    size_bytes: Mapped[int] = mapped_column(BigInteger)
    fetched_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    # FIT files only: NULL = not parsed yet, then parsed / invalid / skipped, by parser_version.
    # A newer parser re-parses older results.
    parse_status: Mapped[str | None] = mapped_column(String(16), nullable=True)
    parser_version: Mapped[int | None] = mapped_column(SmallInteger, nullable=True)

    __table_args__ = (
        CheckConstraint("kind IN ('original', 'fit')", name="kind"),
        CheckConstraint("parse_status IN ('parsed', 'invalid', 'skipped')", name="parse_status"),
        UniqueConstraint("athlete_id", "activity_id", "kind", "sha256", name="uq_activity_file_content"),
    )


class SyncCursor(Base):
    """The contiguous date range [synced_from, synced_through] each dataset has been synced
    over, so runs resume and backfills extend it instead of starting over."""

    __tablename__ = "sync_cursor"

    athlete_id: Mapped[int] = mapped_column(ForeignKey("athlete.id", ondelete="CASCADE"), primary_key=True)
    dataset: Mapped[str] = mapped_column(String(64), primary_key=True)
    synced_from: Mapped[date | None] = mapped_column(Date, nullable=True)
    synced_through: Mapped[date | None] = mapped_column(Date, nullable=True)
    last_run_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))


class Activity(Base):
    __tablename__ = "activity"

    athlete_id: Mapped[int] = mapped_column(ForeignKey("athlete.id", ondelete="CASCADE"), primary_key=True)
    activity_id: Mapped[int] = mapped_column(BigInteger, primary_key=True)
    start_time_utc: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    start_time_local: Mapped[datetime | None] = mapped_column(DateTime(timezone=False), nullable=True)
    sport: Mapped[str | None] = mapped_column(String(64), nullable=True)
    event_type: Mapped[str | None] = mapped_column(String(64), nullable=True)
    duration_s: Mapped[float | None] = mapped_column(Float, nullable=True)
    moving_duration_s: Mapped[float | None] = mapped_column(Float, nullable=True)
    elapsed_duration_s: Mapped[float | None] = mapped_column(Float, nullable=True)
    distance_m: Mapped[float | None] = mapped_column(Float, nullable=True)
    elevation_gain_m: Mapped[float | None] = mapped_column(Float, nullable=True)
    elevation_loss_m: Mapped[float | None] = mapped_column(Float, nullable=True)
    avg_hr: Mapped[float | None] = mapped_column(Float, nullable=True)
    max_hr: Mapped[float | None] = mapped_column(Float, nullable=True)
    avg_power_w: Mapped[float | None] = mapped_column(Float, nullable=True)
    normalized_power_w: Mapped[float | None] = mapped_column(Float, nullable=True)
    aerobic_te: Mapped[float | None] = mapped_column(Float, nullable=True)
    anaerobic_te: Mapped[float | None] = mapped_column(Float, nullable=True)
    training_effect_label: Mapped[str | None] = mapped_column(String(64), nullable=True)
    training_load: Mapped[float | None] = mapped_column(Float, nullable=True)
    vo2max: Mapped[float | None] = mapped_column(Float, nullable=True)
    hr_zone_1_s: Mapped[float | None] = mapped_column(Float, nullable=True)
    hr_zone_2_s: Mapped[float | None] = mapped_column(Float, nullable=True)
    hr_zone_3_s: Mapped[float | None] = mapped_column(Float, nullable=True)
    hr_zone_4_s: Mapped[float | None] = mapped_column(Float, nullable=True)
    hr_zone_5_s: Mapped[float | None] = mapped_column(Float, nullable=True)
    raw_payload_id: Mapped[int | None] = mapped_column(ForeignKey("raw_payload.id", ondelete="SET NULL"), nullable=True)
    # Activity file download: NULL = not tried yet, then stored / unavailable / invalid. Only
    # NULL is retried automatically, so missing files don't burn the request budget every run.
    file_status: Mapped[str | None] = mapped_column(String(16), nullable=True)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=_now())

    __table_args__ = (
        Index("ix_activity_start", "athlete_id", "start_time_utc"),
        CheckConstraint("file_status IN ('stored', 'unavailable', 'invalid')", name="file_status"),
    )


class DailySummary(Base):
    __tablename__ = "daily_summary"

    athlete_id: Mapped[int] = mapped_column(ForeignKey("athlete.id", ondelete="CASCADE"), primary_key=True)
    calendar_date: Mapped[date] = mapped_column(Date, primary_key=True)
    resting_hr: Mapped[int | None] = mapped_column(Integer, nullable=True)
    min_hr: Mapped[int | None] = mapped_column(Integer, nullable=True)
    max_hr: Mapped[int | None] = mapped_column(Integer, nullable=True)
    steps: Mapped[int | None] = mapped_column(Integer, nullable=True)
    stress_avg: Mapped[int | None] = mapped_column(Integer, nullable=True)
    stress_max: Mapped[int | None] = mapped_column(Integer, nullable=True)
    body_battery_min: Mapped[int | None] = mapped_column(Integer, nullable=True)
    body_battery_max: Mapped[int | None] = mapped_column(Integer, nullable=True)
    body_battery_charged: Mapped[int | None] = mapped_column(Integer, nullable=True)
    body_battery_drained: Mapped[int | None] = mapped_column(Integer, nullable=True)
    respiration_avg: Mapped[float | None] = mapped_column(Float, nullable=True)
    intensity_moderate_min: Mapped[int | None] = mapped_column(Integer, nullable=True)
    intensity_vigorous_min: Mapped[int | None] = mapped_column(Integer, nullable=True)
    calories_total: Mapped[int | None] = mapped_column(Integer, nullable=True)
    calories_active: Mapped[int | None] = mapped_column(Integer, nullable=True)
    stress_rest_min: Mapped[int | None] = mapped_column(Integer, nullable=True)
    stress_low_min: Mapped[int | None] = mapped_column(Integer, nullable=True)
    stress_medium_min: Mapped[int | None] = mapped_column(Integer, nullable=True)
    stress_high_min: Mapped[int | None] = mapped_column(Integer, nullable=True)
    body_battery_end_of_day: Mapped[int | None] = mapped_column(Integer, nullable=True)
    raw_payload_id: Mapped[int | None] = mapped_column(ForeignKey("raw_payload.id", ondelete="SET NULL"), nullable=True)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=_now())


class ActivitySample(Base):
    """Per-second activity data parsed from the FIT file. Deliberately no GPS: positions stay in
    the raw file only. `elapsed_s` counts seconds from the activity's first record."""

    __tablename__ = "activity_sample"

    athlete_id: Mapped[int] = mapped_column(ForeignKey("athlete.id", ondelete="CASCADE"), primary_key=True)
    activity_id: Mapped[int] = mapped_column(BigInteger, primary_key=True)
    elapsed_s: Mapped[int] = mapped_column(Integer, primary_key=True)
    heart_rate: Mapped[int | None] = mapped_column(SmallInteger, nullable=True)
    cadence: Mapped[int | None] = mapped_column(SmallInteger, nullable=True)
    power_w: Mapped[int | None] = mapped_column(SmallInteger, nullable=True)
    speed_mps: Mapped[float | None] = mapped_column(Float, nullable=True)
    distance_m: Mapped[float | None] = mapped_column(Float, nullable=True)
    altitude_m: Mapped[float | None] = mapped_column(Float, nullable=True)
    temperature_c: Mapped[int | None] = mapped_column(SmallInteger, nullable=True)

    __table_args__ = (
        ForeignKeyConstraint(
            ["athlete_id", "activity_id"],
            ["activity.athlete_id", "activity.activity_id"],
            name="fk_activity_sample_activity",
            ondelete="CASCADE",
        ),
    )


class ActivityLap(Base):
    """Laps or intervals from the FIT file."""

    __tablename__ = "activity_lap"

    athlete_id: Mapped[int] = mapped_column(ForeignKey("athlete.id", ondelete="CASCADE"), primary_key=True)
    activity_id: Mapped[int] = mapped_column(BigInteger, primary_key=True)
    lap_index: Mapped[int] = mapped_column(Integer, primary_key=True)
    start_offset_s: Mapped[int | None] = mapped_column(Integer, nullable=True)
    elapsed_s: Mapped[float | None] = mapped_column(Float, nullable=True)
    timer_s: Mapped[float | None] = mapped_column(Float, nullable=True)
    distance_m: Mapped[float | None] = mapped_column(Float, nullable=True)
    avg_hr: Mapped[int | None] = mapped_column(SmallInteger, nullable=True)
    max_hr: Mapped[int | None] = mapped_column(SmallInteger, nullable=True)
    avg_power_w: Mapped[int | None] = mapped_column(SmallInteger, nullable=True)

    __table_args__ = (
        ForeignKeyConstraint(
            ["athlete_id", "activity_id"],
            ["activity.athlete_id", "activity.activity_id"],
            name="fk_activity_lap_activity",
            ondelete="CASCADE",
        ),
    )


class SleepSummary(Base):
    """One night, keyed by the date Garmin assigns it. No free text (notes, insights)."""

    __tablename__ = "sleep_summary"

    athlete_id: Mapped[int] = mapped_column(ForeignKey("athlete.id", ondelete="CASCADE"), primary_key=True)
    calendar_date: Mapped[date] = mapped_column(Date, primary_key=True)
    sleep_start_utc: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    sleep_end_utc: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    sleep_s: Mapped[int | None] = mapped_column(Integer, nullable=True)
    deep_s: Mapped[int | None] = mapped_column(Integer, nullable=True)
    light_s: Mapped[int | None] = mapped_column(Integer, nullable=True)
    rem_s: Mapped[int | None] = mapped_column(Integer, nullable=True)
    awake_s: Mapped[int | None] = mapped_column(Integer, nullable=True)
    nap_s: Mapped[int | None] = mapped_column(Integer, nullable=True)
    awake_count: Mapped[int | None] = mapped_column(Integer, nullable=True)
    sleep_score: Mapped[int | None] = mapped_column(SmallInteger, nullable=True)
    avg_hr: Mapped[float | None] = mapped_column(Float, nullable=True)
    avg_stress: Mapped[float | None] = mapped_column(Float, nullable=True)
    avg_respiration: Mapped[float | None] = mapped_column(Float, nullable=True)
    avg_spo2: Mapped[float | None] = mapped_column(Float, nullable=True)
    raw_payload_id: Mapped[int | None] = mapped_column(ForeignKey("raw_payload.id", ondelete="SET NULL"), nullable=True)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=_now())


class HrvSummary(Base):
    """Nightly HRV (ms) with Garmin's baseline bands and status."""

    __tablename__ = "hrv_summary"

    athlete_id: Mapped[int] = mapped_column(ForeignKey("athlete.id", ondelete="CASCADE"), primary_key=True)
    calendar_date: Mapped[date] = mapped_column(Date, primary_key=True)
    last_night_avg: Mapped[int | None] = mapped_column(SmallInteger, nullable=True)
    last_night_5min_high: Mapped[int | None] = mapped_column(SmallInteger, nullable=True)
    weekly_avg: Mapped[int | None] = mapped_column(SmallInteger, nullable=True)
    baseline_low_upper: Mapped[int | None] = mapped_column(SmallInteger, nullable=True)
    baseline_balanced_low: Mapped[int | None] = mapped_column(SmallInteger, nullable=True)
    baseline_balanced_upper: Mapped[int | None] = mapped_column(SmallInteger, nullable=True)
    status: Mapped[str | None] = mapped_column(String(32), nullable=True)
    raw_payload_id: Mapped[int | None] = mapped_column(ForeignKey("raw_payload.id", ondelete="SET NULL"), nullable=True)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=_now())


class WeightDaily(Base):
    """The day's latest weigh-in. Body composition is a sensitive category: stored for the
    owner's analysis, and opt-in before anything reaches a model (docs/plan.md)."""

    __tablename__ = "weight_daily"

    athlete_id: Mapped[int] = mapped_column(ForeignKey("athlete.id", ondelete="CASCADE"), primary_key=True)
    calendar_date: Mapped[date] = mapped_column(Date, primary_key=True)
    weight_kg: Mapped[float | None] = mapped_column(Float, nullable=True)
    bmi: Mapped[float | None] = mapped_column(Float, nullable=True)
    body_fat_pct: Mapped[float | None] = mapped_column(Float, nullable=True)
    entries: Mapped[int | None] = mapped_column(SmallInteger, nullable=True)
    raw_payload_id: Mapped[int | None] = mapped_column(ForeignKey("raw_payload.id", ondelete="SET NULL"), nullable=True)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=_now())


class SyncRun(Base):
    """One sync run: when, how it ended and what it stored. Counts only, never data values."""

    __tablename__ = "sync_run"

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True)
    athlete_id: Mapped[int] = mapped_column(ForeignKey("athlete.id", ondelete="CASCADE"))
    started_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    finished_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    stopped: Mapped[str | None] = mapped_column(String(32), nullable=True)  # None = complete
    requests: Mapped[int] = mapped_column(Integer)
    counts: Mapped[Any] = mapped_column(JSONB)

    __table_args__ = (Index("ix_sync_run_finished", "athlete_id", "finished_at"),)

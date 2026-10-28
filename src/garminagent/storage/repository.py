"""Storage operations: raw payloads, activity files, normalized rows, sync cursors, purge.

Callers own the transaction (pass a Session, commit when done), except `purge_athlete`, which
must commit the database delete before removing blobs. Nothing here logs payload contents.
"""

import hashlib
import json
import logging
from dataclasses import asdict, dataclass
from datetime import UTC, date, datetime
from typing import Any

from sqlalchemy import delete, func, select, update
from sqlalchemy.dialects.postgresql import insert
from sqlalchemy.orm import Session, sessionmaker

from garminagent.garmin import ActivityFile as DownloadedFile
from garminagent.garmin.files import is_fit
from garminagent.normalize import ActivityRecord, DailySummaryRecord
from garminagent.storage.blobs import BlobStore
from garminagent.storage.models import (
    Activity,
    ActivityFile,
    Athlete,
    Base,
    DailySummary,
    RawPayload,
    SyncCursor,
)

log = logging.getLogger(__name__)

MAX_MEMBER_NAME = 255


def ensure_athlete(session: Session) -> int:
    """The single athlete's id, created on first use."""
    existing = session.scalar(select(Athlete.id).order_by(Athlete.id).limit(1))
    if existing is not None:
        return existing
    athlete = Athlete()
    session.add(athlete)
    session.flush()
    return athlete.id


def payload_sha256(payload: Any) -> str:
    """Hash of the canonical JSON, so key order and whitespace don't create new versions."""
    canonical = b"" if payload is None else json.dumps(payload, sort_keys=True, separators=(",", ":")).encode()
    return hashlib.sha256(canonical).hexdigest()


def save_raw_payload(
    session: Session,
    athlete_id: int,
    endpoint: str,
    request_key: str,
    status_code: int,
    payload: Any,
    fetched_at: datetime | None = None,
) -> int:
    """Store a response; returns its row id. Identical content only refreshes last_fetched_at."""
    now = fetched_at or datetime.now(UTC)
    stmt = (
        insert(RawPayload)
        .values(
            athlete_id=athlete_id,
            endpoint=endpoint,
            request_key=request_key,
            status_code=status_code,
            payload=payload,
            sha256=payload_sha256(payload),
            first_fetched_at=now,
            last_fetched_at=now,
        )
        .on_conflict_do_update(constraint="uq_raw_payload_content", set_={"last_fetched_at": now})
        .returning(RawPayload.id)
    )
    return session.execute(stmt).scalar_one()


def save_activity_file(
    session: Session, blobs: BlobStore, athlete_id: int, file: DownloadedFile, fetched_at: datetime | None = None
) -> int:
    """Store the original download and each FIT file inside it; returns rows added.
    Blob keys come from IDs and hashes only; zip member names are kept as data."""
    now = fetched_at or datetime.now(UTC)
    original_ext = "fit" if is_fit(file.original) else "zip"
    parts: list[tuple[str, str | None, str, bytes]] = [("original", None, original_ext, file.original)]
    parts += [("fit", name[:MAX_MEMBER_NAME], "fit", data) for name, data in file.fit_files.items()]

    added = 0
    for kind, member_name, ext, data in parts:
        digest = hashlib.sha256(data).hexdigest()
        key = f"{athlete_id}/activities/{file.activity_id}/{kind}-{digest}.{ext}"
        if not blobs.exists(key):
            blobs.put(key, data)
        stmt = (
            insert(ActivityFile)
            .values(
                athlete_id=athlete_id,
                activity_id=file.activity_id,
                kind=kind,
                member_name=member_name,
                blob_key=key,
                sha256=digest,
                size_bytes=len(data),
                fetched_at=now,
            )
            .on_conflict_do_nothing(constraint="uq_activity_file_content")
            .returning(ActivityFile.id)
        )
        added += 1 if session.execute(stmt).scalar_one_or_none() is not None else 0
    return added


def upsert_activities(
    session: Session, athlete_id: int, records: list[ActivityRecord], raw_payload_id: int | None = None
) -> int:
    return _upsert(session, Activity, ["athlete_id", "activity_id"], athlete_id, records, raw_payload_id)


def upsert_daily_summaries(
    session: Session, athlete_id: int, records: list[DailySummaryRecord], raw_payload_id: int | None = None
) -> int:
    return _upsert(session, DailySummary, ["athlete_id", "calendar_date"], athlete_id, records, raw_payload_id)


def _upsert(
    session: Session,
    model: type[Base],
    key_columns: list[str],
    athlete_id: int,
    records: list[ActivityRecord] | list[DailySummaryRecord],
    raw_payload_id: int | None,
) -> int:
    if not records:
        return 0
    rows = [{**asdict(r), "athlete_id": athlete_id, "raw_payload_id": raw_payload_id} for r in records]
    stmt = insert(model).values(rows)
    updates = {c: stmt.excluded[c] for c in rows[0] if c not in key_columns}
    session.execute(stmt.on_conflict_do_update(index_elements=key_columns, set_={**updates, "updated_at": func.now()}))
    return len(rows)


@dataclass(frozen=True)
class SyncRange:
    """An inclusive date range."""

    start: date
    end: date


def get_cursor(session: Session, athlete_id: int, dataset: str) -> SyncRange | None:
    """The synced range for a dataset, or None if it was never synced."""
    row = session.execute(
        select(SyncCursor.synced_from, SyncCursor.synced_through).where(
            SyncCursor.athlete_id == athlete_id, SyncCursor.dataset == dataset
        )
    ).one_or_none()
    if row is None or row.synced_through is None:
        return None
    # Rows from before ranges existed have no lower end; treat them as covering one day.
    return SyncRange(start=row.synced_from or row.synced_through, end=row.synced_through)


def set_cursor(session: Session, athlete_id: int, dataset: str, synced: SyncRange) -> None:
    now = datetime.now(UTC)
    values = {"synced_from": synced.start, "synced_through": synced.end, "last_run_at": now}
    stmt = insert(SyncCursor).values(athlete_id=athlete_id, dataset=dataset, **values)
    session.execute(stmt.on_conflict_do_update(index_elements=["athlete_id", "dataset"], set_=values))


def activities_without_file(session: Session, athlete_id: int, limit: int) -> list[int]:
    """Activities whose file was never tried, newest first (most useful for planning)."""
    return list(
        session.scalars(
            select(Activity.activity_id)
            .where(Activity.athlete_id == athlete_id, Activity.file_status.is_(None))
            .order_by(Activity.start_time_utc.desc())
            .limit(limit)
        )
    )


def set_file_status(session: Session, athlete_id: int, activity_id: int, status: str) -> None:
    session.execute(
        update(Activity)
        .where(Activity.athlete_id == athlete_id, Activity.activity_id == activity_id)
        .values(file_status=status)
    )


@dataclass(frozen=True)
class PurgeResult:
    rows: dict[str, int]
    blobs: int


def purge_athlete(sessions: sessionmaker[Session], blobs: BlobStore, athlete_id: int) -> PurgeResult:
    """Delete all of an athlete's data: database rows (one transaction, committed first), then
    blobs. Safe to re-run: if blob deletion fails midway, the next run removes the rest."""
    tables = [RawPayload, ActivityFile, SyncCursor, Activity, DailySummary]
    with sessions.begin() as session:
        rows = {
            t.__tablename__: session.scalar(select(func.count()).select_from(t).where(t.athlete_id == athlete_id)) or 0
            for t in tables
        }
        session.execute(delete(Athlete).where(Athlete.id == athlete_id))  # cascades to all tables above
    removed = blobs.delete_prefix(str(athlete_id))
    log.info("purged athlete %d: %d rows, %d blobs", athlete_id, sum(rows.values()), removed)
    return PurgeResult(rows=rows, blobs=removed)

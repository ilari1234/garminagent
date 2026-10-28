"""Sync Garmin data into the store: backfill and incremental, resumable, within a request budget.

Each dataset keeps a contiguous synced range in its cursor. A run
- extends it backwards to the requested start (backfill), newest chunk first, and
- forwards to today, re-fetching the last few days because Garmin revises recent data.
The cursor is moved only after a chunk is fully stored, so an interrupted run never leaves a
gap; the next run re-does at most the unfinished chunk (all writes are idempotent).

Every Garmin call first checks the run's request budget. Running out ends the run cleanly
("budget"); Garmin rate limiting ends it too ("rate_limited"). Either way everything stored so
far is kept, and the next run continues.

Only counts are logged and reported, never payload contents.
"""

import logging
from collections.abc import Iterator
from dataclasses import dataclass, field
from datetime import date, timedelta

from sqlalchemy.orm import Session, sessionmaker

from garminagent.fit import PARSER_VERSION, FitParseError, parse_fit
from garminagent.garmin import (
    RANGE_LIMITS,
    GarminApiError,
    GarminAuthError,
    GarminClient,
    GarminError,
    GarminRateLimitError,
    UnexpectedFileError,
    date_chunks,
)
from garminagent.garmin.client import ACTIVITY_PAGE_SIZE
from garminagent.normalize import ActivityRecord, parse_activities, parse_daily_summaries
from garminagent.storage.blobs import BlobStore
from garminagent.storage.repository import (
    SyncRange,
    activities_without_file,
    count_activities_without_file,
    ensure_athlete,
    fit_files_to_parse,
    get_cursor,
    replace_activity_samples,
    save_activity_file,
    save_raw_payload,
    set_cursor,
    set_file_status,
    set_parse_status,
    upsert_activities,
    upsert_daily_summaries,
)

log = logging.getLogger(__name__)

DAILY_SUMMARIES = "daily_summaries"
ACTIVITIES = "activities"
ACTIVITY_FILES = "activity_files"
FIT_FILES = "fit_files"
DEFAULT_HISTORY_DAYS = 730
ACTIVITY_CHUNK_DAYS = 90  # small enough that an interrupted backfill loses little work
FILE_BATCH = 50
FILE_PROGRESS_EVERY = 25
FIT_PARSE_BATCH = 50


def plan_chunks(
    cursor: SyncRange | None, start: date, today: date, refetch_days: int, chunk_days: int
) -> list[SyncRange]:
    """The chunks to fetch, in an order where each one touches the range synced so far:
    first backwards from the cursor to `start` (newest first), then forwards to `today`."""
    if cursor is None:
        return [SyncRange(a, b) for a, b in date_chunks(start, today, chunk_days)] if start <= today else []
    chunks: list[SyncRange] = []
    if start < cursor.start:
        older = list(date_chunks(start, cursor.start - timedelta(days=1), chunk_days))
        chunks += [SyncRange(a, b) for a, b in reversed(older)]
    if refetch_days > 0:  # re-fetch the last `refetch_days` synced days
        forward_start = max(cursor.start, cursor.end - timedelta(days=refetch_days - 1))
    else:
        forward_start = cursor.end + timedelta(days=1)
    if forward_start <= today:
        chunks += [SyncRange(a, b) for a, b in date_chunks(forward_start, today, chunk_days)]
    return chunks


def extend(cursor: SyncRange | None, chunk: SyncRange) -> SyncRange:
    """The synced range after storing `chunk`; it must overlap or touch the current range."""
    if cursor is None:
        return chunk
    if chunk.end < cursor.start - timedelta(days=1) or chunk.start > cursor.end + timedelta(days=1):
        raise ValueError(f"chunk {chunk} would leave a gap in synced range {cursor}")
    return SyncRange(min(cursor.start, chunk.start), max(cursor.end, chunk.end))


@dataclass
class SyncReport:
    counts: dict[str, dict[str, int]] = field(default_factory=dict[str, dict[str, int]])
    requests: int = 0
    stopped: str | None = None  # None (complete), "budget", "rate_limited" or "garmin_error"

    @property
    def complete(self) -> bool:
        return self.stopped is None

    def add(self, dataset: str, key: str, n: int = 1) -> None:
        counts = self.counts.setdefault(dataset, {})
        counts[key] = counts.get(key, 0) + n


class _BudgetExhausted(Exception):
    pass


class Syncer:
    def __init__(
        self,
        client: GarminClient,
        sessions: sessionmaker[Session],
        blobs: BlobStore,
        *,
        start: date,
        today: date,
        max_requests: int,
        refetch_days: int = 3,
        activity_refetch_days: int = 7,
        page_size: int = ACTIVITY_PAGE_SIZE,
    ) -> None:
        self._client = client
        self._sessions = sessions
        self._blobs = blobs
        self._start = start
        self._today = today
        self._max_requests = max_requests
        self._refetch_days = refetch_days
        self._activity_refetch_days = activity_refetch_days
        self._page_size = page_size
        self._report = SyncReport()

    def run(self) -> SyncReport:
        """Sync daily summaries, then activities, then activity files, then parse downloaded FIT
        files. Raises GarminAuthError (nothing can work without the key); every other stop is
        recorded in the report. Parsing needs no Garmin requests, so it runs even after a stop."""
        with self._sessions.begin() as session:
            athlete = ensure_athlete(session)
        try:
            self._sync_daily_summaries(athlete)
            self._sync_activities(athlete)
            self._sync_activity_files(athlete)
        except _BudgetExhausted:
            log.info("request budget of %d reached; run sync again to continue", self._max_requests)
            self._report.stopped = "budget"
        except GarminRateLimitError as exc:
            log.warning("stopping: %s", exc)
            self._report.stopped = "rate_limited"
        except GarminAuthError:
            raise
        except GarminError as exc:
            log.error("stopping after Garmin error: %s", exc)
            self._report.stopped = "garmin_error"
        self._report.requests = self._client.requests_made
        parse_pending_fit_files(self._sessions, self._blobs, athlete, self._report)
        log.info(
            "sync %s after %d requests: %s",
            "complete" if self._report.complete else f"stopped ({self._report.stopped})",
            self._report.requests,
            self._report.counts,
        )
        return self._report

    def _used(self) -> tuple[int, int]:
        return self._client.requests_made, self._max_requests

    def _spend(self) -> None:
        # Retries and the token request also count, so a run can exceed the budget by a few.
        if self._client.requests_made >= self._max_requests:
            raise _BudgetExhausted

    def _chunks(
        self, athlete: int, dataset: str, refetch_days: int, chunk_days: int
    ) -> Iterator[tuple[SyncRange, SyncRange | None]]:
        with self._sessions() as session:
            cursor = get_cursor(session, athlete, dataset)
        for chunk in plan_chunks(cursor, self._start, self._today, refetch_days, chunk_days):
            yield chunk, cursor
            cursor = extend(cursor, chunk)

    def _sync_daily_summaries(self, athlete: int) -> None:
        for chunk, cursor in self._chunks(
            athlete, DAILY_SUMMARIES, self._refetch_days, RANGE_LIMITS["daily_summaries"]
        ):
            self._spend()
            payload = self._client.get_daily_summaries(chunk.start, chunk.end)
            parsed = parse_daily_summaries(payload)
            with self._sessions.begin() as session:
                raw_id = save_raw_payload(
                    session,
                    athlete,
                    DAILY_SUMMARIES,
                    f"{chunk.start}..{chunk.end}",
                    204 if payload is None else 200,
                    payload,
                )
                upsert_daily_summaries(session, athlete, parsed.records, raw_id)
                set_cursor(session, athlete, DAILY_SUMMARIES, extend(cursor, chunk))
            self._report.add(DAILY_SUMMARIES, "chunks")
            self._report.add(DAILY_SUMMARIES, "records", len(parsed.records))
            self._report.add(DAILY_SUMMARIES, "skipped", parsed.skipped)
            log.info(
                "daily summaries %s..%s: %d days stored (requests %d/%d)",
                chunk.start,
                chunk.end,
                len(parsed.records),
                *self._used(),
            )

    def _sync_activities(self, athlete: int) -> None:
        for chunk, cursor in self._chunks(athlete, ACTIVITIES, self._activity_refetch_days, ACTIVITY_CHUNK_DAYS):
            # Read pages away from the synced range (oldest first going forward, newest first
            # when backfilling), so finished days always touch it and can be saved page by page.
            backward = cursor is not None and chunk.end < cursor.start
            order = "desc" if backward else "asc"
            offset = 0
            pages = records = 0
            while True:
                self._spend()
                page = self._client.search_activities(
                    start=offset, limit=self._page_size, start_date=chunk.start, end_date=chunk.end, sort_order=order
                )
                if page is not None and not isinstance(page, list):
                    raise GarminError("activity search returned an unexpected shape (expected a list)")
                items = page or []
                parsed = parse_activities(items)
                request_key = f"{chunk.start}..{chunk.end}&order={order}&start={offset}&limit={self._page_size}"
                done = self._finished_days(chunk, parsed.records, backward) if len(items) == self._page_size else None
                with self._sessions.begin() as session:
                    raw_id = save_raw_payload(session, athlete, ACTIVITIES, request_key, 200, page)
                    upsert_activities(session, athlete, parsed.records, raw_id)
                    if done is not None:  # progress survives a run that stops mid-chunk
                        set_cursor(session, athlete, ACTIVITIES, extend(cursor, done))
                self._report.add(ACTIVITIES, "pages")
                self._report.add(ACTIVITIES, "records", len(parsed.records))
                self._report.add(ACTIVITIES, "skipped", parsed.skipped)
                pages += 1
                records += len(parsed.records)
                if len(items) < self._page_size:
                    break
                offset += self._page_size
            with self._sessions.begin() as session:
                set_cursor(session, athlete, ACTIVITIES, extend(cursor, chunk))
            self._report.add(ACTIVITIES, "chunks")
            log.info(
                "activities %s..%s: %d stored from %d pages (requests %d/%d)",
                chunk.start,
                chunk.end,
                records,
                pages,
                *self._used(),
            )

    @staticmethod
    def _finished_days(chunk: SyncRange, records: list[ActivityRecord], backward: bool) -> SyncRange | None:
        """After a full page, the part of the chunk known to be complete: every day before the
        page's last activity day (going forward) or after it (backfilling). That day itself may
        continue on the next page."""
        last = records[-1].start_time_local if records else None
        if last is None:
            return None
        if backward:
            first = last.date() + timedelta(days=1)
            return SyncRange(first, chunk.end) if first <= chunk.end else None
        final = last.date() - timedelta(days=1)
        return SyncRange(chunk.start, final) if final >= chunk.start else None

    def _sync_activity_files(self, athlete: int) -> None:
        with self._sessions() as session:
            pending_total = count_activities_without_file(session, athlete)
        if pending_total:
            log.info("activity files: %d to download", pending_total)
        tried: set[int] = set()
        while True:
            with self._sessions() as session:
                pending = [a for a in activities_without_file(session, athlete, FILE_BATCH) if a not in tried]
            if not pending:
                return
            for activity_id in pending:
                tried.add(activity_id)
                self._spend()
                self._download(athlete, activity_id)
                if len(tried) % FILE_PROGRESS_EVERY == 0:
                    log.info(
                        "activity files: %d of %d done this run (requests %d/%d)",
                        len(tried),
                        pending_total,
                        *self._used(),
                    )

    def _download(self, athlete: int, activity_id: int) -> None:
        try:
            file = self._client.download_activity_file(activity_id)
        except GarminApiError as exc:
            if not 400 <= exc.status < 500:
                raise  # server trouble: stop the run, retry next time
            status = "unavailable"  # e.g. manual entries without a file
        except UnexpectedFileError as exc:
            log.warning("activity %d: file rejected (%s)", activity_id, exc.reason)
            status = "invalid"
        else:
            with self._sessions.begin() as session:
                save_activity_file(session, self._blobs, athlete, file)
                set_file_status(session, athlete, activity_id, "stored")
            self._report.add(ACTIVITY_FILES, "stored")
            return
        with self._sessions.begin() as session:
            set_file_status(session, athlete, activity_id, status)
        self._report.add(ACTIVITY_FILES, status)


def parse_pending_fit_files(
    sessions: sessionmaker[Session], blobs: BlobStore, athlete: int, report: SyncReport
) -> None:
    """Parse stored FIT files that are new or were parsed by an older parser. One FIT file per
    activity is used (the first by name); further ones are marked skipped."""
    seen: set[int] = set()
    parsed_activities: set[int] = set()
    while True:
        with sessions() as session:
            batch = [
                f
                for f in fit_files_to_parse(session, athlete, PARSER_VERSION, FIT_PARSE_BATCH)
                if f.file_id not in seen
            ]
        if not batch:
            break
        for item in batch:
            seen.add(item.file_id)
            if item.activity_id in parsed_activities:
                status = "skipped"
            else:
                try:
                    parsed = parse_fit(blobs.get(item.blob_key))
                except FitParseError as exc:
                    log.warning("activity %d: FIT file rejected (%s)", item.activity_id, exc.reason)
                    status = "invalid"
                except FileNotFoundError:
                    log.warning("activity %d: FIT file missing from the blob store", item.activity_id)
                    status = "invalid"
                else:
                    with sessions.begin() as session:
                        samples = replace_activity_samples(session, athlete, item.activity_id, parsed)
                        set_parse_status(session, item.file_id, "parsed", PARSER_VERSION)
                    parsed_activities.add(item.activity_id)
                    report.add(FIT_FILES, "parsed")
                    report.add(FIT_FILES, "samples", samples)
                    if len(parsed_activities) % FILE_PROGRESS_EVERY == 0:
                        log.info("FIT files: %d parsed this run", len(parsed_activities))
                    continue
            with sessions.begin() as session:
                set_parse_status(session, item.file_id, status, PARSER_VERSION)
            report.add(FIT_FILES, status)
    if seen:
        log.info(
            "FIT files: %d parsed, %d samples stored",
            len(parsed_activities),
            report.counts.get(FIT_FILES, {}).get("samples", 0),
        )

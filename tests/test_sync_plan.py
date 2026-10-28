from datetime import date

import pytest

from garminagent.storage.repository import SyncRange
from garminagent.sync import extend, plan_chunks

D = date


def ranges(chunks: list[SyncRange]) -> list[tuple[date, date]]:
    return [(c.start, c.end) for c in chunks]


def test_first_sync_goes_forward_from_start() -> None:
    chunks = plan_chunks(None, D(2026, 1, 1), D(2026, 3, 1), refetch_days=3, chunk_days=31)
    assert ranges(chunks) == [
        (D(2026, 1, 1), D(2026, 1, 31)),
        (D(2026, 2, 1), D(2026, 3, 1)),
    ]


def test_incremental_refetches_trailing_days() -> None:
    cursor = SyncRange(D(2026, 1, 1), D(2026, 3, 1))
    chunks = plan_chunks(cursor, D(2026, 1, 1), D(2026, 3, 5), refetch_days=3, chunk_days=31)
    assert ranges(chunks) == [(D(2026, 2, 27), D(2026, 3, 5))]


def test_zero_refetch_starts_after_cursor() -> None:
    cursor = SyncRange(D(2026, 1, 1), D(2026, 3, 1))
    assert ranges(plan_chunks(cursor, D(2026, 1, 1), D(2026, 3, 3), 0, 31)) == [(D(2026, 3, 2), D(2026, 3, 3))]
    assert plan_chunks(cursor, D(2026, 1, 1), D(2026, 3, 1), 0, 31) == []


def test_backfill_goes_backwards_newest_chunk_first() -> None:
    cursor = SyncRange(D(2026, 3, 1), D(2026, 3, 10))
    chunks = plan_chunks(cursor, D(2026, 1, 1), D(2026, 3, 10), refetch_days=1, chunk_days=31)
    assert ranges(chunks) == [
        (D(2026, 2, 1), D(2026, 2, 28)),
        (D(2026, 1, 1), D(2026, 1, 31)),
        (D(2026, 3, 10), D(2026, 3, 10)),
    ]


def test_every_planned_chunk_extends_the_range_without_gaps() -> None:
    cursor: SyncRange | None = SyncRange(D(2025, 6, 1), D(2025, 9, 1))
    for chunk in plan_chunks(cursor, D(2024, 1, 1), D(2026, 1, 1), refetch_days=7, chunk_days=90):
        cursor = extend(cursor, chunk)
    assert cursor == SyncRange(D(2024, 1, 1), D(2026, 1, 1))


def test_start_after_today_plans_nothing() -> None:
    assert plan_chunks(None, D(2026, 2, 1), D(2026, 1, 1), 3, 31) == []


def test_refetch_never_reaches_before_cursor_start() -> None:
    cursor = SyncRange(D(2026, 3, 1), D(2026, 3, 2))
    assert ranges(plan_chunks(cursor, D(2026, 3, 1), D(2026, 3, 2), 30, 31)) == [(D(2026, 3, 1), D(2026, 3, 2))]


def test_extend_rejects_gaps() -> None:
    cursor = SyncRange(D(2026, 3, 1), D(2026, 3, 10))
    assert extend(cursor, SyncRange(D(2026, 3, 11), D(2026, 3, 20))) == SyncRange(D(2026, 3, 1), D(2026, 3, 20))
    assert extend(cursor, SyncRange(D(2026, 2, 1), D(2026, 2, 28))) == SyncRange(D(2026, 2, 1), D(2026, 3, 10))
    with pytest.raises(ValueError, match="gap"):
        extend(cursor, SyncRange(D(2026, 3, 12), D(2026, 3, 20)))

"""Sync ranges and activity file status.

- sync_cursor.synced_from: lower end of the synced range, so backfills can extend history
  without gaps.
- activity.file_status: outcome of the activity file download, so files Garmin doesn't have
  (or that fail validation) aren't retried on every run.

Revision ID: 0002
Revises: 0001
Create Date: 2026-10-28 20:48:58.088958
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0002"
down_revision: str | None = "0001"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column("activity", sa.Column("file_status", sa.String(length=16), nullable=True))
    op.create_check_constraint(
        op.f("ck_activity_file_status"), "activity", "file_status IN ('stored', 'unavailable', 'invalid')"
    )
    op.add_column("sync_cursor", sa.Column("synced_from", sa.Date(), nullable=True))


def downgrade() -> None:
    op.drop_column("sync_cursor", "synced_from")
    op.drop_constraint(op.f("ck_activity_file_status"), "activity", type_="check")
    op.drop_column("activity", "file_status")

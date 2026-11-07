"""Phase 2 M6a: HR zone history, the threshold timeline and threshold columns on activity.

`athlete_zones` keeps Garmin's per-sport HR zone settings as history (the endpoint returns
today's only). `athlete_threshold` is the timeline the analytics resolve thresholds from.
The five `activity` columns come from fields already stored in `raw_payload`;
`garminagent renormalize activities` fills them for history.

Unlike 0002-0005, this migration grants the app role explicitly on its new tables instead of
relying on 0001's default privileges (ADR 0013; storage contract, invariant 7): default
privileges only apply to tables created by the role that set them, so an owner change would
silently leave new tables unreadable.

Revision ID: 0006
Revises: 0005
Create Date: 2026-11-07 12:00:00.000000
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

from garminagent.config import ROLE_NAME, get_settings

revision: str = "0006"
down_revision: str | None = "0005"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

NEW_TABLES = ("athlete_zones", "athlete_threshold")
ACTIVITY_COLUMNS = ("lactate_threshold_hr", "threshold_speed_mps", "ftp_w", "garmin_tss", "garmin_if")


def upgrade() -> None:
    op.create_table(
        "athlete_zones",
        sa.Column("id", sa.BigInteger(), nullable=False),
        sa.Column("athlete_id", sa.Integer(), nullable=False),
        sa.Column("sport", sa.String(length=16), nullable=False),
        sa.Column("valid_from", sa.Date(), nullable=False),
        sa.Column("training_method", sa.String(length=24), nullable=True),
        sa.Column("zone1_floor", sa.SmallInteger(), nullable=True),
        sa.Column("zone2_floor", sa.SmallInteger(), nullable=True),
        sa.Column("zone3_floor", sa.SmallInteger(), nullable=True),
        sa.Column("zone4_floor", sa.SmallInteger(), nullable=True),
        sa.Column("zone5_floor", sa.SmallInteger(), nullable=True),
        sa.Column("max_hr", sa.SmallInteger(), nullable=True),
        sa.Column("resting_hr", sa.SmallInteger(), nullable=True),
        sa.Column("lthr", sa.SmallInteger(), nullable=True),
        sa.Column("raw_payload_id", sa.BigInteger(), nullable=True),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
        sa.CheckConstraint(
            "sport IN ('DEFAULT', 'RUNNING', 'CYCLING', 'SWIMMING', 'ROWING')", name=op.f("ck_athlete_zones_sport")
        ),
        sa.CheckConstraint(
            "training_method IN ('HR_MAX', 'LACTATE_THRESHOLD', 'HR_RESERVE', 'CUSTOM')",
            name=op.f("ck_athlete_zones_training_method"),
        ),
        sa.ForeignKeyConstraint(
            ["athlete_id"], ["athlete.id"], name=op.f("fk_athlete_zones_athlete_id_athlete"), ondelete="CASCADE"
        ),
        sa.ForeignKeyConstraint(
            ["raw_payload_id"],
            ["raw_payload.id"],
            name=op.f("fk_athlete_zones_raw_payload_id_raw_payload"),
            ondelete="SET NULL",
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_athlete_zones")),
        sa.UniqueConstraint("athlete_id", "sport", "valid_from", name="uq_athlete_zones_day"),
    )
    op.create_table(
        "athlete_threshold",
        sa.Column("id", sa.BigInteger(), nullable=False),
        sa.Column("athlete_id", sa.Integer(), nullable=False),
        sa.Column("value_type", sa.String(length=16), nullable=False),
        sa.Column("sport", sa.String(length=16), nullable=False),
        sa.Column("source", sa.String(length=16), nullable=False),
        sa.Column("valid_from", sa.Date(), nullable=False),
        sa.Column("value", sa.Float(), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
        sa.CheckConstraint(
            "value_type IN ('ftp', 'lthr', 'max_hr', 'resting_hr', 'threshold_speed')",
            name=op.f("ck_athlete_threshold_value_type"),
        ),
        sa.CheckConstraint(
            "sport IN ('all', 'run', 'ride', 'ski', 'strength', 'other')", name=op.f("ck_athlete_threshold_sport")
        ),
        sa.CheckConstraint(
            "source IN ('manual', 'garmin_zones', 'activity')", name=op.f("ck_athlete_threshold_source")
        ),
        sa.ForeignKeyConstraint(
            ["athlete_id"], ["athlete.id"], name=op.f("fk_athlete_threshold_athlete_id_athlete"), ondelete="CASCADE"
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_athlete_threshold")),
        sa.UniqueConstraint(
            "athlete_id", "value_type", "sport", "source", "valid_from", name="uq_athlete_threshold_key"
        ),
    )
    for column in ACTIVITY_COLUMNS:
        op.add_column("activity", sa.Column(column, sa.Float(), nullable=True))
    _grant_app_role(_app_role())


def downgrade() -> None:
    for column in reversed(ACTIVITY_COLUMNS):
        op.drop_column("activity", column)
    op.drop_table("athlete_threshold")
    op.drop_table("athlete_zones")


def _app_role() -> str:
    config = op.get_context().config
    role = config.attributes.get("app_role") if config is not None else None
    role = role if isinstance(role, str) else get_settings().db_app_role
    if not ROLE_NAME.fullmatch(role):  # interpolated into SQL below
        raise ValueError("invalid app role name")
    return role


def _grant_app_role(role: str) -> None:
    """Data access on the new tables and their id sequences, nothing more; skipped with a notice
    when the role doesn't exist (as in 0001). Role names can't be bind parameters; `role` is
    validated against ROLE_NAME and the table names are constants."""
    tables = ", ".join(NEW_TABLES)
    sequences = ", ".join(f"{t}_id_seq" for t in NEW_TABLES)
    op.execute(
        f"""
        DO $$
        BEGIN
          IF EXISTS (SELECT 1 FROM pg_roles WHERE rolname = '{role}') THEN
            GRANT SELECT, INSERT, UPDATE, DELETE ON {tables} TO {role};
            GRANT USAGE, SELECT ON SEQUENCE {sequences} TO {role};
          ELSE
            RAISE NOTICE 'role % does not exist; no grants applied', '{role}';
          END IF;
        END $$;
        """  # noqa: S608 - role validated against ROLE_NAME; table names are constants
    )

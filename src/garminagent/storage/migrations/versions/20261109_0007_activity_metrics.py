"""Phase 2 M6b: per-activity metrics.

`activity_metrics` holds one row per activity, computed by `analyze` from FIT samples and the
thresholds in effect on the activity's day (ADR 0013). Rows are recomputed when their input
fingerprint changes, at most ANALYTICS_MAX_ACTIVITIES per run; nothing here needs filling for
history, the next `analyze` does it.

Like 0006, this migration grants the app role explicitly on its new table rather than relying
on 0001's default privileges (storage contract, invariant 7). The table has a composite key and
no sequence.

Revision ID: 0007
Revises: 0006
Create Date: 2026-11-09 12:00:00.000000
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

from garminagent.config import ROLE_NAME, get_settings

revision: str = "0007"
down_revision: str | None = "0006"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

NEW_TABLES = ("activity_metrics",)


def upgrade() -> None:
    op.create_table(
        "activity_metrics",
        sa.Column("athlete_id", sa.Integer(), nullable=False),
        sa.Column("activity_id", sa.BigInteger(), nullable=False),
        sa.Column("activity_date", sa.Date(), nullable=False),
        sa.Column("sport_group", sa.String(length=16), nullable=False),
        sa.Column("algo_version", sa.SmallInteger(), nullable=False),
        sa.Column("input_fingerprint", sa.String(length=64), nullable=False),
        sa.Column("moving_s", sa.Integer(), nullable=True),
        sa.Column("hr_coverage", sa.Float(), nullable=True),
        sa.Column("power_coverage", sa.Float(), nullable=True),
        sa.Column("speed_coverage", sa.Float(), nullable=True),
        sa.Column("weighted_power_w", sa.Float(), nullable=True),
        sa.Column("intensity", sa.Float(), nullable=True),
        sa.Column("power_load", sa.Float(), nullable=True),
        sa.Column("hr_load", sa.Float(), nullable=True),
        sa.Column("load", sa.Float(), nullable=True),
        sa.Column("load_method", sa.String(length=8), nullable=False),
        sa.Column("hr_zone_0_s", sa.Integer(), nullable=True),
        sa.Column("hr_zone_1_s", sa.Integer(), nullable=True),
        sa.Column("hr_zone_2_s", sa.Integer(), nullable=True),
        sa.Column("hr_zone_3_s", sa.Integer(), nullable=True),
        sa.Column("hr_zone_4_s", sa.Integer(), nullable=True),
        sa.Column("hr_zone_5_s", sa.Integer(), nullable=True),
        sa.Column("power_level_1_s", sa.Integer(), nullable=True),
        sa.Column("power_level_2_s", sa.Integer(), nullable=True),
        sa.Column("power_level_3_s", sa.Integer(), nullable=True),
        sa.Column("power_level_4_s", sa.Integer(), nullable=True),
        sa.Column("power_level_5_s", sa.Integer(), nullable=True),
        sa.Column("power_level_6_s", sa.Integer(), nullable=True),
        sa.Column("power_level_7_s", sa.Integer(), nullable=True),
        sa.Column("three_zone_low_s", sa.Integer(), nullable=True),
        sa.Column("three_zone_mid_s", sa.Integer(), nullable=True),
        sa.Column("three_zone_high_s", sa.Integer(), nullable=True),
        sa.Column("efficiency_factor", sa.Float(), nullable=True),
        sa.Column("ef_basis", sa.String(length=8), nullable=True),
        sa.Column("decoupling", sa.Float(), nullable=True),
        sa.Column("ftp_w", sa.Float(), nullable=True),
        sa.Column("lthr_bpm", sa.Float(), nullable=True),
        sa.Column("max_hr_bpm", sa.Float(), nullable=True),
        sa.Column("resting_hr_bpm", sa.Float(), nullable=True),
        sa.Column("resting_hr_source", sa.String(length=16), nullable=True),
        sa.Column("hr_zones_sport", sa.String(length=16), nullable=True),
        sa.Column("hr_zones_valid_from", sa.Date(), nullable=True),
        sa.Column("thresholds_backfilled", sa.Boolean(), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
        sa.CheckConstraint(
            "sport_group IN ('run', 'ride', 'ski', 'strength', 'other')", name=op.f("ck_activity_metrics_sport_group")
        ),
        sa.CheckConstraint("load_method IN ('power', 'hr', 'none')", name=op.f("ck_activity_metrics_load_method")),
        sa.CheckConstraint("ef_basis IN ('power', 'speed')", name=op.f("ck_activity_metrics_ef_basis")),
        sa.CheckConstraint(
            "resting_hr_source IN ('manual', 'garmin_zones', 'activity', 'daily_summary')",
            name=op.f("ck_activity_metrics_resting_hr_source"),
        ),
        sa.CheckConstraint(
            "hr_zones_sport IN ('DEFAULT', 'RUNNING', 'CYCLING')", name=op.f("ck_activity_metrics_hr_zones_sport")
        ),
        sa.ForeignKeyConstraint(
            ["athlete_id", "activity_id"],
            ["activity.athlete_id", "activity.activity_id"],
            name="fk_activity_metrics_activity",
            ondelete="CASCADE",
        ),
        sa.ForeignKeyConstraint(
            ["athlete_id"], ["athlete.id"], name=op.f("fk_activity_metrics_athlete_id_athlete"), ondelete="CASCADE"
        ),
        sa.PrimaryKeyConstraint("athlete_id", "activity_id", name=op.f("pk_activity_metrics")),
    )
    op.create_index("ix_activity_metrics_date", "activity_metrics", ["athlete_id", "activity_date"], unique=False)
    _grant_app_role(_app_role())


def downgrade() -> None:
    op.drop_index("ix_activity_metrics_date", table_name="activity_metrics")
    op.drop_table("activity_metrics")


def _app_role() -> str:
    config = op.get_context().config
    role = config.attributes.get("app_role") if config is not None else None
    role = role if isinstance(role, str) else get_settings().db_app_role
    if not ROLE_NAME.fullmatch(role):  # interpolated into SQL below
        raise ValueError("invalid app role name")
    return role


def _grant_app_role(role: str) -> None:
    """Data access on the new table, nothing more; skipped with a notice when the role doesn't
    exist (as in 0001). Role names can't be bind parameters; `role` is validated against
    ROLE_NAME and the table names are constants. Copied from 0006 rather than shared, so a
    later change to a helper can't alter what an applied migration did."""
    tables = ", ".join(NEW_TABLES)
    op.execute(
        f"""
        DO $$
        BEGIN
          IF EXISTS (SELECT 1 FROM pg_roles WHERE rolname = '{role}') THEN
            GRANT SELECT, INSERT, UPDATE, DELETE ON {tables} TO {role};
          ELSE
            RAISE NOTICE 'role % does not exist; no grants applied', '{role}';
          END IF;
        END $$;
        """  # noqa: S608 - role validated against ROLE_NAME; table names are constants
    )

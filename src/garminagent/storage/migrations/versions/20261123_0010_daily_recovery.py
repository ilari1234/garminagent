"""Phase 2 M6e: HRV and resting HR trends against baselines, with flags.

`daily_recovery` holds one row per day, rebuilt by every `analyze` from `hrv_summary` and
`daily_summary` (ADR 0013, owner's decisions on M6e). Nothing needs filling for history: the
next `analyze` writes the whole series. Trend columns hold codes only, and a trend exists
exactly when its recent mean and baseline do (CHECK constraints).

Like 0006 to 0009, this migration grants the app role explicitly (storage contract,
invariant 7): data access on the table, nothing more.

Revision ID: 0010
Revises: 0009
Create Date: 2026-11-23 12:00:00.000000
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

from garminagent.config import ROLE_NAME, get_settings

revision: str = "0010"
down_revision: str | None = "0009"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

NEW_TABLES = ("daily_recovery",)


def upgrade() -> None:
    op.create_table(
        "daily_recovery",
        sa.Column("athlete_id", sa.Integer(), nullable=False),
        sa.Column("calendar_date", sa.Date(), nullable=False),
        sa.Column("algo_version", sa.SmallInteger(), nullable=False),
        sa.Column("hrv_days", sa.SmallInteger(), nullable=False),
        sa.Column("hrv_ln_mean", sa.Float(), nullable=True),
        sa.Column("hrv_baseline_days", sa.SmallInteger(), nullable=False),
        sa.Column("hrv_baseline_ln_mean", sa.Float(), nullable=True),
        sa.Column("hrv_baseline_ln_sd", sa.Float(), nullable=True),
        sa.Column("hrv_trend", sa.String(length=8), nullable=True),
        sa.Column("rhr_days", sa.SmallInteger(), nullable=False),
        sa.Column("rhr_mean", sa.Float(), nullable=True),
        sa.Column("rhr_baseline_days", sa.SmallInteger(), nullable=False),
        sa.Column("rhr_baseline_mean", sa.Float(), nullable=True),
        sa.Column("rhr_baseline_sd", sa.Float(), nullable=True),
        sa.Column("rhr_trend", sa.String(length=8), nullable=True),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
        sa.CheckConstraint("hrv_trend IN ('below', 'within', 'above')", name=op.f("ck_daily_recovery_hrv_trend")),
        sa.CheckConstraint("rhr_trend IN ('below', 'within', 'above')", name=op.f("ck_daily_recovery_rhr_trend")),
        sa.CheckConstraint(
            "(hrv_trend IS NULL) = (hrv_ln_mean IS NULL OR hrv_baseline_ln_mean IS NULL OR hrv_baseline_ln_sd IS NULL)",
            name=op.f("ck_daily_recovery_hrv_trend_inputs"),
        ),
        sa.CheckConstraint(
            "(rhr_trend IS NULL) = (rhr_mean IS NULL OR rhr_baseline_mean IS NULL OR rhr_baseline_sd IS NULL)",
            name=op.f("ck_daily_recovery_rhr_trend_inputs"),
        ),
        sa.ForeignKeyConstraint(
            ["athlete_id"], ["athlete.id"], name=op.f("fk_daily_recovery_athlete_id_athlete"), ondelete="CASCADE"
        ),
        sa.PrimaryKeyConstraint("athlete_id", "calendar_date", name=op.f("pk_daily_recovery")),
    )
    _grant_app_role(_app_role())


def downgrade() -> None:
    op.drop_table("daily_recovery")


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
    ROLE_NAME and the table name is a constant. Copied from 0008 rather than shared, so a later
    change to a helper can't alter what an applied migration did."""
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
        """  # noqa: S608 - role validated against ROLE_NAME; table name is a constant
    )

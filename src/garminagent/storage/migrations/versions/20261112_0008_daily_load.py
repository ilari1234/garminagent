"""Phase 2 M6c: fitness, fatigue, form and ramp per day, and a weekly view.

`daily_load` holds one row per day and sport group (`all` is the total), rebuilt by every
`analyze` from `activity_metrics.load` (ADR 0013). Nothing needs filling for history: the next
`analyze` writes the whole series.

`daily_load_weekly` is a plain view over it: ISO weeks (Monday start) with summed load, sessions
and moving time, and fitness, fatigue, form and ramp as of the week's last stored day. `days`
below 7 marks a partial week. Dates are truncated as `timestamp`, not `timestamptz`, so the
week boundary doesn't depend on the session time zone. It runs with the caller's privileges (`security_invoker`), so it
can never show more than the caller may read in `daily_load`.

Like 0006 and 0007, this migration grants the app role explicitly (storage contract,
invariant 7): data access on the table, SELECT only on the view (0001's default privileges,
which also cover views, are revoked on it first).

Revision ID: 0008
Revises: 0007
Create Date: 2026-11-12 12:00:00.000000
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

from garminagent.config import ROLE_NAME, get_settings

revision: str = "0008"
down_revision: str | None = "0007"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

NEW_TABLES = ("daily_load",)
NEW_VIEWS = ("daily_load_weekly",)

WEEKLY_VIEW = """
CREATE VIEW daily_load_weekly WITH (security_invoker = true) AS
SELECT
    athlete_id,
    sport_group,
    date_trunc('week', calendar_date::timestamp)::date AS week_start,
    count(*)::integer AS days,
    sum(load) AS load,
    sum(sessions)::integer AS sessions,
    sum(moving_s)::integer AS moving_s,
    sum(activities_unloaded)::integer AS activities_unloaded,
    sum(activities_pending)::integer AS activities_pending,
    bool_or(thresholds_backfilled) AS thresholds_backfilled,
    (array_agg(fitness ORDER BY calendar_date DESC))[1] AS fitness,
    (array_agg(fatigue ORDER BY calendar_date DESC))[1] AS fatigue,
    (array_agg(form ORDER BY calendar_date DESC))[1] AS form,
    (array_agg(ramp ORDER BY calendar_date DESC))[1] AS ramp,
    (array_agg(fitness_reliable ORDER BY calendar_date DESC))[1] AS fitness_reliable
FROM daily_load
GROUP BY athlete_id, sport_group, date_trunc('week', calendar_date::timestamp)
"""


def upgrade() -> None:
    op.create_table(
        "daily_load",
        sa.Column("athlete_id", sa.Integer(), nullable=False),
        sa.Column("calendar_date", sa.Date(), nullable=False),
        sa.Column("sport_group", sa.String(length=16), nullable=False),
        sa.Column("algo_version", sa.SmallInteger(), nullable=False),
        sa.Column("load", sa.Float(), nullable=False),
        sa.Column("sessions", sa.Integer(), nullable=False),
        sa.Column("moving_s", sa.Integer(), nullable=False),
        sa.Column("activities_unloaded", sa.Integer(), nullable=False),
        sa.Column("activities_pending", sa.Integer(), nullable=False),
        sa.Column("thresholds_backfilled", sa.Boolean(), nullable=False),
        sa.Column("fitness", sa.Float(), nullable=False),
        sa.Column("fatigue", sa.Float(), nullable=False),
        sa.Column("form", sa.Float(), nullable=False),
        sa.Column("ramp", sa.Float(), nullable=True),
        sa.Column("history_days", sa.Integer(), nullable=False),
        sa.Column("fitness_reliable", sa.Boolean(), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
        sa.CheckConstraint(
            "sport_group IN ('all', 'run', 'ride', 'ski', 'strength', 'other')",
            name=op.f("ck_daily_load_sport_group"),
        ),
        sa.ForeignKeyConstraint(
            ["athlete_id"], ["athlete.id"], name=op.f("fk_daily_load_athlete_id_athlete"), ondelete="CASCADE"
        ),
        sa.PrimaryKeyConstraint("athlete_id", "calendar_date", "sport_group", name=op.f("pk_daily_load")),
    )
    op.execute(WEEKLY_VIEW)
    _grant_app_role(_app_role())


def downgrade() -> None:
    op.execute("DROP VIEW daily_load_weekly")
    op.drop_table("daily_load")


def _app_role() -> str:
    config = op.get_context().config
    role = config.attributes.get("app_role") if config is not None else None
    role = role if isinstance(role, str) else get_settings().db_app_role
    if not ROLE_NAME.fullmatch(role):  # interpolated into SQL below
        raise ValueError("invalid app role name")
    return role


def _grant_app_role(role: str) -> None:
    """Data access on the new table and SELECT on the view, nothing more; skipped with a notice
    when the role doesn't exist (as in 0001). Role names can't be bind parameters; `role` is
    validated against ROLE_NAME and the relation names are constants. Copied from 0007 rather
    than shared, so a later change to a helper can't alter what an applied migration did."""
    tables = ", ".join(NEW_TABLES)
    views = ", ".join(NEW_VIEWS)
    op.execute(
        f"""
        DO $$
        BEGIN
          IF EXISTS (SELECT 1 FROM pg_roles WHERE rolname = '{role}') THEN
            GRANT SELECT, INSERT, UPDATE, DELETE ON {tables} TO {role};
            -- 0001's default privileges cover views too; a rollup is read-only.
            REVOKE ALL ON {views} FROM {role};
            GRANT SELECT ON {views} TO {role};
          ELSE
            RAISE NOTICE 'role % does not exist; no grants applied', '{role}';
          END IF;
        END $$;
        """  # noqa: S608 - role validated against ROLE_NAME; relation names are constants
    )

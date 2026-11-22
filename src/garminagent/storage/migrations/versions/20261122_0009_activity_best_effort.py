"""Phase 2 M6d: best efforts per activity, and the curve and PB views.

`activity_best_effort` holds one row per activity, kind (`distance`, `power`) and span (metres
or seconds), written by `analyze` (ADR 0013). Nothing needs filling for history: `analyze`
computes missing rows by fingerprint, capped per run like `activity_metrics`.

Two plain views over it, both `security_invoker`, so they never show more than the caller may
read in the table:

- `best_effort_curve`: per group, kind and span, the best value with its activity and date, for
  all time (`period_days` NULL) and for the last 365, 90 and 42 days counted back from
  `CURRENT_DATE`, which is read when the view is queried, in the session's time zone.
- `best_effort_pb`: the all-time best per group, kind and span, and the best set before that
  day (`previous_*`, NULL for a first effort).

Ties go to the earlier date, then the lower activity ID, so a PB belongs to whoever set it
first. Like 0006-0008, this migration grants the app role explicitly (storage contract,
invariant 7): data access on the table, SELECT only on the views (0001's default privileges,
which also cover views, are revoked on them first).

Revision ID: 0009
Revises: 0008
Create Date: 2026-11-22 12:00:00.000000
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

from garminagent.config import ROLE_NAME, get_settings

revision: str = "0009"
down_revision: str | None = "0008"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

NEW_TABLES = ("activity_best_effort",)
NEW_VIEWS = ("best_effort_curve", "best_effort_pb")

# Best first: the shortest time for distance rows (power_w is NULL in all of them), the highest
# power for power rows (seconds is NULL in all of them); ties to the earliest date.
BEST_FIRST = "seconds ASC NULLS LAST, power_w DESC NULLS LAST, activity_date, activity_id"

CURVE_VIEW = f"""
CREATE VIEW best_effort_curve WITH (security_invoker = true) AS
WITH periods (period_days) AS (VALUES (NULL::integer), (365), (90), (42))
SELECT DISTINCT ON (e.athlete_id, e.sport_group, e.kind, e.span, p.period_days)
    e.athlete_id,
    e.sport_group,
    e.kind,
    e.span,
    p.period_days,
    e.seconds,
    e.power_w,
    e.activity_id,
    e.activity_date
FROM activity_best_effort AS e
CROSS JOIN periods AS p
WHERE (e.seconds IS NOT NULL OR e.power_w IS NOT NULL)
  AND (p.period_days IS NULL OR e.activity_date > CURRENT_DATE - p.period_days)
ORDER BY e.athlete_id, e.sport_group, e.kind, e.span, p.period_days, {BEST_FIRST}
"""  # noqa: S608 - constants only

PB_VIEW = f"""
CREATE VIEW best_effort_pb WITH (security_invoker = true) AS
WITH best AS (
    SELECT DISTINCT ON (athlete_id, sport_group, kind, span)
        athlete_id, sport_group, kind, span, seconds, power_w, activity_id, activity_date
    FROM activity_best_effort
    WHERE seconds IS NOT NULL OR power_w IS NOT NULL
    ORDER BY athlete_id, sport_group, kind, span, {BEST_FIRST}
)
SELECT
    b.athlete_id,
    b.sport_group,
    b.kind,
    b.span,
    b.seconds,
    b.power_w,
    b.activity_id,
    b.activity_date,
    prev.seconds AS previous_seconds,
    prev.power_w AS previous_power_w,
    prev.activity_id AS previous_activity_id,
    prev.activity_date AS previous_activity_date
FROM best AS b
LEFT JOIN LATERAL (
    SELECT seconds, power_w, activity_id, activity_date
    FROM activity_best_effort AS e
    WHERE e.athlete_id = b.athlete_id
      AND e.sport_group = b.sport_group
      AND e.kind = b.kind
      AND e.span = b.span
      AND e.activity_date < b.activity_date
      AND (e.seconds IS NOT NULL OR e.power_w IS NOT NULL)
    ORDER BY {BEST_FIRST}
    LIMIT 1
) AS prev ON true
"""  # noqa: S608 - constants only


def upgrade() -> None:
    op.create_table(
        "activity_best_effort",
        sa.Column("athlete_id", sa.Integer(), nullable=False),
        sa.Column("activity_id", sa.BigInteger(), nullable=False),
        sa.Column("kind", sa.String(length=8), nullable=False),
        sa.Column("span", sa.Integer(), nullable=False),
        sa.Column("activity_date", sa.Date(), nullable=False),
        sa.Column("sport_group", sa.String(length=16), nullable=False),
        sa.Column("seconds", sa.Float(), nullable=True),
        sa.Column("power_w", sa.Float(), nullable=True),
        sa.Column("algo_version", sa.SmallInteger(), nullable=False),
        sa.Column("input_fingerprint", sa.String(length=64), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
        sa.CheckConstraint(
            "sport_group IN ('run', 'ride', 'ski', 'strength', 'other')",
            name=op.f("ck_activity_best_effort_sport_group"),
        ),
        sa.CheckConstraint("kind IN ('distance', 'power')", name=op.f("ck_activity_best_effort_kind")),
        sa.CheckConstraint("span > 0", name=op.f("ck_activity_best_effort_span")),
        sa.CheckConstraint(
            "(kind = 'distance' AND power_w IS NULL) OR (kind = 'power' AND seconds IS NULL)",
            name=op.f("ck_activity_best_effort_value"),
        ),
        sa.ForeignKeyConstraint(
            ["athlete_id", "activity_id"],
            ["activity.athlete_id", "activity.activity_id"],
            name="fk_activity_best_effort_activity",
            ondelete="CASCADE",
        ),
        sa.ForeignKeyConstraint(
            ["athlete_id"], ["athlete.id"], name=op.f("fk_activity_best_effort_athlete_id_athlete"), ondelete="CASCADE"
        ),
        sa.PrimaryKeyConstraint("athlete_id", "activity_id", "kind", "span", name=op.f("pk_activity_best_effort")),
    )
    op.create_index(
        "ix_activity_best_effort_curve",
        "activity_best_effort",
        ["athlete_id", "sport_group", "kind", "span"],
        unique=False,
    )
    op.execute(CURVE_VIEW)
    op.execute(PB_VIEW)
    _grant_app_role(_app_role())


def downgrade() -> None:
    op.execute("DROP VIEW best_effort_pb")
    op.execute("DROP VIEW best_effort_curve")
    op.drop_index("ix_activity_best_effort_curve", table_name="activity_best_effort")
    op.drop_table("activity_best_effort")


def _app_role() -> str:
    config = op.get_context().config
    role = config.attributes.get("app_role") if config is not None else None
    role = role if isinstance(role, str) else get_settings().db_app_role
    if not ROLE_NAME.fullmatch(role):  # interpolated into SQL below
        raise ValueError("invalid app role name")
    return role


def _grant_app_role(role: str) -> None:
    """Data access on the new table and SELECT on the views, nothing more; skipped with a notice
    when the role doesn't exist (as in 0001). Role names can't be bind parameters; `role` is
    validated against ROLE_NAME and the relation names are constants. Copied from 0008 rather
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

"""`garminagent db-bootstrap`: create the database roles where compose's init script doesn't run.

On RDS nothing plays the part of `docker/postgres/init`, and migrations must not run as the
master user (ADR 0008, B3b). So this logs in with the master credentials (DB_MASTER_USERNAME,
DB_MASTER_PASSWORD; host, port and database from MIGRATION_DATABASE_URL) and, in one
transaction:

- creates the schema owner named in MIGRATION_DATABASE_URL and the app role named in
  DATABASE_URL, each with its URL's password, both `NOSUPERUSER NOCREATEDB NOCREATEROLE
  NOREPLICATION NOBYPASSRLS` like the compose app role;
- takes CONNECT on the database away from PUBLIC and gives it to both roles, as the init script
  does for the app role;
- lets the owner create objects in schema `public` (PostgreSQL 15+ grants that to nobody but
  the database owner, which is the master here). `WITH GRANT OPTION`, so migration 0001 can
  still grant the app role USAGE on the schema, unchanged.

Table grants stay with the migrations, run afterwards by `db-upgrade` as the owner. A re-run
sets each role's password from its URL and changes nothing else about an existing role; it
refuses a role with elevated attributes rather than fix it silently.

Passwords never reach the server in clear: libpq turns each into a SCRAM-SHA-256 verifier on
the client (as `psql`'s `\\password` does), and the verifier is sent as a quoted literal.
Neither the passwords nor the verifiers are logged, printed or put in an error message.
"""

import logging
from dataclasses import dataclass
from typing import Any, Literal, cast

import psycopg
from psycopg import sql
from pydantic import SecretStr
from sqlalchemy import URL, make_url

from garminagent.config import ROLE_NAME, Settings
from garminagent.storage.db import make_engine

log = logging.getLogger(__name__)

# Attributes no role made here may have. A re-run checks existing roles against them.
ELEVATED = ("rolsuper", "rolcreaterole", "rolcreatedb", "rolreplication", "rolbypassrls")

RoleKind = Literal["owner", "member"]


class BootstrapError(ValueError):
    """The settings or the server's state don't allow a safe bootstrap. Never carries secrets."""


@dataclass(frozen=True)
class LoginRole:
    """A role `db-bootstrap` creates. `owner` may create objects in schema `public`; a `member`
    gets CONNECT only, and its table access from the migrations."""

    name: str
    password: SecretStr
    kind: RoleKind
    setting: str  # where name and password come from, for messages


@dataclass(frozen=True)
class BootstrapPlan:
    master_url: URL
    database: str
    roles: tuple[LoginRole, ...]


def plan(settings: Settings) -> BootstrapPlan:
    """Check the settings and work out what to create, without connecting."""
    if settings.db_master_username is None or settings.db_master_password is None:
        raise BootstrapError("db-bootstrap needs DB_MASTER_USERNAME and DB_MASTER_PASSWORD")
    master = settings.db_master_username.get_secret_value()
    owner_url = make_url(settings.migration_database_url.get_secret_value())
    roles = [
        _role_from_url(owner_url, "MIGRATION_DATABASE_URL", "owner"),
        _role_from_url(make_url(settings.database_url.get_secret_value()), "DATABASE_URL", "member"),
        # M6f adds `garminagent_agent` here: a third `member`, its name and grants from the one
        # `garminagent.storage` module M6f creates (agreed with track A), its password from
        # AGENT_DATABASE_URL. Its table grants come from M6f's migration, like the app role's.
    ]
    app = roles[1]
    if app.name != settings.db_app_role:
        raise BootstrapError(
            f"DATABASE_URL's user {app.name} is not DB_APP_ROLE ({settings.db_app_role}), which the migrations grant to"
        )
    names = [role.name for role in roles]
    if len(set(names)) != len(names):
        raise BootstrapError("MIGRATION_DATABASE_URL and DATABASE_URL must name different users")
    if master in names:
        raise BootstrapError("DB_MASTER_USERNAME must differ from the database URLs' users: nothing runs as the master")
    if not owner_url.database:
        raise BootstrapError("MIGRATION_DATABASE_URL names no database")
    app_url = make_url(settings.database_url.get_secret_value())
    if (app_url.host, app_url.port, app_url.database) != (owner_url.host, owner_url.port, owner_url.database):
        raise BootstrapError("DATABASE_URL and MIGRATION_DATABASE_URL must point at the same host, port and database")
    master_url = owner_url.set(username=master, password=settings.db_master_password.get_secret_value())
    return BootstrapPlan(master_url=master_url, database=owner_url.database, roles=tuple(roles))


def bootstrap(settings: Settings) -> dict[str, str]:
    """Create or update the roles; returns each role's outcome (`created` or `password_updated`)."""
    steps = plan(settings)
    master_url = steps.master_url.render_as_string(hide_password=False)
    engine = make_engine(master_url, require_verify_full=settings.db_require_verify_full)
    try:
        with engine.begin() as conn:
            raw = cast("psycopg.Connection[Any]", conn.connection.driver_connection)
            with raw.cursor() as cur:
                outcome = {role.name: _ensure_role(raw, cur, role) for role in steps.roles}
                _grant(cur, steps)
                _verify(cur, steps)
    finally:
        engine.dispose()
    for name, result in outcome.items():
        log.info("role %s: %s", name, result.replace("_", " "))
    return outcome


def _role_from_url(url: URL, setting: str, kind: RoleKind) -> LoginRole:
    if not url.username or not url.password:
        raise BootstrapError(f"{setting} must contain a user and a password")
    if not ROLE_NAME.fullmatch(url.username):
        raise BootstrapError(f"{setting}'s user must be a lowercase identifier (letters, digits, underscore)")
    return LoginRole(name=url.username, password=SecretStr(str(url.password)), kind=kind, setting=setting)


def _ensure_role(raw: psycopg.Connection[Any], cur: psycopg.Cursor[Any], role: LoginRole) -> str:
    cur.execute(
        sql.SQL("SELECT rolcanlogin, {} FROM pg_roles WHERE rolname = %s").format(
            sql.SQL(", ").join(sql.Identifier(column) for column in ELEVATED)
        ),
        (role.name,),
    )
    row = cur.fetchone()
    verifier = raw.pgconn.encrypt_password(
        role.password.get_secret_value().encode(), role.name.encode(), b"scram-sha-256"
    ).decode()
    if row is None:
        cur.execute(
            sql.SQL(
                "CREATE ROLE {} WITH LOGIN NOSUPERUSER NOCREATEDB NOCREATEROLE NOREPLICATION NOBYPASSRLS PASSWORD {}"
            ).format(sql.Identifier(role.name), sql.Literal(verifier))
        )
        return "created"
    can_login, *elevated = row
    if not can_login or any(elevated):
        raise BootstrapError(
            f"role {role.name} exists with NOLOGIN or elevated attributes; db-bootstrap changes only passwords"
            " of existing roles, so fix it by hand"
        )
    cur.execute(sql.SQL("ALTER ROLE {} WITH PASSWORD {}").format(sql.Identifier(role.name), sql.Literal(verifier)))
    return "password_updated"


def _grant(cur: psycopg.Cursor[Any], steps: BootstrapPlan) -> None:
    database = sql.Identifier(steps.database)
    owners = [sql.Identifier(role.name) for role in steps.roles if role.kind == "owner"]
    everyone = sql.SQL(", ").join(sql.Identifier(role.name) for role in steps.roles)
    cur.execute(sql.SQL("REVOKE ALL ON DATABASE {} FROM PUBLIC").format(database))
    cur.execute(sql.SQL("GRANT CONNECT ON DATABASE {} TO {}").format(database, everyone))
    cur.execute(sql.SQL("REVOKE CREATE ON SCHEMA public FROM PUBLIC"))
    for owner in owners:
        cur.execute(sql.SQL("GRANT USAGE, CREATE ON SCHEMA public TO {} WITH GRANT OPTION").format(owner))


def _verify(cur: psycopg.Cursor[Any], steps: BootstrapPlan) -> None:
    """GRANT and REVOKE only warn when the master lacks the right to them (e.g. it doesn't own
    the database), so check the result instead of trusting them."""
    problems: list[str] = []
    cur.execute("SELECT has_database_privilege('public', %s, 'CONNECT')", (steps.database,))
    if _scalar(cur):
        problems.append("PUBLIC can still connect")
    for role in steps.roles:
        cur.execute("SELECT has_database_privilege(%s, %s, 'CONNECT')", (role.name, steps.database))
        if not _scalar(cur):
            problems.append(f"{role.name} can't connect")
        if role.kind == "owner":
            cur.execute("SELECT has_schema_privilege(%s, 'public', 'CREATE WITH GRANT OPTION')", (role.name,))
            if not _scalar(cur):
                problems.append(f"{role.name} can't create in schema public")
    if problems:
        raise BootstrapError(
            f"grants did not take effect ({'; '.join(problems)}); does the master user own database {steps.database}?"
        )


def _scalar(cur: psycopg.Cursor[Any]) -> object:
    row = cur.fetchone()
    return row[0] if row else None

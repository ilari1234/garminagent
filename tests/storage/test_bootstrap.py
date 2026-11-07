"""`db-bootstrap`: the settings checks need no database; the rest runs against TEST_DATABASE_URL
with an RDS-like master (CREATEROLE and CREATEDB, not a superuser, owner of a throwaway
database), so it proves the roles work without superuser help, as on RDS."""

import json
import logging
import os
import secrets
from collections.abc import Iterator
from dataclasses import dataclass

import pytest
from pydantic import SecretStr
from sqlalchemy import URL, create_engine, make_url, text
from sqlalchemy.exc import OperationalError, ProgrammingError
from typer.testing import CliRunner

from garminagent.cli import app
from garminagent.config import Settings
from garminagent.storage import migrate
from garminagent.storage.bootstrap import BootstrapError, bootstrap, plan
from garminagent.storage.db import InsecureDatabaseURL, make_engine, make_session_factory
from garminagent.storage.repository import ensure_athlete

# For parametrize lists, which are built at import; generated, so no credential-shaped literal.
PARAM_PASSWORD = secrets.token_hex(8)


def url(
    user: str,
    password: str,
    *,
    host: str = "127.0.0.1",
    port: int = 5432,
    database: str = "ga",
    query: dict[str, str] | None = None,
) -> str:
    return URL.create(
        "postgresql+psycopg",
        username=user,
        password=password,
        host=host,
        port=port,
        database=database,
        query=query or {},
    ).render_as_string(hide_password=False)


def settings_for(
    owner_url: str,
    app_url: str,
    *,
    master: str = "ga_master",
    app_role: str = "ga_app",
    require_verify_full: bool = False,
) -> Settings:
    return Settings(
        migration_database_url=SecretStr(owner_url),
        database_url=SecretStr(app_url),
        db_app_role=app_role,
        db_master_username=SecretStr(master),
        db_master_password=SecretStr(secrets.token_hex(16)),
        db_require_verify_full=require_verify_full,
    )


# --- settings checks, no database ---


def test_plan_takes_names_and_passwords_from_the_urls() -> None:
    owner_pw, app_pw = secrets.token_hex(16), secrets.token_hex(16)
    steps = plan(settings_for(url("ga_owner", owner_pw), url("ga_app", app_pw)))
    assert [(r.name, r.kind, r.password.get_secret_value()) for r in steps.roles] == [
        ("ga_owner", "owner", owner_pw),
        ("ga_app", "member", app_pw),
    ]
    assert steps.database == "ga"
    assert steps.master_url.username == "ga_master"
    assert (steps.master_url.host, steps.master_url.port) == ("127.0.0.1", 5432)


def test_plan_keeps_the_owner_urls_tls_options_for_the_master() -> None:
    query = {"sslmode": "verify-full", "sslrootcert": "/app/certs/rds-global-bundle.pem"}
    pw = secrets.token_hex(8)
    steps = plan(settings_for(url("ga_owner", pw, query=query), url("ga_app", pw, query=query)))
    assert dict(steps.master_url.query) == query


@pytest.mark.parametrize(
    ("owner", "app_user", "master", "app_role", "message"),
    [
        ("ga_owner", "ga_app", "ga_owner", "ga_app", "DB_MASTER_USERNAME must differ"),
        ("ga_owner", "ga_app", "ga_app", "ga_app", "DB_MASTER_USERNAME must differ"),
        ("ga_app", "ga_app", "ga_master", "ga_app", "different users"),
        ("ga_owner", "ga_app", "ga_master", "other_app", "is not DB_APP_ROLE"),
        ("GA-Owner", "ga_app", "ga_master", "ga_app", "lowercase identifier"),
        ("ga_owner", "ga_app; drop", "ga_master", "ga_app", "lowercase identifier"),
    ],
)
def test_plan_refuses_unsafe_role_settings(owner: str, app_user: str, master: str, app_role: str, message: str) -> None:
    pw = secrets.token_hex(8)
    with pytest.raises(BootstrapError, match=message):
        plan(settings_for(url(owner, pw), url(app_user, pw), master=master, app_role=app_role))


def test_plan_needs_master_credentials() -> None:
    pw = secrets.token_hex(8)
    settings = Settings(
        migration_database_url=SecretStr(url("ga_owner", pw)),
        database_url=SecretStr(url("ga_app", pw)),
        db_app_role="ga_app",
    )
    with pytest.raises(BootstrapError, match="DB_MASTER_USERNAME and DB_MASTER_PASSWORD"):
        plan(settings)


def test_plan_needs_a_password_in_each_url() -> None:
    no_password = URL.create("postgresql+psycopg", username="ga_app", host="127.0.0.1", database="ga")
    with pytest.raises(BootstrapError, match="DATABASE_URL must contain a user and a password"):
        plan(settings_for(url("ga_owner", secrets.token_hex(8)), no_password.render_as_string()))


@pytest.mark.parametrize(
    "app_url",
    [
        url("ga_app", PARAM_PASSWORD, host="other.example.test"),
        url("ga_app", PARAM_PASSWORD, port=5433),
        url("ga_app", PARAM_PASSWORD, database="other"),
    ],
)
def test_plan_needs_both_urls_on_the_same_database(app_url: str) -> None:
    with pytest.raises(BootstrapError, match="same host, port and database"):
        plan(settings_for(url("ga_owner", secrets.token_hex(8)), app_url))


def test_bootstrap_obeys_the_tls_guard_before_connecting() -> None:
    pw = secrets.token_hex(8)
    tls = {"sslmode": "require"}
    settings = settings_for(
        url("ga_owner", pw, host="rds.example.test", query=tls),
        url("ga_app", pw, host="rds.example.test", query=tls),
        require_verify_full=True,
    )
    with pytest.raises(InsecureDatabaseURL):
        bootstrap(settings)


def test_refusals_print_no_secrets(monkeypatch: pytest.MonkeyPatch, caplog: pytest.LogCaptureFixture) -> None:
    owner_pw, master_pw = secrets.token_hex(16), secrets.token_hex(16)
    monkeypatch.setenv("MIGRATION_DATABASE_URL", url("ga_owner", owner_pw))
    monkeypatch.setenv("DATABASE_URL", url("ga_app", owner_pw))  # same user check comes later
    monkeypatch.setenv("DB_APP_ROLE", "ga_app")
    monkeypatch.setenv("DB_MASTER_USERNAME", "ga_owner")
    monkeypatch.setenv("DB_MASTER_PASSWORD", master_pw)
    with caplog.at_level(logging.DEBUG):
        result = CliRunner().invoke(app, ["db-bootstrap"])
    assert result.exit_code == 2
    for secret in (owner_pw, master_pw):
        assert secret not in result.output
        assert secret not in caplog.text


# --- against Postgres ---


@dataclass(frozen=True)
class RdsLike:
    """A throwaway database owned by a non-superuser master, as RDS creates it."""

    server: URL
    database: str
    master: str
    master_password: str
    owner: str
    app: str

    def role_url(self, user: str, password: str) -> str:
        return self.server.set(database=self.database, username=user, password=password).render_as_string(
            hide_password=False
        )

    def settings(self, owner_password: str, app_password: str) -> Settings:
        return Settings(
            migration_database_url=SecretStr(self.role_url(self.owner, owner_password)),
            database_url=SecretStr(self.role_url(self.app, app_password)),
            db_app_role=self.app,
            db_master_username=SecretStr(self.master),
            db_master_password=SecretStr(self.master_password),
        )


@pytest.fixture
def rds_like() -> Iterator[RdsLike]:
    server_url = os.environ.get("TEST_DATABASE_URL")
    if not server_url:
        pytest.skip("TEST_DATABASE_URL not set")
    suffix = secrets.token_hex(4)
    env = RdsLike(
        server=make_url(server_url),
        database=f"ga_boot_{suffix}",
        master=f"ga_master_{suffix}",
        master_password=secrets.token_hex(16),
        owner=f"ga_owner_{suffix}",
        app=f"ga_app_{suffix}",
    )
    admin = create_engine(server_url, isolation_level="AUTOCOMMIT")
    # Generated hex names and password; DDL can't take bind parameters.
    with admin.connect() as conn:
        conn.execute(text(f"CREATE ROLE {env.master} LOGIN CREATEROLE CREATEDB PASSWORD '{env.master_password}'"))
        conn.execute(text(f'CREATE DATABASE "{env.database}" OWNER {env.master}'))
    try:
        yield env
    finally:
        with admin.connect() as conn:
            conn.execute(text(f'DROP DATABASE IF EXISTS "{env.database}" WITH (FORCE)'))
            for role in (env.owner, env.app, env.master):
                conn.execute(text(f"DROP ROLE IF EXISTS {role}"))
        admin.dispose()


def connects(role_url: str) -> bool:
    engine = create_engine(role_url, connect_args={"connect_timeout": 5})
    try:
        with engine.connect() as conn:
            conn.execute(text("SELECT 1"))
        return True
    except OperationalError:
        return False
    finally:
        engine.dispose()


def admin_scalar(env: RdsLike, query: str, **params: object) -> object:
    engine = create_engine(env.server.set(database=env.database))
    try:
        with engine.connect() as conn:
            return conn.execute(text(query), params).scalar_one()
    finally:
        engine.dispose()


@pytest.mark.integration
def test_bootstrap_then_migrations_work_without_a_superuser(
    rds_like: RdsLike, caplog: pytest.LogCaptureFixture
) -> None:
    env = rds_like
    owner_pw, app_pw = secrets.token_hex(16), secrets.token_hex(16)
    with caplog.at_level(logging.DEBUG):
        assert bootstrap(env.settings(owner_pw, app_pw)) == {env.owner: "created", env.app: "created"}

    # The owner runs the existing migrations unchanged, and they grant the app role as on compose.
    owner_url = env.role_url(env.owner, owner_pw)
    migrate.upgrade(owner_url, env.app)
    assert migrate.current_revision(owner_url) == migrate.head_revision()
    app_engine = make_engine(env.role_url(env.app, app_pw))
    sessions = make_session_factory(app_engine)
    try:
        with sessions.begin() as session:
            ensure_athlete(session)
        with sessions() as session, pytest.raises(ProgrammingError, match="permission denied"):
            session.execute(text("CREATE TABLE intruder (id int)"))
    finally:
        app_engine.dispose()

    for role in (env.owner, env.app):
        attributes = admin_scalar(
            env,
            "SELECT rolcanlogin AND NOT (rolsuper OR rolcreaterole OR rolcreatedb OR rolreplication OR rolbypassrls)"
            " FROM pg_roles WHERE rolname = :r",
            r=role,
        )
        assert attributes is True, f"{role} has unexpected attributes"
    assert admin_scalar(env, "SELECT has_database_privilege('public', :d, 'CONNECT')", d=env.database) is False
    assert admin_scalar(env, "SELECT rolpassword LIKE 'SCRAM-SHA-256$%' FROM pg_authid WHERE rolname = :r", r=env.app)

    logged = caplog.text
    for secret in (owner_pw, app_pw, env.master_password, "SCRAM-SHA-256"):
        assert secret not in logged


@pytest.mark.integration
def test_rerun_updates_passwords_and_nothing_else(rds_like: RdsLike) -> None:
    env = rds_like
    old_owner, old_app = secrets.token_hex(16), secrets.token_hex(16)
    bootstrap(env.settings(old_owner, old_app))
    migrate.upgrade(env.role_url(env.owner, old_owner), env.app)
    admin = create_engine(env.server.set(database=env.database), isolation_level="AUTOCOMMIT")
    with admin.connect() as conn:  # a change made by hand, which a re-run must keep
        conn.execute(text(f"ALTER ROLE {env.app} CONNECTION LIMIT 7"))
    admin.dispose()

    new_owner, new_app = secrets.token_hex(16), secrets.token_hex(16)
    outcome = bootstrap(env.settings(new_owner, new_app))
    assert outcome == {env.owner: "password_updated", env.app: "password_updated"}
    assert connects(env.role_url(env.app, new_app))
    assert connects(env.role_url(env.owner, new_owner))
    assert not connects(env.role_url(env.app, old_app))
    assert admin_scalar(env, "SELECT rolconnlimit FROM pg_roles WHERE rolname = :r", r=env.app) == 7
    # The migrated schema and the app role's grants are untouched.
    assert admin_scalar(env, "SELECT has_table_privilege(:r, 'athlete', 'INSERT')", r=env.app) is True
    assert migrate.current_revision(env.role_url(env.owner, new_owner)) == migrate.head_revision()


@pytest.mark.integration
def test_refuses_an_existing_role_with_elevated_attributes_and_changes_nothing(rds_like: RdsLike) -> None:
    env = rds_like
    admin = create_engine(env.server.set(database=env.database), isolation_level="AUTOCOMMIT")
    with admin.connect() as conn:
        conn.execute(text(f"CREATE ROLE {env.app} LOGIN CREATEDB"))
    admin.dispose()
    with pytest.raises(BootstrapError, match=f"role {env.app} exists"):
        bootstrap(env.settings(secrets.token_hex(16), secrets.token_hex(16)))
    # One transaction: the owner created before the refusal was rolled back.
    assert admin_scalar(env, "SELECT count(*) FROM pg_roles WHERE rolname = :r", r=env.owner) == 0


@pytest.mark.integration
def test_cli_prints_outcomes_only(rds_like: RdsLike, monkeypatch: pytest.MonkeyPatch) -> None:
    env = rds_like
    owner_pw, app_pw = secrets.token_hex(16), secrets.token_hex(16)
    settings = env.settings(owner_pw, app_pw)
    monkeypatch.setenv("MIGRATION_DATABASE_URL", settings.migration_database_url.get_secret_value())
    monkeypatch.setenv("DATABASE_URL", settings.database_url.get_secret_value())
    monkeypatch.setenv("DB_APP_ROLE", env.app)
    monkeypatch.setenv("DB_MASTER_USERNAME", env.master)
    monkeypatch.setenv("DB_MASTER_PASSWORD", env.master_password)
    result = CliRunner().invoke(app, ["db-bootstrap"])
    assert result.exit_code == 0, result.output
    assert json.loads(result.stdout) == {"roles": {env.owner: "created", env.app: "created"}}
    for secret in (owner_pw, app_pw, env.master_password):
        assert secret not in result.output

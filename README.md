# garminagent

AI-assisted training planning on Garmin Connect data: agents create, update and evaluate
training plans for off-season training and events (half marathon to ultra, road and gravel
cycling), using your history from the Garmin Connect Personal API.

See [docs/plan.md](docs/plan.md) for decisions, architecture and milestones,
[SECURITY.md](SECURITY.md) for the threat model, controls and routines, and
[CLAUDE.md](CLAUDE.md) for the rules AI-assisted sessions follow.

## Requirements

- [uv](https://docs.astral.sh/uv/) 0.13.5 exactly (pinned by `required-version` in `pyproject.toml`;
  `uv self update 0.13.5`). uv installs Python 3.15 itself.
- Docker with Compose v2

## Setup

```bash
uv sync
cp .env.example .env   # then fill in GARMIN_PERSONAL_API_KEY
```

Create the key in Garmin Connect web on the Personal API Key page. It is shown only once.
`.env` is gitignored and never leaves your machine.

## Usage

```bash
uv run garminagent --help
uv run garminagent config      # effective settings, secrets masked
uv run garminagent db-check    # can we reach Postgres?
uv run garminagent auth-check  # does the Garmin API key work? (prints only the token expiry)
uv run garminagent db-upgrade  # apply migrations (as the schema owner)
uv run garminagent purge-data --yes  # delete ALL stored data (rows and files)
```

With Docker:

```bash
docker compose up -d db
docker compose run --rm migrate
docker compose run --rm app db-check
```

Postgres has two roles: `garminagent` owns the schema and runs migrations, and
`garminagent_app` (what the app connects as) can only read and write data. The app role is
created by `docker/postgres/init` when the database volume is first initialized; with an
older volume, recreate it once with `docker compose down -v` (this deletes local data).
There are no default passwords: set `POSTGRES_PASSWORD` and `APP_DB_PASSWORD` in `.env` first
(see `.env.example`); compose refuses to start without them.

## Configuration

All settings come from environment variables (or `.env`):

| Variable | Default | Purpose |
|---|---|---|
| `GARMIN_PERSONAL_API_KEY` | – | Personal API key |
| `GARMIN_API_KEY_EXPIRES` | – | Key expiry date (`YYYY-MM-DD`), for advance warnings |
| `DATABASE_URL` | local Postgres as `garminagent_app`, no password | App connection (data access only), `postgresql+psycopg://…` |
| `MIGRATION_DATABASE_URL` | local Postgres as `garminagent`, no password | Schema owner, used only by `db-upgrade` |
| `DB_APP_ROLE` | `garminagent_app` | Role the migrations grant data access to |
| `DATA_DIR` | `data` (`/data` in the image) | Raw activity files (`DATA_DIR/blobs`) |
| `LOG_LEVEL` | `INFO` | `DEBUG`, `INFO`, `WARNING`, `ERROR` |
| `LOG_FORMAT` | `text` (`json` in the image) | `text` or `json` |
| `GARMIN_BASE_URL` | `https://personal.connectapi.garmin.com` | Personal API base URL |
| `GARMIN_MIN_REQUEST_INTERVAL` | `1.0` | Minimum seconds between Garmin requests |
| `GARMIN_MAX_RETRIES` | `4` | Retries on 429, 5xx and network errors before giving up |

## Development

```bash
uv run ruff check
uv run ruff format --check
uv run pyright
uv run pytest
```

Database tests are marked `integration` and run only when `TEST_DATABASE_URL` is set. Each run
creates and drops its own throwaway database and app role (so the URL needs a role allowed to
create both, like the compose owner) and never touches the real database:

```bash
TEST_DATABASE_URL="postgresql+psycopg://garminagent:$POSTGRES_PASSWORD@127.0.0.1:5432/garminagent" uv run pytest
```

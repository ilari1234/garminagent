# garminagent

AI-assisted training planning on Garmin Connect data: agents create, update and evaluate
training plans for off-season training and events (half marathon to ultra, road and gravel
cycling), using your history from the Garmin Connect Personal API.

See [docs/plan.md](docs/plan.md) for decisions, architecture and milestones.

## Requirements

- [uv](https://docs.astral.sh/uv/) (installs Python 3.15 itself)
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
```

With Docker:

```bash
docker compose up -d db
docker compose run --rm app db-check
```

## Configuration

All settings come from environment variables (or `.env`):

| Variable | Default | Purpose |
|---|---|---|
| `GARMIN_PERSONAL_API_KEY` | – | Personal API key |
| `GARMIN_API_KEY_EXPIRES` | – | Key expiry date (`YYYY-MM-DD`), for advance warnings |
| `DATABASE_URL` | local Postgres | SQLAlchemy URL, `postgresql+psycopg://…` |
| `DATA_DIR` | `data` (`/data` in the image) | Raw payloads and activity files |
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

Database tests are marked `integration` and run only when `TEST_DATABASE_URL` is set, e.g.
against the compose database:

```bash
TEST_DATABASE_URL=postgresql+psycopg://garminagent:garminagent@127.0.0.1:5432/garminagent uv run pytest
```

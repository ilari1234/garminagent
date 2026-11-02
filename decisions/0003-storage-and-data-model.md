# 0003: PostgreSQL with raw and normalized layers

**Status:** Accepted (owner chose PostgreSQL, 2026-10-24; built in M3–M5).

## Context

Data must be re-parseable without re-fetching, queryable for analytics, private, and portable to
AWS. Garmin's payloads can drift, and they contain GPS and free text that must never spread.

## Decision

- **PostgreSQL 18**, reached by a standard `DATABASE_URL`, so a container and RDS differ only by
  configuration.
- **Raw layer:** every response verbatim in `raw_payload` (JSONB, one row per distinct content);
  activity files in a `BlobStore` with content-addressed keys, indexed by `activity_file`.
- **Normalized layer:** typed rows with **no GPS and no free text**, enforced by a schema guard
  test. Normalization is lenient, so schema drift never breaks ingestion.
- **`athlete_id` on every data table**, even though the app is single-user.
- **Least-privilege roles:** migrations run as the owner in a separate process; the app connects as
  `garminagent_app` (DML only, read-only `alembic_version`).
- **Per-second data in Postgres** (`activity_sample`), one FIT file per activity.
- **`purge-data --yes`** removes all rows, then all blobs.

## Alternatives considered

- **SQLite plus files**, **DuckDB plus Parquet.** Offered; the owner chose PostgreSQL. It fits the
  "local now, AWS later" requirement directly via RDS. DuckDB's analytics strength was given up.
- **Parquet or downsampling for samples.** Deferred: not needed until volume says so.
- **Keeping only normalized data.** Rejected: any parser change would need a full re-fetch.

## Consequences

- Phase 2 can re-normalize stored payloads to get fields not yet extracted, with no Garmin requests
  (ADR 0013 relies on this).
- The raw layer keeps GPS and free text, so nothing outside ingestion and normalization may read it.
- `activity_sample` can grow large. Volume on real data isn't measured yet (OPEN_QUESTIONS).
- Migrations form a linear Alembic chain: only one track may add them at a time (ADR 0012).
- No encryption in code: local relies on full-disk encryption, AWS on KMS.
- GHCR image and backups need care: see SECURITY.md and `docs/operations.md`.
- Column-level contract: [storage schema](../docs/contracts/storage-schema.md).

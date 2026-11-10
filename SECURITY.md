# Security

How garminagent protects its secrets and the user's health data, and how security stays part
of the work. Keep this file current: update it whenever a milestone changes what we store,
send, or depend on.

## What we protect

| Asset | Why it matters |
|---|---|
| Garmin Personal API key | Reads the user's entire Garmin history. Valid up to a year. |
| Garmin access tokens | Same reach as the key for 24 h. |
| Health data (HR, HRV, sleep, stress, weight, cycle data) | GDPR special-category personal data. |
| GPS start points and tracks | Reveal home and work locations. |
| AWS / Anthropic credentials (later) | Cost, and access to all stored data. |

## Trust boundaries

```
 Garmin Personal API ──https──▶ client ──▶ raw store + Postgres ──▶ analytics ──▶ agent tools ──▶ Bedrock (EU, retention none)
        (Garmin)              (our code)     (local / AWS, encrypted)            (derived metrics only)       (AWS)
 Developer machine: .env, data/, Docker  ·  Claude Code (reads code and structure-only reports, never data/ or .env)
 GitHub: code, CI, images in GHCR (no personal data ever)
```

## Threats and controls

| Threat | Controls | Where |
|---|---|---|
| API key or token leaks (logs, errors, output, git, image) | `SecretStr`; token never persisted, `repr=False`; errors carry method/path/status only; httpx/httpcore loggers held at WARNING; `.env` gitignored and dockerignored; leak regression test over every log record in the suite; Trivy secret scan of the image; GitGuardian on PRs | `config.py`, `garmin/token.py`, `garmin/transport.py`, `log.py`, `tests/conftest.py`, CI |
| Credentials sent to the wrong place | Base URL must be https (http only to loopback), no userinfo/query; httpx doesn't follow redirects | `urls.py`, transport |
| Garmin throttling or locking the account | 1 req/s pacing, bounded backoff, hard stop on long `Retry-After`; per-run request budget (`SYNC_MAX_REQUESTS`); a rate-limited sync stops the whole run (exit 75) instead of continuing | transport, `sync.py` |
| Sync output or logs exposing health data | Sync reports, logs, `sync_run` history and `status` carry counts, dates and stop reasons only (tested); command output on stdout, logs on stderr | `sync.py`, `runner.py`, `cli.py`, `log.py` |
| Overlapping syncs (manual + scheduled, or overlapping cloud tasks) doubling Garmin requests | Postgres advisory lock: a second run skips without any request | `storage/db.py`, `runner.py` |
| Sync silently broken (expired key, repeated errors) | `sync_run` history; scheduler health check (`status --max-age-hours 13`) turns unhealthy on stale or failed runs; unexpected errors end a run as `internal_error` and the scheduler backs off instead of exiting and restarting into a request loop | `runner.py`, `sync.py`, compose |
| Malicious or compromised dependency | `uv.lock` with hashes, installed `--locked`; pip-audit gate + weekly; Dependabot with 7-day cooldown | CI, `dependabot.yml` |
| Compromised GitHub Action or base image | SHA/digest pins; zizmor (online audits) gate + weekly; `persist-credentials: false`; least permissions; Trivy run from a pinned image in a job without secrets, after cosign verifies it was signed by Aqua's release workflow for that exact tag | workflows |
| Distrusted or compromised CA (e.g. eMudhra, Oct 2026) | Up-to-date `certifi` (httpx's and botocore's trust store) and base-image CA bundle; tool images chosen for current CA stores; the database trusts only AWS's RDS CA bundle (`PGSSLROOTCERT`), committed with its SHA-256 checked by a test (see *RDS CA bundle*) | `uv.lock`, Dockerfile, CI, `tests/test_rds_ca.py` |
| Broken or unchecked change reaches `main` | Ruleset: PR required, all CI checks and CodeQL must pass on an up-to-date branch, no direct or force pushes, no bypass | GitHub ruleset |
| Vulnerable OS packages in the image | Trivy scan gates the image push (fixable HIGH/CRITICAL) | CI `scan` job |
| Personal data in the repo or chat | Synthetic fixtures only; Claude Code denied reading `.env*` and `data/**`, and a PreToolUse hook blocks Bash and PowerShell commands that name them, including absolute paths and globs, failing closed if it can't run (command-text rules: a backstop, not a boundary; tested) | tests, `.claude/settings.json`, `scripts/claude_hooks/`, CLAUDE.md |
| AWS account details (account IDs, ARNs, endpoints) published through Terraform files or public CI logs | State, plans, real tfvars and `backend.hcl` gitignored and dockerignored; infra CI has no AWS credentials, no `id-token` and never runs `plan` or touches state; plans shared only as `plan_summary.py` output (addresses and actions, tested never to print values); Claude Code denied reading those files, `~/.aws` and running `terraform` apply/plan/state commands (command-text rules: a backstop, not a boundary) | `.gitignore`, `infra.yml`, `infra/scripts/plan_summary.py`, `.claude/settings.json` |
| Terraform changes something outside the EU or in the wrong account | Provider region fixed to `eu-north-1` and checked by a custom Trivy check (any non-EU or implicit region fails CI; the check self-tests on each run); `allowed_account_ids` in every root; account IDs validated | `infra/*/providers.tf`, `infra/policy/`, `infra.yml` |
| Compromised Terraform provider or lint plugin | Exact provider version; `.terraform.lock.hcl` with hashes for three platforms, CI `init -lockfile=readonly`; no third-party modules; Terraform and TFLint from digest-pinned images; TFLint AWS ruleset installed by SHA-256 pinned after verifying its release attestation | `infra/`, `infra.yml` |
| Malicious activity file (zip bomb, wrong format, corrupt member) | Size cap, FIT signature check; a zip that can't be read is marked `invalid` (`corrupt_zip`), never retried in a loop | `garmin/files.py`, `sync.py` |
| Malformed or hostile FIT content | Garmin's maintained SDK decodes; size (64 MiB) and record (400k) caps; integrity/CRC check before reading; only known numeric fields taken, developer fields and text ignored; out-of-range values become NULL; failures marked `invalid` with a fixed reason code, never retried in a loop | `fit.py`, `sync.py` |
| Path traversal via zip member names | Blob keys built from IDs and SHA-256 only; keys validated (no `.`/`..`/absolute/backslash) in every operation of both backends, before any S3 request, and resolved inside the store root locally; `delete_prefix` stops at `/` boundaries (athlete 1's purge can't reach athlete 12); member names stored as data | `storage/blobs.py`, `storage/s3.py`, `storage/repository.py` |
| S3 blob store leaking credentials or response data | No AWS key settings: region and credentials come from the default chain (the ECS task role); boto3 is imported only with `BLOB_BACKEND=s3`; errors carry operation, key and HTTP status (or the botocore class) only, never S3's error body or headers, and the botocore exception isn't chained into tracebacks (tested with canaries); a missing bucket is an error, not a missing blob | `storage/s3.py`, `config.py`, `tests/storage/test_blobs.py` |
| App compromise or bug alters the schema | App connects as `garminagent_app` (DML only, no DDL/TRUNCATE, read-only `alembic_version`; from 0006 each migration grants it explicitly on its own new tables, tested with default privileges revoked); migrations run as the owner in a separate one-shot process; the app containers get no owner credentials (compose blanks `POSTGRES_PASSWORD` and `MIGRATION_DATABASE_URL` for `scheduler` and `app`). On RDS the owner isn't a superuser either: CONNECT and `USAGE, CREATE ON SCHEMA public` only (`db-bootstrap`) | migration 0001, `docker/postgres/init`, compose, `storage/bootstrap.py` |
| RDS master credentials misused, or migrations run as the master | Only `db-bootstrap` reads `DB_MASTER_USERNAME`/`DB_MASTER_PASSWORD` (`SecretStr`, shown as `set` by `config`); it refuses when the master is also the owner or app user, when the app user isn't `DB_APP_ROLE`, or when the URLs point at different databases; role names checked against `ROLE_NAME` and quoted as identifiers; passwords become SCRAM-SHA-256 verifiers on the client (libpq), so the server and its logs never see them; nothing secret is logged, printed or put in errors (tested); one transaction; a re-run changes only passwords and refuses existing roles with elevated attributes; grants are checked after they run, because GRANT and REVOKE only warn when not allowed | `storage/bootstrap.py`, `cli.py`, `tests/storage/test_bootstrap.py` |
| SQL injection | SQLAlchemy Core with bound parameters everywhere; the one interpolated identifier (app role) is regex-validated twice | `storage/repository.py`, migration 0001, `config.py` |
| Database traffic readable on a network | Warning on every connection path when a non-local host lacks `sslmode=require/verify-*`. With `DB_REQUIRE_VERIFY_FULL=true` (AWS), anything but `sslmode=verify-full` for a non-local host is refused before connecting, on every path: app, migrations, `db-bootstrap` (tested). The image carries the RDS CA bundle | `storage/db.py`, migrations `env.py`, Dockerfile |
| Raw store readable by other local users | Files 0600, directories 0700 (POSIX); in the image `/data` belongs to the non-root `app` user. On S3: private bucket, SSE-KMS, TLS-only policy, no versioning (B3c) | `storage/blobs.py`, Dockerfile, `infra/workload` (B3c) |
| Location or free text spreading into analysis | Normalized tables have no GPS or name/description/note columns (schema guard test covers every normalized and derived table, and fails on a table not assigned to a layer); sleep notes, feedback and insights are never read; HRV status kept only if it is an enum code; HR zone sport and method kept only as the spec's codes, and every text column in the M6a and M6b tables is held to its codes by a CHECK constraint (a test fails on a derived-table text column without one); raw layer keeps everything only for re-parsing | `storage/models.py`, `normalize.py`, `tests/storage/test_schema.py` |
| Wrong thresholds silently skewing loads (phase 2) | Every threshold source is range-checked with one table (`THRESHOLD_RANGES`): activity fields and Garmin zones become NULL when implausible, manual input is refused (exit 2, the message names the range, never the value); precedence is code (manual, Garmin zones, activity) and `thresholds list` shows each value's source; Garmin's FTP and threshold speed feed only rides and runs; generated default zones are not requested | `normalize.py`, `analytics/thresholds.py`, `cli.py` |
| Wrong per-activity metrics or loads (phase 2) | Every formula tested against hand-computed synthetic values; one gap rule; no value without 50 % channel coverage and the thresholds it needs (never a guess); running load HR-only; recompute by input fingerprint, so a changed threshold, parser or algorithm never leaves stale rows; rows record the thresholds used and whether any was carried back | `analytics/metrics.py`, `analytics/activity_metrics.py`, `tests/test_metrics.py` |
| HR load coefficient revealing sex | `ANALYTICS_TRIMP_B` accepts only 1.92 or 1.67, has no default, shows as `set` in `config`, is never logged or stored as a column (only inside the input fingerprint), and settings errors don't echo input (tested) | `config.py`, `models.py`, `tests/test_config.py`, `tests/storage/test_activity_metrics.py` |
| Analytics bugs breaking the sync | The `analyze` step catches every error per part (and per activity for metrics), counts it in `sync_run.counts["analytics"]` and logs the class name only; it never changes how a run ends | `analytics/step.py`, `analytics/activity_metrics.py`, `sync.py` |
| Data kept longer than wanted | `garminagent purge-data --yes` deletes all rows (one transaction, cascading) and then all blobs; re-runnable | `storage/repository.py`, `cli.py` |
| Data sent to a model (phase 3) | See *Data handling* in docs/plan.md; OWASP LLM/Agentic mapping below | planned |
| Unsafe training plan (phase 3) | Load-progression and recovery limits as code; evaluator agent; user approval before a plan is active | planned |

### OWASP Top 10 for LLM (2025) / Agentic (2026) — planned controls

| Risk | Control |
|---|---|
| LLM01 Prompt injection / ASI01 Goal hijack | Garmin free text never sent; tool outputs treated as data |
| LLM02 Sensitive information disclosure | Derived metrics only; no GPS/IDs; Bedrock retention `none` |
| LLM05 Improper output handling | Plans validated against a schema and plausibility rules before storage/export |
| LLM06 Excessive agency / ASI02 Tool misuse / ASI03 Privilege abuse | Read-only narrow tools; one validated write path for plans; no shell/code tools |
| LLM09 Misinformation / ASI09 Human-agent trust | Safety limits in code; plan shows its inputs and reasoning; user approves |
| ASI06 Memory/context poisoning | Plans versioned with their inputs; validated before reuse |
| ASI08 Cascading failures / LLM10 Unbounded consumption | Iteration and token caps; AWS budget alarm |

## Security in every milestone

**Definition of done — answer each before a milestone is complete:**

- [ ] New secret? Stored as `SecretStr`, never logged, documented in README configuration.
- [ ] New personal data stored, logged or sent anywhere? Minimal, documented here, and covered
      by the data-handling rules.
- [ ] External input (API responses, files, config) validated; failure paths tested.
- [ ] New dependency, action or image? Justified, locked/pinned, audit and zizmor clean.
- [ ] `/security-review` run on the change; findings fixed or recorded here.
- [ ] This file updated if the threat model changed.

**Deeper reviews at risky transitions:**

| Transition | Review focus |
|---|---|
| M3 storage ✅ 2026-10-25 | DB credentials and roles, raw-store file permissions, encryption at rest, retention and deletion — results below |
| Phase 3 agents | Walk the OWASP LLM and Agentic tables above; safety limits as code; tool permissions; prompt-injection tests |
| AWS deployment | Least-privilege IAM, Secrets Manager, Bedrock retention SCP, EU regions only, CloudTrail, KMS, budget alarms. Per-stage items: [docs/aws-deployment.md](docs/aws-deployment.md#security-review-per-stage) |

### M3 storage review (2026-10-25)

| Area | Decision / result |
|---|---|
| DB roles | Owner role runs migrations only; app role `garminagent_app` has SELECT/INSERT/UPDATE/DELETE on data tables, SELECT on `alembic_version`, nothing else. Verified by tests that run as the app role and attempt CREATE/DROP/ALTER/TRUNCATE/role creation and alembic_version writes. |
| Credentials | Two URLs (`DATABASE_URL`, `MIGRATION_DATABASE_URL`), both `SecretStr`, passwords hidden in `config` output and never logged; Alembic gets the URL in memory, not via `alembic.ini`. Compose has no default passwords: they are required from `.env` (changed after this review, when GitGuardian flagged the earlier defaults). On AWS both URLs come from Secrets Manager. |
| Tests vs. real data | Integration tests create a throwaway database and role per run and drop them afterwards; they never touch the compose database. |
| Raw store | Local filesystem behind a `BlobStore` interface (S3 later); content-addressed keys; 0600/0700 permissions on POSIX; atomic writes. |
| Encryption at rest | Not implemented in code by design. Local: rely on full-disk encryption (BitLocker / FileVault / LUKS) — recommended for the developer machine. AWS: RDS and S3 with KMS. |
| Encryption in transit | Compose traffic stays on the Docker network; any non-local DB without TLS is warned about. AWS: `sslmode=verify-full`. |
| Retention and deletion | Data is kept until the owner deletes it; `purge-data` removes everything (rows, then blobs) and can be re-run. Revisions of raw payloads are kept (dedupe by content hash) so analysis can be reproduced. |
| Data minimization | Normalized layer: no GPS, names or descriptions (enforced by a test). Raw layer keeps Garmin's payloads verbatim, including GPS in summaries and FIT files, for re-parsing; it never feeds agents. |
| `/security-review` | No findings at the reporting bar. One hardening note (migrations bypassed the TLS warning) fixed before merge. |

### M6a analytics review (2026-11-07)

`/security-review` on the M6a change: no findings at the reporting bar. It checked migration 0006's
role interpolation (validated with `ROLE_NAME`, constant table names) and its grants (DML on the two
new tables and their sequences only); bound parameters in every new query; counts and codes only in
logs and `sync_run`; Garmin codes kept only from the spec's enums. Two hardening notes, both applied:
the database helper behind `renormalize`, `analyze` and `thresholds` now turns any unexpected error
into exit 1 with the class name (no traceback), and docs/operations.md says `thresholds list`, which
prints values, is for interactive use only, not where stdout becomes logs.

### M6b analytics review (2026-11-09)

`/security-review` on the M6b change: no findings at the reporting bar. It checked migration 0007's
role interpolation (validated with `ROLE_NAME`, constant table name) and grants (DML on
`activity_metrics` only; no sequence); bound parameters in every new query; that logs, `analyze`
output, `status` and `sync_run.counts` carry counts and codes only; that the HR load coefficient
has no column, shows as `set` in `config` and isn't echoed by validation errors; and CHECK
constraints on every code column. One hardening note, applied: the coefficient is now left out of
the `repr` of `Settings` and `AnalyticsOptions`, although no code path printed either. One note
accepted as designed (ADR 0013, *M6b as built*): a database reader could test both coefficients
against a row's fingerprint, but the same reader can derive it from HR load and the samples.

### B3b app code review (2026-11-07)

`/security-review` on the B3b change: no findings at the reporting bar. It checked `db-bootstrap`'s
SQL (role and database names only as quoted identifiers after `ROLE_NAME`, lookups bound, verifiers
as literals), its refusals and post-grant checks, and that no password, verifier or master URL can
reach logs, errors, stdout or tracebacks. It also checked that every connection path calls
`check_tls`, that S3 keys are validated before any request and errors carry no S3 body or headers,
and that the CA bundle holds only certificates. One hardening note, applied: `check_tls` now judges
a `?host=` query parameter (which libpq connects to) rather than the URL's host. Two notes below
the bar, accepted: with `log_statement=ddl` the server would log SCRAM verifiers (not passwords;
guessable offline only for weak passwords, and the URL passwords are generated); S3 calls don't set
`ExpectedBucketOwner` (only matters if the bucket were deleted and its name taken by another account).

## Automated checks

| Check | When | Fails on |
|---|---|---|
| ruff incl. bandit (`S`) rules, pyright strict | every push/PR | any finding |
| pytest incl. leak regression test | every push/PR | a fake secret in any log record; the RDS CA bundle's SHA-256 differing from this file |
| pip-audit over `uv.lock` | every push/PR, weekly | any known vulnerability |
| zizmor (online) over workflows and Dependabot config | every push/PR, weekly | any finding |
| cosign signature check of the Trivy image (exact signer identity) | every push/PR, before the scan | missing or wrong signature |
| Trivy image scan (vulnerabilities + secrets) | every push/PR | fixable HIGH/CRITICAL, any secret |
| `terraform fmt -check`, `init -backend=false -lockfile=readonly`, `validate`, `test` (mocked provider) per root (`infra-terraform`) | every push/PR | unformatted file, provider hash not in the lock file, invalid config, failing test |
| TFLint with the bundled Terraform rules (recommended preset) and the hash-pinned AWS ruleset (`infra-tflint`) | every push/PR | any warning or error; ruleset hash mismatch |
| Trivy config scan of `infra/` with embedded checks and custom check GA001 (EU Regions), after cosign verifies the image (`infra-trivy`) | every push/PR | any misconfiguration; GA001 self-test not failing on a non-EU provider |
| GitGuardian | PRs | detected secret |
| GitHub secret scanning with push protection | every push | push containing a known secret pattern is rejected |
| CodeQL (Python and Actions workflows) | PRs, weekly | high or higher security alerts (required by the ruleset) |
| Dependabot (actions, Docker, compose, Terraform providers in `infra/*`; uv once Python 3.15 is supported) | weekly | — (opens PRs) |

The image is pushed to GHCR only when lint, tests, audit and scan pass.

## Routine maintenance (manual)

| When | What |
|---|---|
| Weekly | Read the audit results and Dependabot PRs. Image bumps: Dependabot's Docker updater ignored the cooldown once (uv 0.13.5) — check release age. uv image bumps also need `required-version` and `uv_build` in the same PR. |
| Monthly | `uv lock --upgrade` and a PR (Dependabot can't update Python packages until it supports Python 3.15). Bump the Trivy and cosign images in ci.yml (Dependabot doesn't track them): check Aqua's advisories, take a release ≥7 days old unless it's a security fix, update `TRIVY_IMAGE` digest **and** the tag in `TRIVY_SIGNER`, and run the verify command locally first. The same Trivy and cosign pins are in `infra.yml`: bump both workflows together. Bump the Terraform and TFLint image digests and the TFLint AWS ruleset (`RULESET_URL` and `RULESET_SHA256`, after `gh attestation verify`) in `infra.yml` the same way; steps in `infra/README.md`. |
| Every 90 days | Create a new Garmin key (90-day expiry), update `.env`, delete the old key (steps: docs/operations.md, key rotation). Reread this file and the threat model. Confirm the GHCR image is still private (`garmin-fit-sdk` license). Compare the RDS CA bundle with AWS's (see *RDS CA bundle*). |
| On each new GitHub setting or integration | Least privilege; note it here. |

### Cooldown exceptions

Releases newer than the 7-day cooldown, taken deliberately. Add a row for every new exception.

| Date | Component | Version | Why |
|---|---|---|---|
| 2026-10-25 | uv (CLI, image, `uv_build`) | 0.13.5 | 0.13.4 removed the distrusted eMudhra/emSign root from uv's certificate store; 0.13.5 ships CPython 3.15.1 (security release). |
| 2026-10-25 | certifi (via `uv.lock`) | 2026.10.23 | Removes emSign ECC Root CA - G3 (eMudhra incident). |
| 2026-10-25 | Trivy image | 0.76.1 | Security release for the eMudhra CA incident (Go 1.27.3, updated CA store). |
| 2026-10-25 | cosign image | 3.1.4 | Its CA store drops emSign ECC Root CA - G3; 3.1.3 still trusted it (not stated in the release notes; checked in the image). |
| 2026-11-06 | `python:3.15-slim` base image | digest `e97b88e4…`, built 2026-11-05 (Debian 13.7, apt 3.0.4, Python 3.15.2) | CVE-2026-109518 in apt 3.0.3 (package verification bypass on install; fixed in trixie-security 3.0.4) failed the Trivy gate. The app never runs apt; the fix is taken so the gate stays meaningful. |
| 2026-11-07 | `zlib1g` (Debian package, runtime stage) | 1:1.3.dfsg+really1.3.1-1+deb13u1 (trixie-security) | CVE-2026-76844 and CVE-2026-85091 (heap overflow in `gz_vacate()`) in 1:1.3.dfsg+really1.3.1-1+b1 failed the Trivy gate. No `python:3.15-slim` digest has the fix yet, so the Dockerfile upgrades the package with apt; remove that once the base image carries it. |

### RDS CA bundle

`docker/certs/rds-global-bundle.pem` is AWS's global RDS trust store, copied into the image at
`/app/certs/` (root-owned, read-only) and used by libpq through `PGSSLROOTCERT` for
`sslmode=verify-full`. It is public data; committing it pins exactly what the image trusts.

| Field | Value |
|---|---|
| Source | `https://truststore.pki.rds.amazonaws.com/global/global-bundle.pem`, linked from [RDS: using SSL/TLS](https://docs.aws.amazon.com/AmazonRDS/latest/UserGuide/UsingWithRDS.SSL.html) |
| Downloaded | 2026-11-07 (server `Last-Modified` 2026-10-14) |
| SHA-256 | `90a6f1a7978a1b1b096dca6c57511ca48d3b8cf052f8e704baf358f34ea2f21b` |
| Contents | 114 root CA certificates, including `Amazon RDS eu-north-1 Root CA` RSA2048 G1 (expires 2061), RSA4096 G1 and ECC384 G1. The earliest expiry in the bundle is 2061 |

**Refresh it** when AWS adds or rotates RDS CAs (announced in the RDS documentation and the AWS
Health Dashboard), before B3c's first apply, and when the 90-day routine finds the published file
changed: download it into an empty directory, check it holds only `BEGIN/END CERTIFICATE` blocks,
and update the file, the SHA-256 here and `BUNDLE_SHA256` in `tests/test_rds_ca.py` in one PR. The
instance's CA (B3c) must be one of the bundle's roots, or `verify-full` fails closed. The
eu-north-1 bundle would trust fewer CAs; the global one is kept so a Region change can't break
connections, and `verify-full` still checks the endpoint's name.

**Repository settings (owner):** the repo is public. Ruleset `prevent-push-to-main` on the
default branch, no bypass: no deletion, no force push, linear history, PR required (0
approvals, conversations resolved, squash only), status checks `lint`, `test`,
`audit / pip-audit`, `audit / zizmor`, `scan`, `image` required and up to date (to add after
B0's first green run: `infra-terraform`, `infra-tflint`, `infra-trivy`), CodeQL
results required. Code security: Dependabot alerts and security updates, secret scanning with
push protection, CodeQL default setup, private vulnerability reporting. GitHub's dependency
graph can't process `uv.lock` with Python 3.15 yet (same limitation as Dependabot's uv
updates). Review these settings with the 90-day routine; changing them is outside git history.

## Third-party license constraints

| Component | License | Constraint we follow |
|---|---|---|
| `garmin-fit-sdk` (FIT decoding) | Garmin *FIT Protocol License Agreement* (not OSI) | Use only, for our own purposes. **Never distribute it**: it is installed from PyPI, not vendored, and the **GHCR image, which contains it, must stay private**. Don't modify it (modifications are licensed back to Garmin). Don't put the repo under a copyleft license (GPL/AGPL) that would require disclosing it. Garmin may terminate with 30 days' notice, so it sits behind `fit.parse_fit` and can be swapped for `fitdecode` (MIT). Its "no safety-critical use" clause: plans are advisory, approved by the owner, with safety limits in code. |
| Terraform CLI (infra tooling) | BUSL 1.1 (not OSI) | Used only to manage our own AWS accounts; never offered to others as a hosted or embedded Terraform service. Not vendored: run from HashiCorp's image. Switching to OpenTofu (MPL-2.0) stays about one PR (ADR 0008). |
| AWS provider, TFLint, TFLint AWS ruleset | MPL-2.0 | None for our use: not modified or redistributed. |
| `boto3`, `botocore`, `s3transfer` (S3 blob store) | Apache-2.0 | None beyond keeping their license and notice files, which the installed wheels carry into the (private) image. The dev-only `types-boto3` stubs are MIT. |

## Incident playbook

| Event | Do |
|---|---|
| Garmin key exposed (commit, log, chat, screenshot) | Delete the key in Garmin Connect → Personal API Key page (revokes all its tokens); create a new one; update `.env`; if committed, rewrite history is not enough — the key is burned. |
| Backups (`data/backups/`) copied somewhere unencrypted | Treat as disclosed: delete the copies; they contain the full health history. See docs/operations.md for where backups belong. |
| Personal data committed or pushed | Remove it from history, force-push, delete GHCR images built from it; treat the data as disclosed to anyone with repo access. |
| Compromised dependency/action announced | Check workflow runs that used it; rotate any secret those jobs could reach; pin to a clean version. |
| Cloud credential exposed (later) | Revoke in IAM immediately; review CloudTrail for its use. |

## Reporting

Please report vulnerabilities privately through GitHub's
[private vulnerability reporting](https://github.com/ilari1234/garminagent/security/advisories/new),
not in public issues. This is a personal, single-user project without a support commitment, but
reports are read and acted on.

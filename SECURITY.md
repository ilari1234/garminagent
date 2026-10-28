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
| Sync output or logs exposing health data | Sync reports and logs carry counts only (tested); command output on stdout, logs on stderr | `sync.py`, `cli.py`, `log.py` |
| Malicious or compromised dependency | `uv.lock` with hashes, installed `--locked`; pip-audit gate + weekly; Dependabot with 7-day cooldown | CI, `dependabot.yml` |
| Compromised GitHub Action or base image | SHA/digest pins; zizmor (online audits) gate + weekly; `persist-credentials: false`; least permissions; Trivy run from a pinned image in a job without secrets, after cosign verifies it was signed by Aqua's release workflow for that exact tag | workflows |
| Distrusted or compromised CA (e.g. eMudhra, Oct 2026) | Up-to-date `certifi` (httpx's trust store) and base-image CA bundle; tool images chosen for current CA stores | `uv.lock`, Dockerfile, CI |
| Broken or unchecked change reaches `main` | Ruleset: PR required, all CI checks and CodeQL must pass on an up-to-date branch, no direct or force pushes, no bypass | GitHub ruleset |
| Vulnerable OS packages in the image | Trivy scan gates the image push (fixable HIGH/CRITICAL) | CI `scan` job |
| Personal data in the repo or chat | Synthetic fixtures only; Claude Code denied `.env*` and `data/**` | tests, `.claude/settings.json`, CLAUDE.md |
| Malicious activity file (zip bomb, wrong format) | Size cap, FIT signature check, fail loudly | `garmin/files.py` |
| Malformed or hostile FIT content | Garmin's maintained SDK decodes; size (64 MiB) and record (400k) caps; integrity/CRC check before reading; only known numeric fields taken, developer fields and text ignored; out-of-range values become NULL; failures marked `invalid` with a fixed reason code, never retried in a loop | `fit.py`, `sync.py` |
| Path traversal via zip member names | Blob keys built from IDs and SHA-256 only; keys validated (no `.`/`..`/absolute/backslash) and resolved inside the store root; member names stored as data | `storage/blobs.py`, `storage/repository.py` |
| App compromise or bug alters the schema | App connects as `garminagent_app` (DML only, no DDL/TRUNCATE, read-only `alembic_version`); migrations run as the owner in a separate one-shot process | migration 0001, `docker/postgres/init`, compose |
| SQL injection | SQLAlchemy Core with bound parameters everywhere; the one interpolated identifier (app role) is regex-validated twice | `storage/repository.py`, migration 0001, `config.py` |
| Database traffic readable on a network | Warning on every connection path when a non-local host lacks `sslmode=require/verify-*`; AWS must use `verify-full` | `storage/db.py` |
| Raw store readable by other local users | Files 0600, directories 0700 (POSIX); in the image `/data` belongs to the non-root `app` user | `storage/blobs.py`, Dockerfile |
| Location or free text spreading into analysis | Normalized tables have no GPS or name/description columns (schema guard test); raw layer keeps them only for re-parsing | `storage/models.py`, `tests/storage/test_schema.py` |
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
| AWS deployment | Least-privilege IAM, Secrets Manager, Bedrock retention SCP, EU regions only, CloudTrail, KMS, budget alarms |

### M3 storage review (2026-10-25)

| Area | Decision / result |
|---|---|
| DB roles | Owner role runs migrations only; app role `garminagent_app` has SELECT/INSERT/UPDATE/DELETE on data tables, SELECT on `alembic_version`, nothing else. Verified by tests that run as the app role and attempt CREATE/DROP/ALTER/TRUNCATE/role creation and alembic_version writes. |
| Credentials | Two URLs (`DATABASE_URL`, `MIGRATION_DATABASE_URL`), both `SecretStr`, passwords hidden in `config` output and never logged; Alembic gets the URL in memory, not via `alembic.ini`. Local compose passwords are local-only defaults; on AWS both come from Secrets Manager. |
| Tests vs. real data | Integration tests create a throwaway database and role per run and drop them afterwards; they never touch the compose database. |
| Raw store | Local filesystem behind a `BlobStore` interface (S3 later); content-addressed keys; 0600/0700 permissions on POSIX; atomic writes. |
| Encryption at rest | Not implemented in code by design. Local: rely on full-disk encryption (BitLocker / FileVault / LUKS) — recommended for the developer machine. AWS: RDS and S3 with KMS. |
| Encryption in transit | Compose traffic stays on the Docker network; any non-local DB without TLS is warned about. AWS: `sslmode=verify-full`. |
| Retention and deletion | Data is kept until the owner deletes it; `purge-data` removes everything (rows, then blobs) and can be re-run. Revisions of raw payloads are kept (dedupe by content hash) so analysis can be reproduced. |
| Data minimization | Normalized layer: no GPS, names or descriptions (enforced by a test). Raw layer keeps Garmin's payloads verbatim, including GPS in summaries and FIT files, for re-parsing; it never feeds agents. |
| `/security-review` | No findings at the reporting bar. One hardening note (migrations bypassed the TLS warning) fixed before merge. |

## Automated checks

| Check | When | Fails on |
|---|---|---|
| ruff incl. bandit (`S`) rules, pyright strict | every push/PR | any finding |
| pytest incl. leak regression test | every push/PR | a fake secret in any log record |
| pip-audit over `uv.lock` | every push/PR, weekly | any known vulnerability |
| zizmor (online) over workflows and Dependabot config | every push/PR, weekly | any finding |
| cosign signature check of the Trivy image (exact signer identity) | every push/PR, before the scan | missing or wrong signature |
| Trivy image scan (vulnerabilities + secrets) | every push/PR | fixable HIGH/CRITICAL, any secret |
| GitGuardian | PRs | detected secret |
| GitHub secret scanning with push protection | every push | push containing a known secret pattern is rejected |
| CodeQL (Python and Actions workflows) | PRs, weekly | high or higher security alerts (required by the ruleset) |
| Dependabot (actions, Docker, compose; uv once Python 3.15 is supported) | weekly | — (opens PRs) |

The image is pushed to GHCR only when lint, tests, audit and scan pass.

## Routine maintenance (manual)

| When | What |
|---|---|
| Weekly | Read the audit results and Dependabot PRs. Image bumps: Dependabot's Docker updater ignored the cooldown once (uv 0.13.5) — check release age. uv image bumps also need `required-version` and `uv_build` in the same PR. |
| Monthly | `uv lock --upgrade` and a PR (Dependabot can't update Python packages until it supports Python 3.15). Bump the Trivy and cosign images in ci.yml (Dependabot doesn't track them): check Aqua's advisories, take a release ≥7 days old unless it's a security fix, update `TRIVY_IMAGE` digest **and** the tag in `TRIVY_SIGNER`, and run the verify command locally first. |
| Every 90 days | Create a new Garmin key (90-day expiry), update `.env`, delete the old key. Reread this file and the threat model. Confirm the GHCR image is still private (`garmin-fit-sdk` license). |
| On each new GitHub setting or integration | Least privilege; note it here. |

### Cooldown exceptions

Releases newer than the 7-day cooldown, taken deliberately. Add a row for every new exception.

| Date | Component | Version | Why |
|---|---|---|---|
| 2026-10-25 | uv (CLI, image, `uv_build`) | 0.13.5 | 0.13.4 removed the distrusted eMudhra/emSign root from uv's certificate store; 0.13.5 ships CPython 3.15.1 (security release). |
| 2026-10-25 | certifi (via `uv.lock`) | 2026.10.23 | Removes emSign ECC Root CA - G3 (eMudhra incident). |
| 2026-10-25 | Trivy image | 0.76.1 | Security release for the eMudhra CA incident (Go 1.27.3, updated CA store). |
| 2026-10-25 | cosign image | 3.1.4 | Its CA store drops emSign ECC Root CA - G3; 3.1.3 still trusted it (not stated in the release notes; checked in the image). |

**Repository settings (owner):** the repo is public. Ruleset `prevent-push-to-main` on the
default branch, no bypass: no deletion, no force push, linear history, PR required (0
approvals, conversations resolved, squash only), status checks `lint`, `test`,
`audit / pip-audit`, `audit / zizmor`, `scan`, `image` required and up to date, CodeQL
results required. Code security: Dependabot alerts and security updates, secret scanning with
push protection, CodeQL default setup, private vulnerability reporting. GitHub's dependency
graph can't process `uv.lock` with Python 3.15 yet (same limitation as Dependabot's uv
updates). Review these settings with the 90-day routine; changing them is outside git history.

## Third-party license constraints

| Component | License | Constraint we follow |
|---|---|---|
| `garmin-fit-sdk` (FIT decoding) | Garmin *FIT Protocol License Agreement* (not OSI) | Use only, for our own purposes. **Never distribute it**: it is installed from PyPI, not vendored, and the **GHCR image, which contains it, must stay private**. Don't modify it (modifications are licensed back to Garmin). Don't put the repo under a copyleft license (GPL/AGPL) that would require disclosing it. Garmin may terminate with 30 days' notice, so it sits behind `fit.parse_fit` and can be swapped for `fitdecode` (MIT). Its "no safety-critical use" clause: plans are advisory, approved by the owner, with safety limits in code. |

## Incident playbook

| Event | Do |
|---|---|
| Garmin key exposed (commit, log, chat, screenshot) | Delete the key in Garmin Connect → Personal API Key page (revokes all its tokens); create a new one; update `.env`; if committed, rewrite history is not enough — the key is burned. |
| Personal data committed or pushed | Remove it from history, force-push, delete GHCR images built from it; treat the data as disclosed to anyone with repo access. |
| Compromised dependency/action announced | Check workflow runs that used it; rotate any secret those jobs could reach; pin to a clean version. |
| Cloud credential exposed (later) | Revoke in IAM immediately; review CloudTrail for its use. |

## Reporting

Please report vulnerabilities privately through GitHub's
[private vulnerability reporting](https://github.com/ilari1234/garminagent/security/advisories/new),
not in public issues. This is a personal, single-user project without a support commitment, but
reports are read and acted on.

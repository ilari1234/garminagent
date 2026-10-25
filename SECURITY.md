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
| Garmin throttling or locking the account | 1 req/s pacing, bounded backoff, hard stop on long `Retry-After` | transport |
| Malicious or compromised dependency | `uv.lock` with hashes, installed `--locked`; pip-audit gate + weekly; Dependabot with 7-day cooldown | CI, `dependabot.yml` |
| Compromised GitHub Action or base image | SHA/digest pins; zizmor (online audits) gate + weekly; `persist-credentials: false`; least permissions; Trivy run from a pinned image in a job without secrets, after cosign verifies it was signed by Aqua's release workflow for that exact tag | workflows |
| Distrusted or compromised CA (e.g. eMudhra, Oct 2026) | Up-to-date `certifi` (httpx's trust store) and base-image CA bundle; tool images chosen for current CA stores | `uv.lock`, Dockerfile, CI |
| Broken or unchecked change reaches `main` | Ruleset: PR required, all CI checks and CodeQL must pass on an up-to-date branch, no direct or force pushes, no bypass | GitHub ruleset |
| Vulnerable OS packages in the image | Trivy scan gates the image push (fixable HIGH/CRITICAL) | CI `scan` job |
| Personal data in the repo or chat | Synthetic fixtures only; Claude Code denied `.env*` and `data/**` | tests, `.claude/settings.json`, CLAUDE.md |
| Malicious activity file (zip bomb, wrong format) | Size cap, FIT signature check, fail loudly | `garmin/files.py` |
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
| M3 storage | DB credentials and roles, raw-store file permissions, encryption at rest, retention and deletion |
| Phase 3 agents | Walk the OWASP LLM and Agentic tables above; safety limits as code; tool permissions; prompt-injection tests |
| AWS deployment | Least-privilege IAM, Secrets Manager, Bedrock retention SCP, EU regions only, CloudTrail, KMS, budget alarms |

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
| Every 90 days | Create a new Garmin key (90-day expiry), update `.env`, delete the old key. Reread this file and the threat model. |
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

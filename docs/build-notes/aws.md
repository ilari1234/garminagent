# AWS build notes (track B)

What was built for each AWS stage, choices made where
[ADR 0008](../../decisions/0008-cloud-deployment.md) and [docs/aws-deployment.md](../aws-deployment.md)
left room, and what later stages must provide. The ADR holds the decisions; this file holds how
they were carried out ([ADR 0017](../../decisions/0017-build-notes-out-of-adrs.md)). Each stage PR
adds its own section. No account IDs, ARNs or state: this repository is public.

## B0 implementation (2026-11-03, versions bumped 2026-11-06)

Release dates rechecked with
`gh api` on 2026-11-06.

- **Versions.** Terraform 1.17.1 (`required_version = "~> 1.17.0"`), `hashicorp/aws` exactly 6.71.0
  (6.72.0 is under 7 days old), TFLint 0.65.0, TFLint AWS ruleset 0.50.0. No cooldown exception
  was needed.
- **TFLint AWS ruleset: pinned, so not core-only** (OPEN_QUESTIONS 9). `gh attestation verify`
  showed the release's `checksums.txt` was signed by the ruleset repo's `release.yml` at tag
  v0.50.0. CI downloads the Linux zip and checks the SHA-256 from that file, instead of
  `tflint --init`, which verifies the attestation but pins no hash and needs a GitHub token.
  `deep_check` (AWS API calls) is off.
- **Trivy custom checks.** Built-in checks already cover public RDS and open inbound rules, so B0
  adds only GA001: every `aws` provider sets an explicit `eu-*` region (Rego in `infra/policy/`,
  run through `--raw-config-scanners terraform`). CI self-tests it against a non-EU provider each
  run. `--skip-check-update` uses the checks embedded in the pinned image rather than downloading
  the latest bundle, so check changes arrive with Trivy bumps.
- **Account guard.** Every root sets `allowed_account_ids` from a validated `account_id` variable;
  the example tfvars placeholder fails validation if left unfilled.
- **Script name.** `infra/scripts/plan_summary.py` (not `plan-summary`) so ruff, pyright strict and
  pytest cover it; standard library only.
- **State bootstrap.** `org` and `account` start on local state through a gitignored
  `backend_override.tf` and migrate to their bucket in B1.
- **Claude deny rules.** Reading state, plans, tfvars, `backend.hcl` and `~/.aws`; running
  `terraform` apply, destroy, plan, refresh, state, show, output, console, import and
  force-unlock. They match command text, so they back up the rule rather than enforce it.

## B3b implementation (2026-11-07)

App code only; no `infra/` change, no AWS resource, no
migration. Release dates checked on PyPI on 2026-11-07.

- **Dependency.** `boto3` 1.43.123 (2026-10-30; 1.43.124–126 are under 7 days old), with `botocore`
  1.43.123, `s3transfer` 0.19.3 (Apache-2.0) and `jmespath` 1.1.0 (MIT). Maintained by AWS, released
  every weekday. Why: AWS's own SDK does SigV4 signing, the credential chain (the ECS task role)
  and retries; our own signer would be more code to get wrong. Dev only: `types-boto3[s3]` 1.43.123
  (MIT) for pyright strict. No cooldown exception. The image grows from 309 to 364 MB, mostly
  botocore's service models (31 MB).
- **S3 store** (`storage/s3.py`) as planned, plus: only `NoSuchKey` counts as a missing blob (a
  missing bucket is also a 404 and must not mark files `invalid`); other failures raise
  `BlobStoreError`, an `OSError`, so callers treat both backends alike; standard retry mode, 5
  attempts. `purge-data` now exits 1 without a traceback if deleting blobs fails (re-run it).
- **What B3c and B3d must provide.** The task role needs `s3:ListBucket` on the bucket as well as
  get, put and delete on its objects (without it a missing key is 403, not 404, and `delete_prefix`
  can't list), plus the KMS key's `GenerateDataKey` and `Decrypt`. Task definitions set
  `AWS_REGION`, `BLOB_BACKEND=s3`, `BLOB_S3_BUCKET` and `DB_REQUIRE_VERIFY_FULL=true`. The
  `db-bootstrap` task maps the RDS-managed secret's JSON keys to `DB_MASTER_USERNAME` and
  `DB_MASTER_PASSWORD` (`<secret ARN>:username::` and `:password::`). URLs need
  `?sslmode=verify-full`; the image sets `PGSSLROOTCERT` to the bundle, so `sslrootcert` is optional.
- **`db-bootstrap`** (`storage/bootstrap.py`). The owner doesn't become the database owner: the RDS
  master isn't a superuser and, from PostgreSQL 16, can't act as roles it creates without granting
  itself membership first. Instead the owner gets CONNECT and `USAGE, CREATE ON SCHEMA public WITH
  GRANT OPTION` (the option lets migration 0001 grant the app role USAGE, unchanged). The app role
  gets CONNECT; PUBLIC loses CONNECT, as in the compose init script. Both roles are `NOSUPERUSER
  NOCREATEDB NOCREATEROLE NOREPLICATION NOBYPASSRLS`. Passwords go to the server only as SCRAM
  verifiers computed by libpq on the client, so they can't reach server logs. Everything runs in
  one transaction and the grants are checked afterwards (GRANT and REVOKE only warn when not
  allowed). It refuses when the master is also the owner or app user, so it doesn't run against the
  compose stack itself, where the owner is the superuser; the compose init script stays the local
  bootstrap, and the integration tests run `db-bootstrap` on the compose Postgres image with an
  RDS-like master (CREATEROLE, CREATEDB, not a superuser), then all migrations as its owner. M6f's
  `garminagent_agent` is a marked extension point (a third role of kind `member`, name and grants
  from M6f's `garminagent.storage` module, password from `AGENT_DATABASE_URL`); not built.
- **TLS guard.** `warn_if_insecure` became `check_tls`: with `DB_REQUIRE_VERIFY_FULL`, a non-local
  host without `sslmode=verify-full` raises before connecting, from `make_engine` (app,
  `current_revision`, `db-bootstrap`) and the Alembic env (migrations). `make_engine` takes the flag
  explicitly, so `db-bootstrap` checks the settings it was given rather than the cached ones.
- **RDS CA bundle.** The global bundle, at `docker/certs/rds-global-bundle.pem`, stored without
  line-ending conversion (`*.pem -text`) so its SHA-256 (SECURITY.md, checked by a test) holds on
  every checkout. Root-owned and read-only in the image.

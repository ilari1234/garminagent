# Track B: infrastructure

Entry point for a session working on `infra/` (AWS stages; next: B1, then B2, then B3c–d). Read
this instead of every doc; follow the links only for what your stage touches.

## Read first

1. [CLAUDE.md](../../CLAUDE.md) (loaded for you) and the Status table in
   [PROJECT_STATE.md](../../PROJECT_STATE.md) on `origin/main`, or run `/session-start`.
2. [ADR 0008](../../decisions/0008-cloud-deployment.md): the decisions and their defaults.
3. Your stage's section of [docs/aws-deployment.md](../aws-deployment.md) (*B1 foundation*,
   *B2 Bedrock*, *B3 workload*), plus its *Security review per stage* row.
4. [infra/README.md](../../infra/README.md): roots, the owner's plan and apply flow, conventions.
5. [docs/build-notes/aws.md](../build-notes/aws.md): versions pinned in B0, and what B3b asks of
   B3c and B3d.

Only if needed: [SECURITY.md](../../SECURITY.md) (threats and controls, cooldown exceptions),
[ADR 0011](../../decisions/0011-supply-chain-controls.md) (pinning and cooldown),
[ADR 0006](../../decisions/0006-model-access-and-data-handling.md) (Bedrock, for B2).

## What this track owns

- `infra/` and `.github/workflows/infra.yml`. ADR 0008 lists the exceptions outside `infra/`.
- Not migrations: those are track A's. App code that a stage needs (like B3a and B3b) is done in
  its own PR and rebases over track A's merges to the shared files.

## Rules that bite in this track

- **Every `terraform plan`, `apply` and state access, and every AWS account step, is the
  owner's**, in their own terminal. Deny rules back this up. Share plans only as
  `plan_summary.py` output.
- **Nothing account-shaped in the repo or chat:** no account IDs, ARNs, emails or state. Never use
  them as `for_each` keys (addresses get printed).
- **Pins:** Terraform `~> 1.17.0`, `hashicorp/aws` exact, lock files for three platforms, no
  third-party modules, tool images by digest. 7-day cooldown; a security exception goes in
  SECURITY.md's table.
- **EU only:** every provider sets an explicit `eu-*` region (Trivy check GA001).
- **CI has no AWS credentials** and never runs `plan`. Keep it that way.
- **No real prompt to Bedrock** before B2's retention verification passes.

## Finishing a stage

Run the `milestone-close` skill: checks, the stage's security review row, build notes in
`docs/build-notes/aws.md`, and the stage's row in PROJECT_STATE marked Done in the same PR. The
owner applies after merge.

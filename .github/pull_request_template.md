## What and why

## Project state

- [ ] Architecture, schema, contracts or conventions changed? ADR added or updated, and
      `PROJECT_STATE.md` updated in this PR.

## Security checklist

See [SECURITY.md](../SECURITY.md#security-in-every-milestone).

- [ ] New secret? `SecretStr`, never logged, documented in the README configuration table.
- [ ] New personal data stored, logged or sent? Minimal and covered by the data-handling rules.
- [ ] External input validated; failure paths tested.
- [ ] New dependency, action or image? Justified and locked/pinned (SHA / digest).
- [ ] `/security-review` run; findings fixed or recorded in SECURITY.md.
- [ ] SECURITY.md updated if the threat model changed.

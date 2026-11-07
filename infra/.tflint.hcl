# TFLint for all roots (`tflint --recursive --config "$PWD/.tflint.hcl"` from infra/).
config {
  # No modules are used (ADR 0008); nothing to fetch or inspect.
  call_module_type = "none"
}

plugin "terraform" {
  enabled = true
  preset  = "recommended"
}

# The AWS ruleset (MPL-2.0) is installed by hand, not by `tflint --init`, so that CI runs a
# binary whose SHA-256 is pinned in .github/workflows/infra.yml. That hash was taken from the
# release's checksums.txt after `gh attestation verify` confirmed it was signed by the
# ruleset's own release workflow for that tag. Hence no `source` or `version` here.
plugin "aws" {
  enabled = true

  # Deep checking calls AWS APIs with real credentials. It stays off: CI has none.
  deep_check = false
}

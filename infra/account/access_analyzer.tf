# Flags resources shared outside the account (buckets, keys, roles, ...). The external access
# analyzer is free. Findings are read in the console or with
# `aws accessanalyzer list-findings-v2`, in the quarterly SSO review (SECURITY.md).
resource "aws_accessanalyzer_analyzer" "external" {
  analyzer_name = "garminagent-external-access"
  type          = "ACCOUNT"
}

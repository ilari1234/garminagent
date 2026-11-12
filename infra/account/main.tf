# Member account foundation (stage B1): the customer-managed KMS key, the state bucket for this
# root and infra/workload, the GitHub OIDC provider with a role that has no permissions yet, and
# IAM Access Analyzer. Steps the owner does by hand, in order: docs/aws-b1-checklist.md.
#
# Resource addresses never contain an account ID or name from tfvars: plan_summary.py prints
# addresses, and its output is what the owner shares.

# The one customer-managed key: state now, B3c's data later (RDS, S3, secrets), when its policy
# gains the service statements those need.
resource "aws_kms_key" "main" {
  description             = "garminagent: Terraform state and workload data"
  enable_key_rotation     = true
  deletion_window_in_days = 30

  # Delegates to IAM, so the WorkloadAdmin permission set governs use. Without this statement
  # the account itself could lose control of the key (KMS: default key policy).
  policy = jsonencode({
    Version = "2012-10-17"
    Statement = [{
      Sid       = "EnableIamPolicies"
      Effect    = "Allow"
      Principal = { AWS = "arn:aws:iam::${var.account_id}:root" }
      Action    = "kms:*"
      Resource  = "*"
    }]
  })

  lifecycle {
    prevent_destroy = true
  }
}

resource "aws_kms_alias" "main" {
  name          = "alias/garminagent"
  target_key_id = aws_kms_key.main.key_id
}

# Management account (stage B1): the organization, the workload OU and its one member account,
# centralized root access, IAM Identity Center permission sets, budgets, the organization trail
# and this root's own state bucket. B2 adds the Bedrock SCPs. Steps the owner does by hand, in
# order: docs/aws-b1-checklist.md.
#
# Resource addresses never contain an account ID, email or name from tfvars: plan_summary.py
# prints addresses, and its output is what the owner shares.

locals {
  region     = "eu-north-1"
  trail_name = "garminagent-org"

  # Built rather than read from aws_cloudtrail.org, because the trail bucket's policy must exist
  # before the trail and refers to it (aws:SourceArn).
  trail_arn = "arn:aws:cloudtrail:${local.region}:${var.account_id}:trail/${local.trail_name}"
}

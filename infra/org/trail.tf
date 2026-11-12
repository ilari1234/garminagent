# Organization trail: management events from every account and Region, to the management
# account's bucket. Member accounts can see it but can't stop, change or delete it (CloudTrail:
# creating a trail for an organization). The first copy of management events is free.

# Not sent to CloudWatch Logs (ingestion and storage cost; nothing alerts on it in B1) and not
# encrypted with a KMS key (see buckets.tf). Log file validation detects altered or deleted files.
#trivy:ignore:AVD-AWS-0162
#trivy:ignore:AVD-AWS-0015
resource "aws_cloudtrail" "org" {
  name           = local.trail_name
  s3_bucket_name = aws_s3_bucket.this["trail"].id

  is_organization_trail         = true
  is_multi_region_trail         = true
  include_global_service_events = true
  enable_log_file_validation    = true

  depends_on = [
    aws_s3_bucket_policy.trail,
    aws_organizations_organization.this,
  ]
}

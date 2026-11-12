# The management account's two buckets: this root's state and the organization trail's logs.
# Both private, ACLs disabled, versioned, TLS only. They use SSE-S3 rather than a KMS key: state
# is designed to hold no secrets (only IDs, ARNs and the two emails), access is limited to
# management account admins either way, and the one customer-managed key lives in the member
# account (docs/aws-deployment.md).

locals {
  buckets = {
    state = var.state_bucket_name
    trail = var.trail_bucket_name
  }
}

# Server access logging is off: it needs a third bucket, and the organization trail already
# records every management call on these buckets. Data events (object reads) aren't logged.
#trivy:ignore:AVD-AWS-0089
resource "aws_s3_bucket" "this" {
  for_each = local.buckets

  bucket = each.value

  lifecycle {
    prevent_destroy = true
  }
}

resource "aws_s3_bucket_ownership_controls" "this" {
  for_each = local.buckets

  bucket = aws_s3_bucket.this[each.key].id

  rule {
    object_ownership = "BucketOwnerEnforced"
  }
}

resource "aws_s3_bucket_public_access_block" "this" {
  for_each = local.buckets

  bucket                  = aws_s3_bucket.this[each.key].id
  block_public_acls       = true
  block_public_policy     = true
  ignore_public_acls      = true
  restrict_public_buckets = true
}

# SSE-S3 by choice, see the top of this file.
#trivy:ignore:AVD-AWS-0132
resource "aws_s3_bucket_server_side_encryption_configuration" "this" {
  for_each = local.buckets

  bucket = aws_s3_bucket.this[each.key].id

  rule {
    apply_server_side_encryption_by_default {
      sse_algorithm = "AES256"
    }
  }
}

resource "aws_s3_bucket_versioning" "this" {
  for_each = local.buckets

  bucket = aws_s3_bucket.this[each.key].id

  versioning_configuration {
    status = "Enabled"
  }
}

resource "aws_s3_bucket_lifecycle_configuration" "this" {
  for_each = local.buckets

  bucket = aws_s3_bucket.this[each.key].id

  # State: old versions are the undo history for a bad apply. Trail: log files are kept a year,
  # and a deleted or overwritten one stays recoverable for 90 days.
  rule {
    id     = "expire"
    status = "Enabled"

    filter {}

    dynamic "expiration" {
      for_each = each.key == "trail" ? [365] : []
      content {
        days = expiration.value
      }
    }

    noncurrent_version_expiration {
      noncurrent_days = 90
    }

    abort_incomplete_multipart_upload {
      days_after_initiation = 7
    }
  }

  depends_on = [aws_s3_bucket_versioning.this]
}

locals {
  deny_insecure_transport = {
    for key, bucket in aws_s3_bucket.this : key => {
      Sid       = "DenyInsecureTransport"
      Effect    = "Deny"
      Principal = "*"
      Action    = "s3:*"
      Resource  = [bucket.arn, "${bucket.arn}/*"]
      Condition = { Bool = { "aws:SecureTransport" = "false" } }
    }
  }
}

resource "aws_s3_bucket_policy" "state" {
  bucket = aws_s3_bucket.this["state"].id

  policy = jsonencode({
    Version   = "2012-10-17"
    Statement = [local.deny_insecure_transport["state"]]
  })

  depends_on = [aws_s3_bucket_public_access_block.this]
}

# AWS's organization trail bucket policy (CloudTrail: S3 bucket policy for CloudTrail, checked
# 2026-11-09), with aws:SourceArn pinned to this trail, plus TLS only.
resource "aws_s3_bucket_policy" "trail" {
  bucket = aws_s3_bucket.this["trail"].id

  policy = jsonencode({
    Version = "2012-10-17"
    Statement = [
      local.deny_insecure_transport["trail"],
      {
        Sid       = "AWSCloudTrailAclCheck"
        Effect    = "Allow"
        Principal = { Service = "cloudtrail.amazonaws.com" }
        Action    = "s3:GetBucketAcl"
        Resource  = aws_s3_bucket.this["trail"].arn
        Condition = { StringEquals = { "aws:SourceArn" = local.trail_arn } }
      },
      {
        Sid       = "AWSCloudTrailWrite"
        Effect    = "Allow"
        Principal = { Service = "cloudtrail.amazonaws.com" }
        Action    = "s3:PutObject"
        Resource = [
          "${aws_s3_bucket.this["trail"].arn}/AWSLogs/${var.account_id}/*",
          "${aws_s3_bucket.this["trail"].arn}/AWSLogs/${aws_organizations_organization.this.id}/*",
        ]
        Condition = {
          StringEquals = {
            "s3:x-amz-acl"  = "bucket-owner-full-control"
            "aws:SourceArn" = local.trail_arn
          }
        }
      },
    ]
  })

  depends_on = [aws_s3_bucket_public_access_block.this]
}

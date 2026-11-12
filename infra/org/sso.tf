# IAM Identity Center. The organization instance, its MFA settings and the one user are set up
# by hand: CreateInstance can't create an organization instance and MFA settings have no API
# (checked 2026-11-09; docs/aws-b1-checklist.md). Terraform owns the permission sets and the
# account assignments.

data "aws_ssoadmin_instances" "this" {
  lifecycle {
    postcondition {
      condition     = length(self.arns) == 1
      error_message = "Expected exactly one IAM Identity Center instance. Enable the organization instance in eu-north-1 first (docs/aws-b1-checklist.md)."
    }
  }
}

locals {
  sso_instance_arn  = tolist(data.aws_ssoadmin_instances.this.arns)[0]
  identity_store_id = tolist(data.aws_ssoadmin_instances.this.identity_store_ids)[0]

  # Role sessions outlive a revoked sign-in until they expire (Identity Center: authentication
  # sessions), so they stay short.
  admin_session_duration = "PT1H"
}

data "aws_identitystore_user" "owner" {
  identity_store_id = local.identity_store_id

  alternate_identifier {
    unique_attribute {
      attribute_path  = "UserName"
      attribute_value = var.sso_user_name
    }
  }
}

# Administrator in the management account, without Bedrock. SCPs don't bind the management
# account, so this inline deny stops accidental model calls from the admin set. It is a
# guardrail, not a boundary: an admin can still create a role that calls Bedrock, and the trail
# records that.
resource "aws_ssoadmin_permission_set" "management_admin" {
  name             = "ManagementAdmin"
  description      = "Administrator in the management account; Bedrock denied."
  instance_arn     = local.sso_instance_arn
  session_duration = local.admin_session_duration
}

resource "aws_ssoadmin_managed_policy_attachment" "management_admin" {
  instance_arn       = local.sso_instance_arn
  permission_set_arn = aws_ssoadmin_permission_set.management_admin.arn
  managed_policy_arn = "arn:aws:iam::aws:policy/AdministratorAccess"
}

resource "aws_ssoadmin_permission_set_inline_policy" "management_admin" {
  instance_arn       = local.sso_instance_arn
  permission_set_arn = aws_ssoadmin_permission_set.management_admin.arn

  inline_policy = jsonencode({
    Version = "2012-10-17"
    Statement = [{
      Sid      = "NoBedrockInManagementAccount"
      Effect   = "Deny"
      Action   = ["bedrock:*", "bedrock-mantle:*"]
      Resource = "*"
    }]
  })
}

# Administrator in the member account. B2 adds a narrower BedrockUser set.
resource "aws_ssoadmin_permission_set" "workload_admin" {
  name             = "WorkloadAdmin"
  description      = "Administrator in the workload account."
  instance_arn     = local.sso_instance_arn
  session_duration = local.admin_session_duration
}

resource "aws_ssoadmin_managed_policy_attachment" "workload_admin" {
  instance_arn       = local.sso_instance_arn
  permission_set_arn = aws_ssoadmin_permission_set.workload_admin.arn
  managed_policy_arn = "arn:aws:iam::aws:policy/AdministratorAccess"
}

resource "aws_ssoadmin_account_assignment" "management_admin" {
  instance_arn       = local.sso_instance_arn
  permission_set_arn = aws_ssoadmin_permission_set.management_admin.arn
  principal_type     = "USER"
  principal_id       = data.aws_identitystore_user.owner.user_id
  target_type        = "AWS_ACCOUNT"
  target_id          = var.account_id

  # The inline deny must be in place before the set can be used.
  depends_on = [aws_ssoadmin_permission_set_inline_policy.management_admin]
}

resource "aws_ssoadmin_account_assignment" "workload_admin" {
  instance_arn       = local.sso_instance_arn
  permission_set_arn = aws_ssoadmin_permission_set.workload_admin.arn
  principal_type     = "USER"
  principal_id       = data.aws_identitystore_user.owner.user_id
  target_type        = "AWS_ACCOUNT"
  target_id          = aws_organizations_account.workload.id
}

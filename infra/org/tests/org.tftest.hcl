# Asserts the B1 controls in infra/org. `command = apply` runs against the mocked provider
# only: no credentials, no API calls, nothing created. All values are synthetic.
mock_provider "aws" {
  source = "./tests/mocks"
}

variables {
  account_id             = "000000000000"
  workload_account_email = "workload@example.invalid"
  sso_user_name          = "owner"
  budget_email           = "budget@example.invalid"
  state_bucket_name      = "example-org-tfstate"
  trail_bucket_name      = "example-org-trail"
}

run "organization" {
  command = apply

  assert {
    condition     = aws_organizations_organization.this.feature_set == "ALL"
    error_message = "The organization needs all features for SCPs and the organization trail."
  }

  assert {
    condition     = contains(aws_organizations_organization.this.enabled_policy_types, "SERVICE_CONTROL_POLICY")
    error_message = "SCPs must be enabled."
  }

  assert {
    condition = toset(aws_organizations_organization.this.aws_service_access_principals) == toset([
      "cloudtrail.amazonaws.com", "iam.amazonaws.com", "sso.amazonaws.com",
    ])
    error_message = "Trusted access must be exactly CloudTrail, IAM and Identity Center."
  }

  assert {
    condition     = aws_organizations_organizational_unit.workload.name == "workload"
    error_message = "The OU is called workload."
  }

  assert {
    condition     = aws_organizations_account.workload.parent_id == aws_organizations_organizational_unit.workload.id
    error_message = "The member account belongs in the workload OU, where B2's SCPs attach."
  }

  assert {
    condition     = aws_organizations_account.workload.close_on_deletion == false
    error_message = "Removing the account from Terraform must not close it."
  }

  assert {
    condition = toset(aws_iam_organizations_features.root_access.enabled_features) == toset([
      "RootCredentialsManagement", "RootSessions",
    ])
    error_message = "Centralized root access must be on, so member accounts have no root credentials."
  }
}

run "identity_center" {
  command = apply

  assert {
    condition = alltrue([
      for ps in [aws_ssoadmin_permission_set.management_admin, aws_ssoadmin_permission_set.workload_admin] :
      ps.session_duration == "PT1H"
    ])
    error_message = "Admin role sessions last one hour at most."
  }

  assert {
    condition = (
      contains(jsondecode(aws_ssoadmin_permission_set_inline_policy.management_admin.inline_policy).Statement[0].Action, "bedrock:*") &&
      contains(jsondecode(aws_ssoadmin_permission_set_inline_policy.management_admin.inline_policy).Statement[0].Action, "bedrock-mantle:*") &&
      jsondecode(aws_ssoadmin_permission_set_inline_policy.management_admin.inline_policy).Statement[0].Effect == "Deny" &&
      jsondecode(aws_ssoadmin_permission_set_inline_policy.management_admin.inline_policy).Statement[0].Resource == "*"
    )
    error_message = "ManagementAdmin must deny all of Bedrock: SCPs don't bind the management account."
  }

  assert {
    condition     = aws_ssoadmin_account_assignment.management_admin.target_id == var.account_id
    error_message = "ManagementAdmin is assigned in the management account."
  }

  assert {
    condition     = aws_ssoadmin_account_assignment.workload_admin.target_id == aws_organizations_account.workload.id
    error_message = "WorkloadAdmin is assigned in the member account."
  }

  assert {
    condition = alltrue([
      for a in [aws_ssoadmin_account_assignment.management_admin, aws_ssoadmin_account_assignment.workload_admin] :
      a.principal_type == "USER" && a.principal_id == data.aws_identitystore_user.owner.user_id
    ])
    error_message = "Both sets are assigned to the one user, and to nobody else."
  }
}

run "budgets" {
  command = apply

  assert {
    condition     = aws_budgets_budget.monthly.budget_type == "COST" && aws_budgets_budget.monthly.limit_amount == "25.00" && aws_budgets_budget.monthly.time_unit == "MONTHLY"
    error_message = "The budget is $25 a month on cost."
  }

  assert {
    condition     = one(aws_budgets_budget.monthly.cost_types).include_credit == false
    error_message = "Credits must not hide spend."
  }

  assert {
    condition = toset([
      for n in aws_budgets_budget.monthly.notification : "${n.notification_type}:${n.threshold}:${n.threshold_type}"
      ]) == toset([
      "ACTUAL:50:PERCENTAGE", "ACTUAL:80:PERCENTAGE", "ACTUAL:100:PERCENTAGE",
      "FORECASTED:50:PERCENTAGE", "FORECASTED:80:PERCENTAGE", "FORECASTED:100:PERCENTAGE",
    ])
    error_message = "Alerts at 50, 80 and 100 % of actual and forecast spend."
  }

  assert {
    condition = alltrue([
      for n in aws_budgets_budget.monthly.notification : n.subscriber_email_addresses == toset([var.budget_email])
    ])
    error_message = "Every alert goes to the budget address."
  }

  assert {
    condition = (
      aws_budgets_budget_action.deny_invoke.action_type == "APPLY_SCP_POLICY" &&
      aws_budgets_budget_action.deny_invoke.approval_model == "AUTOMATIC" &&
      aws_budgets_budget_action.deny_invoke.notification_type == "ACTUAL" &&
      one(aws_budgets_budget_action.deny_invoke.action_threshold).action_threshold_type == "ABSOLUTE_VALUE" &&
      one(aws_budgets_budget_action.deny_invoke.action_threshold).action_threshold_value == 50
    )
    error_message = "At $50 of actual spend the SCP is attached automatically."
  }

  assert {
    condition = (
      one(one(aws_budgets_budget_action.deny_invoke.definition).scp_action_definition).policy_id == aws_organizations_policy.budget_deny_invoke.id &&
      one(one(aws_budgets_budget_action.deny_invoke.definition).scp_action_definition).target_ids == toset([aws_organizations_organizational_unit.workload.id])
    )
    error_message = "The action attaches the deny-invoke SCP to the workload OU only."
  }

  assert {
    condition = (
      jsondecode(aws_organizations_policy.budget_deny_invoke.content).Statement[0].Effect == "Deny" &&
      toset(jsondecode(aws_organizations_policy.budget_deny_invoke.content).Statement[0].Action) == toset([
        "bedrock:Invoke*", "bedrock:StartAsyncInvoke", "bedrock:CreateModelInvocationJob", "bedrock-mantle:*",
      ])
    )
    error_message = "The SCP denies every model invocation path."
  }

  assert {
    condition = toset(jsondecode(aws_iam_role_policy.budget_action.policy).Statement[0].Resource) == toset([
      aws_organizations_policy.budget_deny_invoke.arn, aws_organizations_organizational_unit.workload.arn,
    ])
    error_message = "The budget role may move only the one SCP on the one OU."
  }

  assert {
    condition = (
      jsondecode(aws_iam_role.budget_action.assume_role_policy).Statement[0].Principal.Service == "budgets.amazonaws.com" &&
      jsondecode(aws_iam_role.budget_action.assume_role_policy).Statement[0].Condition.StringEquals["aws:SourceAccount"] == var.account_id &&
      toset(jsondecode(aws_iam_role.budget_action.assume_role_policy).Statement[0].Condition.ArnLike["aws:SourceArn"]) == toset([
        "arn:aws:budgets::000000000000:budget/garminagent-monthly",
        "arn:aws:budgets::000000000000:budget/garminagent-monthly/action/*",
      ])
    )
    error_message = "Only this account's budget, or an action on it, may assume the role."
  }
}

run "buckets_and_trail" {
  command = apply

  assert {
    condition = alltrue([
      for b in aws_s3_bucket_public_access_block.this :
      b.block_public_acls && b.block_public_policy && b.ignore_public_acls && b.restrict_public_buckets
    ])
    error_message = "Both buckets block all public access."
  }

  assert {
    condition     = alltrue([for c in aws_s3_bucket_ownership_controls.this : one(c.rule).object_ownership == "BucketOwnerEnforced"])
    error_message = "ACLs are disabled on both buckets."
  }

  assert {
    condition     = alltrue([for v in aws_s3_bucket_versioning.this : one(v.versioning_configuration).status == "Enabled"])
    error_message = "Both buckets are versioned."
  }

  assert {
    condition = alltrue([
      for key in ["state", "trail"] :
      contains([
        for s in jsondecode(key == "state" ? aws_s3_bucket_policy.state.policy : aws_s3_bucket_policy.trail.policy).Statement :
        s.Sid if s.Effect == "Deny" && s.Condition.Bool["aws:SecureTransport"] == "false"
      ], "DenyInsecureTransport")
    ])
    error_message = "Both buckets refuse plain HTTP."
  }

  assert {
    condition = alltrue([
      for s in jsondecode(aws_s3_bucket_policy.trail.policy).Statement :
      s.Condition.StringEquals["aws:SourceArn"] == local.trail_arn if s.Effect == "Allow"
    ])
    error_message = "CloudTrail may write only for this trail (aws:SourceArn)."
  }

  assert {
    condition = (
      aws_cloudtrail.org.is_organization_trail &&
      aws_cloudtrail.org.is_multi_region_trail &&
      aws_cloudtrail.org.include_global_service_events &&
      aws_cloudtrail.org.enable_log_file_validation
    )
    error_message = "One multi-Region organization trail with log file validation."
  }

  assert {
    condition     = local.trail_arn == "arn:aws:cloudtrail:eu-north-1:000000000000:trail/garminagent-org" && aws_cloudtrail.org.name == local.trail_name
    error_message = "The bucket policy's aws:SourceArn must name the trail as created."
  }
}

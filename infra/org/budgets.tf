# Budgets in the management account cover the whole organization. Total cost rather than a
# Bedrock filter, because Claude charges appear under the model provider through AWS
# Marketplace. Billing data lags by hours, so the token and iteration caps in code (ADR 0007)
# stay the main control; this is the backstop.

locals {
  budget_name = "garminagent-monthly"

  # 50, 80 and 100 % of the limit, each on actual and forecast spend.
  budget_alerts = [
    for pair in setproduct([50, 80, 100], ["ACTUAL", "FORECASTED"]) : {
      threshold = pair[0]
      type      = pair[1]
    }
  ]
}

resource "aws_budgets_budget" "monthly" {
  name         = local.budget_name
  budget_type  = "COST"
  limit_amount = format("%.2f", var.budget_limit_usd)
  limit_unit   = "USD"
  time_unit    = "MONTHLY"

  # Gross usage: credits would hide spend that continues once they run out (docs/aws-deployment.md,
  # Free plan accounts).
  cost_types {
    include_credit = false
    include_refund = false
  }

  dynamic "notification" {
    for_each = local.budget_alerts
    content {
      comparison_operator        = "GREATER_THAN"
      threshold                  = notification.value.threshold
      threshold_type             = "PERCENTAGE"
      notification_type          = notification.value.type
      subscriber_email_addresses = [var.budget_email]
    }
  }
}

# Attached by the budget action only: denies model invocation in the workload OU. Detach it by
# reversing the action in the Budgets console once the cause is understood.
resource "aws_organizations_policy" "budget_deny_invoke" {
  name        = "budget-deny-invoke"
  description = "Attached by the budget action when actual spend passes the action threshold."
  type        = "SERVICE_CONTROL_POLICY"

  content = jsonencode({
    Version = "2012-10-17"
    Statement = [{
      Sid    = "DenyModelInvocation"
      Effect = "Deny"
      # bedrock:InvokeModel also authorizes Converse, and InvokeModelWithResponseStream
      # ConverseStream (Bedrock: inference prerequisites). The mantle endpoint is denied whole.
      Action = [
        "bedrock:Invoke*",
        "bedrock:StartAsyncInvoke",
        "bedrock:CreateModelInvocationJob",
        "bedrock-mantle:*",
      ]
      Resource = "*"
    }]
  })
}

resource "aws_iam_role" "budget_action" {
  name        = "garminagent-budget-action"
  description = "Assumed by AWS Budgets to attach and detach the deny-invoke SCP."

  assume_role_policy = jsonencode({
    Version = "2012-10-17"
    Statement = [{
      Effect    = "Allow"
      Principal = { Service = "budgets.amazonaws.com" }
      Action    = "sts:AssumeRole"
      # Confused-deputy guard: only this account's budget, or an action on it, may use the role.
      # AWS's example uses budget/*; which ARN Budgets presents when it runs an action isn't
      # documented, so both forms are allowed and the checklist (section 8) fires the action once.
      Condition = {
        StringEquals = { "aws:SourceAccount" = var.account_id }
        ArnLike = { "aws:SourceArn" = [
          "arn:aws:budgets::${var.account_id}:budget/${local.budget_name}",
          "arn:aws:budgets::${var.account_id}:budget/${local.budget_name}/action/*",
        ] }
      }
    }]
  })
}

# Narrower than AWS's example policy (Cost Management: policy examples), which allows any
# attach or detach: this one only moves the one SCP on the one OU.
resource "aws_iam_role_policy" "budget_action" {
  name = "attach-deny-invoke-scp"
  role = aws_iam_role.budget_action.id

  policy = jsonencode({
    Version = "2012-10-17"
    Statement = [{
      Effect = "Allow"
      Action = ["organizations:AttachPolicy", "organizations:DetachPolicy"]
      Resource = [
        aws_organizations_policy.budget_deny_invoke.arn,
        aws_organizations_organizational_unit.workload.arn,
      ]
    }]
  })
}

resource "aws_budgets_budget_action" "deny_invoke" {
  budget_name        = aws_budgets_budget.monthly.name
  action_type        = "APPLY_SCP_POLICY"
  approval_model     = "AUTOMATIC"
  notification_type  = "ACTUAL"
  execution_role_arn = aws_iam_role.budget_action.arn

  action_threshold {
    action_threshold_type  = "ABSOLUTE_VALUE"
    action_threshold_value = var.budget_action_usd
  }

  definition {
    scp_action_definition {
      policy_id  = aws_organizations_policy.budget_deny_invoke.id
      target_ids = [aws_organizations_organizational_unit.workload.id]
    }
  }

  subscriber {
    address           = var.budget_email
    subscription_type = "EMAIL"
  }

  depends_on = [aws_iam_role_policy.budget_action]
}

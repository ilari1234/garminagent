variable "account_id" {
  description = "ID of the management account, the only account this root may change."
  type        = string

  validation {
    condition     = can(regex("^[0-9]{12}$", var.account_id))
    error_message = "account_id must be a 12-digit AWS account ID."
  }
}

variable "workload_account_email" {
  description = "Root email address of the member account. Personal data: kept in the gitignored tfvars and hidden from plan output."
  type        = string
  sensitive   = true

  validation {
    condition     = can(regex("^[^@\\s<>]+@[^@\\s<>]+\\.[^@\\s<>]+$", var.workload_account_email))
    error_message = "workload_account_email must be an email address."
  }
}

variable "workload_account_name" {
  description = "Display name of the member account."
  type        = string
  default     = "garminagent-workload"

  validation {
    condition     = can(regex("^[A-Za-z0-9 ._-]{1,50}$", var.workload_account_name))
    error_message = "workload_account_name must be 1-50 letters, digits, spaces, dots, underscores or hyphens."
  }
}

variable "sso_user_name" {
  description = "User name of the one IAM Identity Center user, created by hand before the first apply (docs/aws-b1-checklist.md)."
  type        = string
  sensitive   = true

  validation {
    condition     = can(regex("^[A-Za-z0-9+=,.@_-]{1,128}$", var.sso_user_name))
    error_message = "sso_user_name must be an Identity Center user name (letters, digits and +=,.@_-)."
  }
}

variable "budget_email" {
  description = "Address that receives budget alerts and budget action notices. Personal data: kept in the gitignored tfvars."
  type        = string
  sensitive   = true

  validation {
    condition     = can(regex("^[^@\\s<>]+@[^@\\s<>]+\\.[^@\\s<>]+$", var.budget_email))
    error_message = "budget_email must be an email address."
  }
}

variable "budget_limit_usd" {
  description = "Monthly budget on total cost, in USD. Alerts fire at 50, 80 and 100 % of actual and forecast spend."
  type        = number
  default     = 25

  validation {
    condition     = var.budget_limit_usd > 0
    error_message = "budget_limit_usd must be positive."
  }
}

variable "budget_action_usd" {
  description = "Actual monthly spend, in USD, at which the deny-invoke SCP is attached to the workload OU."
  type        = number
  default     = 50

  validation {
    condition     = var.budget_action_usd > var.budget_limit_usd
    error_message = "budget_action_usd must be above budget_limit_usd, so the alerts come first."
  }
}

variable "state_bucket_name" {
  description = "Name of this root's own state bucket. Must match `bucket` in backend.hcl."
  type        = string

  validation {
    condition     = can(regex("^[a-z0-9][a-z0-9-]{1,61}[a-z0-9]$", var.state_bucket_name))
    error_message = "state_bucket_name must be 3-63 lowercase letters, digits or hyphens."
  }
}

variable "trail_bucket_name" {
  description = "Name of the bucket that receives the organization trail's log files."
  type        = string

  validation {
    condition     = can(regex("^[a-z0-9][a-z0-9-]{1,61}[a-z0-9]$", var.trail_bucket_name))
    error_message = "trail_bucket_name must be 3-63 lowercase letters, digits or hyphens."
  }
}

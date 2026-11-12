variable "account_id" {
  description = "ID of the member account, the only account this root may change."
  type        = string

  validation {
    condition     = can(regex("^[0-9]{12}$", var.account_id))
    error_message = "account_id must be a 12-digit AWS account ID."
  }
}

variable "state_bucket_name" {
  description = "Name of the member account's state bucket (this root and infra/workload). Must match `bucket` in backend.hcl."
  type        = string

  validation {
    condition     = can(regex("^[a-z0-9][a-z0-9-]{1,61}[a-z0-9]$", var.state_bucket_name))
    error_message = "state_bucket_name must be 3-63 lowercase letters, digits or hyphens."
  }
}

# The GitHub repository the OIDC role trusts. Repositories created after 15 July 2026, like this
# one, put immutable owner and repository IDs in the token's subject (GitHub: OIDC reference,
# checked 2026-11-09). Names and IDs are public; they come from
# `gh api repos/OWNER/REPO --jq '{owner: .owner.login, owner_id: .owner.id, repo: .name, repo_id: .id}'`.
variable "github_owner" {
  description = "GitHub user or organization that owns the repository."
  type        = string

  validation {
    condition     = can(regex("^[A-Za-z0-9](?:[A-Za-z0-9-]{0,38})$", var.github_owner))
    error_message = "github_owner must be a GitHub user or organization name."
  }
}

variable "github_owner_id" {
  description = "Numeric ID of the repository owner."
  type        = string

  validation {
    condition     = can(regex("^[0-9]{1,20}$", var.github_owner_id))
    error_message = "github_owner_id must be the owner's numeric GitHub ID."
  }
}

variable "github_repository" {
  description = "Repository name, without the owner."
  type        = string

  validation {
    condition     = can(regex("^[A-Za-z0-9._-]{1,100}$", var.github_repository))
    error_message = "github_repository must be a repository name without the owner."
  }
}

variable "github_repository_id" {
  description = "Numeric ID of the repository."
  type        = string

  validation {
    condition     = can(regex("^[0-9]{1,20}$", var.github_repository_id))
    error_message = "github_repository_id must be the repository's numeric GitHub ID."
  }
}

variable "github_environment" {
  description = "GitHub environment the ECR push job runs in (B3c). Only main may deploy to it."
  type        = string
  default     = "ecr-push"

  validation {
    condition     = can(regex("^[A-Za-z0-9._-]{1,255}$", var.github_environment))
    error_message = "github_environment must be a plain environment name."
  }
}

# GitHub Actions OIDC: the only link from public CI to AWS. In B1 the role has no permissions at
# all; B3c gives it ECR push on one repository. No thumbprint: IAM trusts GitHub's certificate
# authority itself, and the provider no longer requires one.

resource "aws_iam_openid_connect_provider" "github" {
  url            = "https://token.actions.githubusercontent.com"
  client_id_list = ["sts.amazonaws.com"]
}

locals {
  # Immutable subject format: owner and repository IDs survive renames and can't be taken over
  # by someone who later registers a freed name. Bound to the GitHub environment, which only
  # main may deploy to. Confirmed on the first run in B3c (docs/aws-b1-checklist.md).
  github_oidc_subject = format(
    "repo:%s@%s/%s@%s:environment:%s",
    var.github_owner, var.github_owner_id, var.github_repository, var.github_repository_id, var.github_environment,
  )
}

resource "aws_iam_role" "gha_ecr_push" {
  name                 = "gha-ecr-push"
  description          = "Assumed by GitHub Actions through OIDC. No permissions until B3c (ECR push to one repository)."
  max_session_duration = 3600

  assume_role_policy = jsonencode({
    Version = "2012-10-17"
    Statement = [{
      Effect    = "Allow"
      Principal = { Federated = aws_iam_openid_connect_provider.github.arn }
      Action    = "sts:AssumeRoleWithWebIdentity"
      # StringEquals, never StringLike: exactly one subject.
      Condition = {
        StringEquals = {
          "token.actions.githubusercontent.com:aud" = "sts.amazonaws.com"
          "token.actions.githubusercontent.com:sub" = local.github_oidc_subject
        }
      }
    }]
  })
}

# Deliberately nothing attached to gha_ecr_push in B1: no aws_iam_role_policy,
# aws_iam_role_policy_attachment or permissions boundary. tests/account.tftest.hcl checks the
# trust policy; review checks that this file gains no policy before B3c.

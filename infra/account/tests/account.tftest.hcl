# Asserts the B1 controls in infra/account. `command = apply` runs against the mocked provider
# only: no credentials, no API calls, nothing created. All values are synthetic.
mock_provider "aws" {
  source = "./tests/mocks"
}

variables {
  account_id           = "000000000000"
  state_bucket_name    = "example-account-tfstate"
  github_owner         = "example-owner"
  github_owner_id      = "1000"
  github_repository    = "example-repo"
  github_repository_id = "2000"
}

run "kms_key" {
  command = apply

  assert {
    condition     = aws_kms_key.main.enable_key_rotation && aws_kms_key.main.deletion_window_in_days == 30
    error_message = "The key rotates yearly and can be recovered for 30 days after scheduling deletion."
  }

  assert {
    condition = (
      length(jsondecode(aws_kms_key.main.policy).Statement) == 1 &&
      jsondecode(aws_kms_key.main.policy).Statement[0].Principal.AWS == "arn:aws:iam::000000000000:root"
    )
    error_message = "The key policy trusts only this account (through IAM) in B1."
  }
}

run "state_bucket" {
  command = apply

  assert {
    condition = (
      aws_s3_bucket_public_access_block.state.block_public_acls &&
      aws_s3_bucket_public_access_block.state.block_public_policy &&
      aws_s3_bucket_public_access_block.state.ignore_public_acls &&
      aws_s3_bucket_public_access_block.state.restrict_public_buckets
    )
    error_message = "The state bucket blocks all public access."
  }

  assert {
    condition     = one(aws_s3_bucket_ownership_controls.state.rule).object_ownership == "BucketOwnerEnforced"
    error_message = "ACLs are disabled."
  }

  assert {
    condition     = one(aws_s3_bucket_versioning.state.versioning_configuration).status == "Enabled"
    error_message = "State is versioned."
  }

  assert {
    condition = (
      one(one(aws_s3_bucket_server_side_encryption_configuration.state.rule).apply_server_side_encryption_by_default).sse_algorithm == "aws:kms" &&
      one(one(aws_s3_bucket_server_side_encryption_configuration.state.rule).apply_server_side_encryption_by_default).kms_master_key_id == aws_kms_key.main.arn
    )
    error_message = "State is encrypted with the account's own KMS key."
  }

  assert {
    condition = toset([for s in jsondecode(aws_s3_bucket_policy.state.policy).Statement : s.Sid]) == toset([
      "DenyInsecureTransport", "DenyOtherEncryption", "DenyOtherKmsKeys", "DenyKmsWithoutKeyId",
    ])
    error_message = "The bucket policy refuses plain HTTP and other encryption."
  }

  assert {
    condition = alltrue([
      for s in jsondecode(aws_s3_bucket_policy.state.policy).Statement : s.Effect == "Deny"
    ])
    error_message = "The bucket policy only denies; access comes from IAM."
  }
}

run "github_oidc" {
  command = apply

  assert {
    condition = (
      aws_iam_openid_connect_provider.github.url == "https://token.actions.githubusercontent.com" &&
      toset(aws_iam_openid_connect_provider.github.client_id_list) == toset(["sts.amazonaws.com"])
    )
    error_message = "One GitHub provider, audience sts.amazonaws.com."
  }

  assert {
    condition = (
      jsondecode(aws_iam_role.gha_ecr_push.assume_role_policy).Statement[0].Condition.StringEquals["token.actions.githubusercontent.com:sub"] ==
      "repo:example-owner@1000/example-repo@2000:environment:ecr-push"
    )
    error_message = "The role trusts exactly one subject: this repository by ID, in the ecr-push environment."
  }

  assert {
    condition     = jsondecode(aws_iam_role.gha_ecr_push.assume_role_policy).Statement[0].Condition.StringEquals["token.actions.githubusercontent.com:aud"] == "sts.amazonaws.com"
    error_message = "The audience is pinned."
  }

  assert {
    condition     = keys(jsondecode(aws_iam_role.gha_ecr_push.assume_role_policy).Statement[0].Condition) == ["StringEquals"]
    error_message = "No StringLike or other wildcard condition in the trust policy."
  }

  assert {
    condition = (
      jsondecode(aws_iam_role.gha_ecr_push.assume_role_policy).Statement[0].Principal.Federated == aws_iam_openid_connect_provider.github.arn &&
      jsondecode(aws_iam_role.gha_ecr_push.assume_role_policy).Statement[0].Action == "sts:AssumeRoleWithWebIdentity"
    )
    error_message = "Only GitHub's OIDC provider, through AssumeRoleWithWebIdentity."
  }

  assert {
    condition     = length(jsondecode(aws_iam_role.gha_ecr_push.assume_role_policy).Statement) == 1
    error_message = "The trust policy has one statement."
  }

  assert {
    condition     = aws_iam_role.gha_ecr_push.max_session_duration == 3600
    error_message = "CI sessions last one hour at most."
  }
}

run "access_analyzer" {
  command = apply

  assert {
    condition     = aws_accessanalyzer_analyzer.external.type == "ACCOUNT"
    error_message = "An external access analyzer for the member account."
  }
}

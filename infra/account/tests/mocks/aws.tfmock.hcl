# Synthetic values for the mocked aws provider: ARN-shaped where the provider validates ARNs.

mock_resource "aws_kms_key" {
  defaults = {
    arn    = "arn:aws:kms:eu-north-1:000000000000:key/00000000-0000-0000-0000-000000000000"
    key_id = "00000000-0000-0000-0000-000000000000"
  }
}

mock_resource "aws_s3_bucket" {
  defaults = {
    arn = "arn:aws:s3:::example-account-tfstate"
  }
}

mock_resource "aws_iam_openid_connect_provider" {
  defaults = {
    arn = "arn:aws:iam::000000000000:oidc-provider/token.actions.githubusercontent.com"
  }
}

mock_resource "aws_iam_role" {
  defaults = {
    arn = "arn:aws:iam::000000000000:role/gha-ecr-push"
  }
}

# Runs without AWS credentials: the provider is mocked and nothing leaves the machine.
mock_provider "aws" {}

variables {
  account_id = "000000000000"
}

run "accepts_a_12_digit_account_id" {
  command = plan
}

run "rejects_a_malformed_account_id" {
  command = plan

  variables {
    account_id = "12345"
  }

  expect_failures = [var.account_id]
}

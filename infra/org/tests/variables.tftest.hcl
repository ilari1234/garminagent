# Runs without AWS credentials: the provider is mocked and nothing leaves the machine.
# All values are synthetic.
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

run "accepts_valid_variables" {
  command = plan
}

run "rejects_a_malformed_account_id" {
  command = plan

  variables {
    account_id = "12345"
  }

  expect_failures = [var.account_id]
}

run "rejects_an_unfilled_email_placeholder" {
  command = plan

  variables {
    workload_account_email = "<member account email>"
    budget_email           = "<budget alert email>"
  }

  expect_failures = [var.workload_account_email, var.budget_email]
}

run "rejects_bucket_names_s3_would_refuse" {
  command = plan

  variables {
    state_bucket_name = "<state bucket name>"
    trail_bucket_name = "Upper_Case"
  }

  expect_failures = [var.state_bucket_name, var.trail_bucket_name]
}

# The literal values in terraform.tfvars.example: an unfilled copy must not plan.
run "rejects_the_example_file_placeholders" {
  command = plan

  variables {
    account_id             = "<12-digit management account ID>"
    workload_account_email = "<root email of the member account, unused by any AWS account>"
    sso_user_name          = "<Identity Center user name>"
    budget_email           = "<address for budget alerts>"
    state_bucket_name      = "<org state bucket name>"
    trail_bucket_name      = "<organization trail bucket name>"
  }

  expect_failures = [
    var.account_id,
    var.workload_account_email,
    var.sso_user_name,
    var.budget_email,
    var.state_bucket_name,
    var.trail_bucket_name,
  ]
}

run "rejects_an_action_threshold_below_the_budget" {
  command = plan

  variables {
    budget_limit_usd  = 25
    budget_action_usd = 20
  }

  expect_failures = [var.budget_action_usd]
}

run "fails_without_an_identity_center_instance" {
  command = plan

  override_data {
    target = data.aws_ssoadmin_instances.this
    values = {
      arns               = []
      identity_store_ids = []
    }
  }

  expect_failures = [data.aws_ssoadmin_instances.this]
}

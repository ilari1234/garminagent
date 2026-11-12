# Runs without AWS credentials: the provider is mocked and nothing leaves the machine.
# All values are synthetic.
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

run "rejects_unfilled_placeholders" {
  command = plan

  variables {
    state_bucket_name    = "<state bucket name>"
    github_owner         = "<owner>"
    github_owner_id      = "<owner id>"
    github_repository    = "<owner>/<repo>"
    github_repository_id = "<repo id>"
  }

  expect_failures = [
    var.state_bucket_name,
    var.github_owner,
    var.github_owner_id,
    var.github_repository,
    var.github_repository_id,
  ]
}

# The literal values in terraform.tfvars.example: an unfilled copy must not plan.
run "rejects_the_example_file_placeholders" {
  command = plan

  variables {
    account_id           = "<12-digit member account ID>"
    state_bucket_name    = "<member account state bucket name>"
    github_owner         = "<owner>"
    github_owner_id      = "<owner id>"
    github_repository    = "<repo>"
    github_repository_id = "<repo id>"
  }

  expect_failures = [
    var.account_id,
    var.state_bucket_name,
    var.github_owner,
    var.github_owner_id,
    var.github_repository,
    var.github_repository_id,
  ]
}

run "rejects_a_wildcard_environment" {
  command = plan

  variables {
    github_environment = "*"
  }

  expect_failures = [var.github_environment]
}

provider "aws" {
  region = "eu-north-1"

  # Refuses to plan or apply with credentials for any other account, such as the wrong SSO
  # profile.
  allowed_account_ids = [var.account_id]

  default_tags {
    tags = {
      project    = "garminagent"
      root       = "workload"
      managed-by = "terraform"
    }
  }
}

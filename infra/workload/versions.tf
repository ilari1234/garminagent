terraform {
  # Patch releases only. CI runs the exact version from its digest-pinned image.
  required_version = "~> 1.17.0"

  required_providers {
    aws = {
      source  = "hashicorp/aws"
      version = "6.71.0"
    }
  }

  # Partial configuration: `bucket` and `kms_key_id` come from the gitignored backend.hcl
  # (see backend.hcl.example), because they name the account.
  backend "s3" {
    key          = "workload/terraform.tfstate"
    region       = "eu-north-1"
    encrypt      = true
    use_lockfile = true
  }
}

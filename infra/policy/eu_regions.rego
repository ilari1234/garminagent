# METADATA
# title: AWS provider outside EU Regions
# description: >-
#   Every aws provider block must set its region explicitly, to an EU Region. Health data and
#   prompts stay in the EU (docs/plan.md, data handling); an implicit region would come from the
#   environment of whoever runs Terraform.
# schemas:
#   - input: schema["terraform-raw"]
# custom:
#   id: GA001
#   short_code: aws-provider-eu-region
#   severity: HIGH
#   recommended_actions: Set region to an eu-* Region in the provider block.
#   input:
#     selector:
#       - type: terraform-raw
package user.garminagent.ga001

import rego.v1

aws_providers contains block if {
	some module in input.modules
	some block in module.blocks
	block.kind == "provider"
	block.name == "aws"
}

deny contains res if {
	some block in aws_providers
	not block.attributes.region
	res := result.new("aws provider has no explicit region", block)
}

deny contains res if {
	some block in aws_providers
	region := block.attributes.region
	not eu_region(region)
	res := result.new("aws provider region must be a known eu-* Region", region)
}

eu_region(attr) if {
	attr.known
	is_string(attr.value)
	startswith(attr.value, "eu-")
}

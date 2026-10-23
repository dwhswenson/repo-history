terraform {
  required_version = ">= 1.5.0"

  required_providers {
    github = {
      source  = "registry.terraform.io/integrations/github"
      version = "~> 6.13.0"
    }
  }
}

locals {
  # These identities also appear in the release workflows. Change them together.
  github_owner = "dwhswenson"
  repository   = "repo-history"

  publishers = {
    pypi = {
      package  = "repo-history"
      workflow = "publish-pypi.yaml"
    }
    npm = {
      package  = "@dwhswenson/repo-history-viz"
      workflow = "publish-npm.yaml"
    }
  }
}

# Supply GITHUB_TOKEN in the environment, or authenticate with the GitHub CLI.
# Registry publishing uses OIDC; no PyPI or npm tokens belong in Terraform.
provider "github" {
  owner = local.github_owner
}

# The repository already exists and is deliberately not managed by this root.
resource "github_repository_environment" "publishing" {
  for_each = local.publishers

  repository  = local.repository
  environment = each.key

  # Required reviewers are optional in the release setup and are left unset.
  deployment_branch_policy {
    protected_branches     = false
    custom_branch_policies = true
  }
}

resource "github_repository_environment_deployment_policy" "release_tags" {
  for_each = github_repository_environment.publishing

  repository  = each.value.repository
  environment = each.value.environment
  tag_pattern = "v*"
}

# These are inputs for the remaining registry-side setup, not managed resources.
# See README.md for PyPI setup and the npm trust command.
output "registry_trusted_publisher_settings" {
  description = "Configure these trusted publishers on the registries after applying the GitHub environments."
  value = {
    for registry, publisher in local.publishers : registry => {
      package           = publisher.package
      github_owner      = local.github_owner
      github_repository = local.repository
      workflow_filename = publisher.workflow
      environment       = github_repository_environment.publishing[registry].environment
    }
  }
}

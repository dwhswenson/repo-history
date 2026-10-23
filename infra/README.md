# Publishing infrastructure

`main.tf` manages four resources in the existing `dwhswenson/repo-history` repository:

- GitHub environments named `pypi` and `npm`, with no required reviewers.
- One deployment policy per environment allowing only tags matching `v*`, such as `v0.1.1`.

The GitHub owner, repository, and publisher identities are fixed to match the existing release workflows. If changing them, update the workflows and registry settings together.

## Deploy

Use Terraform 1.5 or later. OpenTofu 1.6 or later also supports this configuration; substitute `tofu` for `terraform` in the commands below.

Authenticate with `gh auth login`, or set `GITHUB_TOKEN` in your shell. The account needs repository administration access. A fine-grained token needs **Administration: write** for `dwhswenson/repo-history`; see the [GitHub environment API permissions](https://docs.github.com/en/rest/deployments/environments#create-or-update-an-environment). Registry publishing tokens are unnecessary.

From the repository root:

```sh
terraform -chdir=infra init
terraform -chdir=infra fmt -check
terraform -chdir=infra validate
terraform -chdir=infra plan -out=publishing.tfplan
terraform -chdir=infra apply publishing.tfplan
```

For a fresh setup, expect two environments and two tag policies to be created. Review the plan before applying, especially when environments already exist: this configuration leaves reviewers and wait timers unset. Terraform only manages the deployment policy resources in its state; remove any pre-existing extra branch or tag rules to enforce `v*` tags exclusively. If the previous `main` branch policies were applied from this configuration, Terraform will replace them with the new tag policies.

The default backend stores state locally. Keep the state and backups between runs, or configure a shared backend before collaborating. State, saved plans, and variable files are ignored; keep `.terraform.lock.hcl` under version control after initialization.

## Existing environments and policies

Import existing resources before planning, instead of creating duplicate deployment policies:

```sh
terraform -chdir=infra import 'github_repository_environment.publishing["pypi"]' 'repo-history:pypi'
terraform -chdir=infra import 'github_repository_environment.publishing["npm"]' 'repo-history:npm'
```

Find existing deployment policy IDs with the GitHub CLI:

```sh
gh api repos/dwhswenson/repo-history/environments/pypi/deployment-branch-policies
gh api repos/dwhswenson/repo-history/environments/npm/deployment-branch-policies
```

For each existing **tag** policy named `v*`, substitute its numeric ID below:

```sh
terraform -chdir=infra import 'github_repository_environment_deployment_policy.release_tags["pypi"]' 'repo-history:pypi:POLICY_ID'
terraform -chdir=infra import 'github_repository_environment_deployment_policy.release_tags["npm"]' 'repo-history:npm:POLICY_ID'
```

See the provider documentation for [environments](https://registry.terraform.io/providers/integrations/github/latest/docs/resources/repository_environment) and [deployment policies](https://registry.terraform.io/providers/integrations/github/latest/docs/resources/repository_environment_deployment_policy).

## Remaining registry setup

Terraform manages the GitHub side. It does not create the PyPI/npm trusted publishers, enable account 2FA, or publish a release. `terraform output registry_trusted_publisher_settings` prints the identities needed for registry setup.

| Setting | PyPI | npm |
| --- | --- | --- |
| Project/package | `repo-history` | `@dwhswenson/repo-history-viz` |
| GitHub owner | `dwhswenson` | `dwhswenson` |
| GitHub repository | `repo-history` | `repo-history` |
| Workflow filename | `publish-pypi.yaml` | `publish-npm.yaml` |
| Environment | `pypi` | `npm` |

### PyPI

Sign in to production PyPI with 2FA enabled. For an existing project you manage, add a GitHub publisher at [Manage → Publishing](https://pypi.org/manage/project/repo-history/settings/publishing/). Otherwise, register a pending publisher for `repo-history` on your [account Publishing page](https://pypi.org/manage/account/publishing/); the first successful upload creates the project. Resolve any project ownership conflict first.

PyPI's documented setup uses its website; its maintainer has [described the lack of management APIs](https://discuss.python.org/t/terraform-for-pypi-settings/103982/2). See [existing-project setup](https://docs.pypi.org/trusted-publishers/adding-a-publisher/) and [pending publishers](https://docs.pypi.org/trusted-publishers/creating-a-project-through-oidc/).

### npm

Sign in to npm with an account that has permission to manage the existing package. Add a GitHub Actions publisher in the [package settings](https://www.npmjs.com/package/@dwhswenson/repo-history-viz/access), using the table above and explicitly allowing direct `npm publish`.

Alternatively, with npm 11.15.0 or later, an authenticated npm session, package write access, and account 2FA enabled, run:

```sh
npm trust github @dwhswenson/repo-history-viz \
  --repository=dwhswenson/repo-history \
  --file=publish-npm.yaml \
  --environment=npm \
  --allow-publish \
  --registry=https://registry.npmjs.org/
```

This is a separate authenticated setup operation and can prompt for 2FA. It is deliberately outside Terraform's apply lifecycle. See [`npm trust`](https://docs.npmjs.com/cli/v11/commands/npm-trust/).

Complete the first successful npm trusted publish within two days of creating its publisher. If it expires, delete and recreate it. After the first successful trusted publish, set **Publishing access** to **Require two-factor authentication and disallow tokens** in the package settings. See [npm trusted publishing](https://docs.npmjs.com/trusted-publishers/).

## Release verification

Push the release workflows and intended package versions to `main`, confirm CI passes, and create and push a new release tag matching `v*`, such as `v0.1.1`. The tag push automatically starts both publishing workflows. Each derives its version from the tag and requires matching package metadata before building and publishing. These environments have no required reviewers, so there is no separate approval before publishing. Terraform apply does not start either workflow. A successful publish through each workflow is the end-to-end verification of its OIDC setup.

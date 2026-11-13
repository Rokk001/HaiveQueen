# Azure DevOps Services adapter

`azure` detects HTTPS `dev.azure.com/{organization}/{project}/_git/{repository}`,
legacy `{organization}.visualstudio.com/{project}/_git/{repository}`, and Azure
SSH v3 remotes. It returns an `(organization, project, repository)` tuple. It
does not contact Azure during discovery. Azure DevOps Server is not supported.

Each operation receives the tuple first, followed by the arguments below. Pass
`token` explicitly; `auth` is `pat` (default, HTTP Basic with an empty username)
or `bearer` (Microsoft Entra access token). `login_methods(repo)` lists these
methods; obtaining and storing credentials is the caller's responsibility. Grant
only the Azure permissions required by the operations used.

* `pull_request(repo, source, target, title, token, auth='pat', description='')`
  creates a pull request; branch names may include `refs/heads/`.
* `tag(repo, name, commit, token, auth='pat')` creates a lightweight tag at a
  40-digit commit ID and rejects an unsuccessful ref update.
* `release(repo, definition_id, token, auth='pat', description='', artifacts=None)`
  starts a classic deployment release. Artifacts are Azure artifact metadata.
* `release_list(repo, token, auth='pat', definition_id=None, continuation_token=None)`
  returns `(Azure JSON, continuation token)` for a page of classic deployment
  releases; pass the token to fetch the next page.
* `release_assets(repo, release_id, token, auth='pat')` returns the classic
  release's artifact metadata, not uploaded downloadable release files.
* `pipeline(repo, build_id, token, auth='pat')` returns the build record, including
  Azure's status and result fields.

These calls return Azure JSON and propagate HTTP and parsing failures. Classic
releases are project-wide, not GitHub-style repository releases. No file upload capability is advertised.

References: [pull requests](https://learn.microsoft.com/en-us/rest/api/azure/devops/git/pull-requests/create?view=azure-devops-rest-7.1),
[refs](https://learn.microsoft.com/en-us/rest/api/azure/devops/git/refs/update-refs?view=azure-devops-rest-7.1),
[classic releases](https://learn.microsoft.com/en-us/rest/api/azure/devops/release/releases?view=azure-devops-rest-7.1),
[builds](https://learn.microsoft.com/en-us/rest/api/azure/devops/build/builds/get?view=azure-devops-rest-7.1).

# Git provider module contract

Add a Python module in this directory. Export `CAPABILITIES`, a collection of
public operation names, and `detect(remote)`. Return `None` for an unsupported
Git remote, otherwise return the repository identifier passed as the first
argument to each operation. Export a callable for each advertised operation.
The caller supplies the remaining arguments. Advertise only implemented
operations; consumers should check capabilities before calling them.

`git_provider.discover` returns the matching provider and a list of errors.
A broken import, contract or detector is reported by module name and does not
prevent discovery of other modules. `Provider.call` reports operation failures
as `ProviderError`. Modules should validate their own inputs and avoid doing
network or authentication work during detection. Multiple matches are reported;
the first module in alphabetical order wins.

## GitHub provider

The `github` module detects GitHub HTTPS and SSH remotes. All operations take
`owner/name` first and are called through `Provider.call`. `pull_request` takes
`title, head, base`, optional `body` and `token`; `tag` takes `name, commit` and
optional `token`; `publish_release` takes `version, commit, token` and safely
retries an existing matching tag and release. `release_list` returns all pages;
`release_asset` takes `release_id, name, content` (bytes), `token`, and optional
`content_type`. `pipeline` takes a commit or ref and returns GitHub's combined
commit status. `login` finds credentials in GITHUB_TOKEN, GH_TOKEN, then
`gh auth token` without interactive login. `latest_version` and `check_latest`
support existing release selection. API failures propagate through ProviderError.

## Normalized integration operations

`Provider.open_pr(title, source, target, token=None)` returns a stable string
identifier. `Provider.pr_state(id, token=None)` returns `open`, `merged`, or
`closed`. `Provider.pipeline_state(commit, token=None)` returns `pending`,
`success`, or `failure`; missing evidence is pending. These operations never
merge a pull request or enable auto-merge. Provider errors are reported as
`ProviderError`. Bitbucket uses its existing `BITBUCKET_TOKEN` environment
credential; callers must provide tokens for providers requiring them.

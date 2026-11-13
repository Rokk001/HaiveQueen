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

## Normalized release operations

All operations take the repository first and an optional token last.
`release_tag(tag, commit, token)` creates a tag or verifies the existing tag's
commit. `release_state(tag, commit, token)` returns `pending`, `success`, or
`failure` for that tag's workflows only. Missing evidence remains pending.
GitHub, GitLab, Gitea/Forgejo and Azure implement these operations. Bitbucket
has no release resource and does not advertise release completion.

Local releases use `release_prepare(tag, commit, token)` to create or recover a
prerelease and return `{id, assets: [{name, ...}]}` with every asset page.
`release_upload(release, name, content, token)` uploads unchanged bytes and
returns an asset. `release_asset_digest(release, asset, token)` returns its
verified SHA-256 digest. A retry lists and verifies existing assets, including
an upload whose response was lost. Cleanup starts only after all digests match.
GitHub and Gitea mark prereleases explicitly. GitLab has no prerelease boolean:
its semantic prerelease tag and release name identify the prerelease; files
are stored in the generic package registry and attached as release links.
Azure classic release artifacts and Bitbucket repository downloads are not
local prerelease attachments, so local publication is skipped with a reason.

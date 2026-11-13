# GitLab provider

`gitlab` recognizes GitLab.com HTTPS, SSH and `git@` remotes, including
nested groups. Self-managed hosts are not detected automatically. Detection
performs no network or authentication work.

Operations are called through `Provider.call` and return decoded GitLab API
JSON. `pull_request(source_branch, target_branch, title, token, description='')`
creates a merge request; `tag(name, ref, token)` creates a tag;
`release(tag_name, name, description, token, ref=None)` creates a release;
`asset(tag_name, name, url, token)` attaches a release link (the file must
already be hosted). `release_list(token=None)` returns all pages, and
`pipeline(ref, token=None)` returns the newest pipeline object or None.
`login_methods()` returns `('personal_access_token',)`. Tokens are passed
explicitly as GitLab personal access tokens with appropriate API permissions;
public reads may omit them. HTTP and API errors propagate through the adapter
as `ProviderError`. No credentials are stored by this module.

API references: https://docs.gitlab.com/api/releases/,
https://docs.gitlab.com/api/releases/links/, https://docs.gitlab.com/api/tags/,
https://docs.gitlab.com/api/merge_requests/, https://docs.gitlab.com/api/pipelines/.

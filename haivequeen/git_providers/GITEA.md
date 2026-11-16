# Gitea and Forgejo provider

`gitea` implements the shared module contract for Gitea and Forgejo. Offline
detection recognizes `gitea.com` and `codeberg.org`. For self-hosted instances,
set `HAIVEQUEEN_GITEA_HOSTS` to a comma-separated list of hostnames before
discovery. HTTP(S), SSH URLs and SCP-style remotes are supported; SSH remotes
use HTTPS for the API. A custom SSH port is not an HTTP port; use an HTTPS
remote when the API uses a different port. The repository identifier is an
opaque `(origin, owner, repository)` tuple.

Operations accept the repository first and an optional `token` keyword, a
personal access token with the permissions required by the server. `login(repo,
token)` verifies a token via `/api/v1/user`; it does not store credentials.
Responses are the decoded API objects, and HTTP and JSON errors propagate through
`Provider.call` as provider errors. No provider login or network call occurs
during detection.

- `pull_request(repo, title, head, base, body='', token=None)` creates a pull request.
- `tag(repo, name, target, message='', token=None)` creates a tag.
- `release(repo, tag_name, name='', body='', target_commitish='', draft=False, prerelease=False, token=None)` creates a release.
- `asset_upload(repo, release_id, file_path, token=None)` uploads a release attachment.
- `release_list(repo, token=None)` returns all release pages.
- `pipeline(repo, ref, token=None)` returns the combined commit status, including its state and individual statuses. A missing status is not a successful pipeline. Actions availability and status reporting depend on server configuration.

API details: [Gitea API](https://docs.gitea.com/api/),
[Forgejo API](https://forgejo.org/docs/latest/user/api-usage/).

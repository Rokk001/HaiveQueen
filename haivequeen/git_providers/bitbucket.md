# Bitbucket Cloud provider

The adapter recognizes HTTPS, SSH and SCP-style `bitbucket.org` Git remotes.
It uses the Bitbucket Cloud 2.0 API and requires `BITBUCKET_TOKEN`, a Bearer
access token with appropriate repository, pull request and pipeline permissions.
Detection does not authenticate or access the network. `login(repo)` verifies
access to the repository and returns its API response.

Operations take the detected `workspace/repository` first and return the raw
API response: `pull_request(repo, title, source, destination, description='')`,
`tag(repo, name, commit, message=None)`, `pipeline(repo, pipeline_id)`,
`asset_upload(repo, file_path)`, and `asset_list(repo)`. The latter returns all
pages of repository downloads; uploads are repository downloads and are not
associated with a particular tag. Bitbucket Cloud has no release object API,
so release creation and release lists are not advertised. Tags and downloads
are available separately. Errors propagate to the adapter wrapper.

See [Bitbucket Cloud REST API](https://developer.atlassian.com/cloud/bitbucket/rest/api/).

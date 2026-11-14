"""GitHub REST provider. No network access occurs during detection."""
import json
import os
import re
from urllib.request import Request, urlopen
from urllib.parse import quote

from ..releases import NoRelease, next_version

CAPABILITIES = frozenset({"pull_request", "tag", "publish_release", "release_list", "release_asset", "pipeline", "login", "latest_version", "check_latest", "device_sign_in", "auth_status", "login_methods", "pr_status"})
LOGIN_OPTIONS = {'device': True, 'client_id_env': 'HAIVEQUEEN_GITHUB_CLIENT_ID', 'environment_first': True, 'token_env': ('GITHUB_TOKEN', 'GH_TOKEN'),
                 'registration_url': 'https://github.com/settings/applications/new',
                 'registration_help': 'Register an OAuth app and enable Device Flow. Enter its public client ID. An access token is also sufficient.'}

def login_methods(repository):
    return ("personal_access_token", "device")

def detect(remote):
    try:
        return repository_from_remote(remote)
    except (ValueError, TypeError):
        return None

def check_latest(repository: str, current: str, _urlopen=None):
    """Return the latest stable GitHub release if it is newer, otherwise None; NoRelease when GitHub has no release yet (HTTP 404)."""
    import json
    from urllib.error import HTTPError
    from urllib.request import Request

    if not re.fullmatch(r"[A-Za-z0-9_.-]+/[A-Za-z0-9_.-]+", repository) or any(part in (".", "..") for part in repository.split("/")):
        raise ValueError("repository must be an owner/name pair")
    next_version(current)  # Validate before making a network request.
    request = Request("https://api.github.com/repos/%s/releases/latest" % repository,
                      headers={"Accept": "application/vnd.github+json", "User-Agent": "HaiveQueen"})
    try:
        with (_urlopen or urlopen)(request, timeout=15) as response:
            release = json.load(response)
    except HTTPError as exc:
        if exc.code == 404:
            raise NoRelease("no release published yet") from exc
        raise
    version = str(release.get("tag_name", "")).removeprefix("v")
    try:
        if not version:
            return None
        next_version(current, version)
    except ValueError:
        return None
    return {"version": version, "url": str(release.get("html_url", ""))}


def latest_version(repository: str, _urlopen=None):
    """The version of the latest stable GitHub release of `repository`, or None when it has none (404) or its tag is no version."""
    import json
    from urllib.error import HTTPError
    from urllib.request import Request

    if not re.fullmatch(r"[A-Za-z0-9_.-]+/[A-Za-z0-9_.-]+", repository) or any(part in (".", "..") for part in repository.split("/")):
        raise ValueError("repository must be an owner/name pair")
    request = Request("https://api.github.com/repos/%s/releases/latest" % repository,
                      headers={"Accept": "application/vnd.github+json", "User-Agent": "HaiveQueen"})
    try:
        with (_urlopen or urlopen)(request, timeout=15) as response:
            release = json.load(response)
    except HTTPError as exc:
        if exc.code == 404:
            return None
        raise
    version = str(release.get("tag_name", "")).removeprefix("v")
    try:
        next_version(version)
    except ValueError:
        return None
    return version


def repository_from_remote(remote: str) -> str:
    """Extract an owner/name pair from a GitHub HTTPS or SSH remote."""
    match = re.fullmatch(
        r"(?:https://github\.com/|ssh://git@github\.com/|git@github\.com:)"
        r"([A-Za-z0-9_.-]+)/([A-Za-z0-9_.-]+?)(?:\.git)?/?", remote, re.IGNORECASE
    )
    if not match or any(part in (".", "..") for part in match.groups()):
        raise ValueError("remote must identify a GitHub repository")
    return "/".join(match.groups())


def publish_release(repository: str, version: str, commit: str, token: str, _urlopen=None):
    """Publish a release at an exact commit; safely retry an existing release.

    An existing tag must be lightweight and point at the requested commit.
    Network and GitHub errors propagate so callers can retry or report them.
    """
    import json
    from urllib.error import HTTPError
    from urllib.request import Request

    if not re.fullmatch(r"[A-Za-z0-9_.-]+/[A-Za-z0-9_.-]+", repository) or any(
        part in (".", "..") for part in repository.split("/")
    ):
        raise ValueError("repository must be an owner/name pair")
    prerelease = bool(re.fullmatch(r"v(?:0|[1-9]\d*)\.(?:0|[1-9]\d*)\.(?:0|[1-9]\d*)-[A-Za-z0-9]+(?:-[A-Za-z0-9]+)*\.\d+", version))
    if not prerelease:
        next_version(version)
    if not re.fullmatch(r"[0-9a-fA-F]{40}", commit):
        raise ValueError("commit must be a full Git object ID")
    if token is None:
        token = login(repository)
    if not token or "\n" in token or "\r" in token:
        raise ValueError("a GitHub token is required")
    base = "https://api.github.com/repos/" + repository
    tag = version if prerelease else "v" + version
    headers = {"Accept": "application/vnd.github+json", "User-Agent": "HaiveQueen",
               "Authorization": "Bearer " + token, "X-GitHub-Api-Version": "2022-11-28"}

    def request(path, data=None):
        payload = json.dumps(data).encode("utf-8") if data is not None else None
        req = Request(base + path, data=payload, headers=headers,
                      method="POST" if payload is not None else "GET")
        with (_urlopen or urlopen)(req, timeout=15) as response:
            return json.load(response)

    try:
        ref = request("/git/ref/tags/" + tag)
    except HTTPError as exc:
        if exc.code != 404:
            raise
        ref = None
    if ref is None:
        try:
            ref = request("/git/refs", {"ref": "refs/tags/" + tag, "sha": commit})
        except HTTPError as exc:
            if exc.code != 422:
                raise
            # Another publisher may have created the tag concurrently.
            ref = request("/git/ref/tags/" + tag)
    if ref is not None:
        obj = ref.get("object", {})
        if obj.get("type") != "commit" or str(obj.get("sha", "")).lower() != commit.lower():
            raise ValueError("release tag already exists at a different or unverifiable commit")
        try:
            existing = request("/releases/tags/" + tag)
            if existing.get("tag_name") != tag or existing.get("draft") or bool(existing.get("prerelease")) != prerelease:
                raise ValueError("existing release has the wrong publication status")
            return existing
        except HTTPError as exc:
            if exc.code != 404:
                raise
    return request("/releases", {"tag_name": tag, "target_commitish": commit,
                                  "name": tag, "draft": False, "prerelease": prerelease})


def login(repository, run=None):
    """Find an existing GitHub credential, without initiating an interactive login."""
    for key in ("GITHUB_TOKEN", "GH_TOKEN"):
        value = os.environ.get(key, "").strip()
        if value:
            return value
    from ..config import git_credential
    credential = git_credential("github")
    if credential:
        return credential["token"]
    if run is None:
        from ..publish import _run
        run = _run
    code, out = run(["gh", "auth", "token"], os.getcwd())
    value = out.strip() if code == 0 else ""
    return value if value and not any(c.isspace() for c in value) else ""

def _request(repository, path, token=None, data=None, method=None, content_type=None, raw=False):
    if not re.fullmatch(r"[A-Za-z0-9_.-]+/[A-Za-z0-9_.-]+", repository) or any(p in (".", "..") for p in repository.split("/")):
        raise ValueError("repository must be an owner/name pair")
    headers = {"Accept": "application/vnd.github+json", "User-Agent": "HaiveQueen", "X-GitHub-Api-Version": "2022-11-28"}
    if raw:
        headers["Accept"] = "application/octet-stream"
    if token is None:
        token = login(repository)
    if token:
        if "\n" in token or "\r" in token:
            raise ValueError("invalid token")
        headers["Authorization"] = "Bearer " + token
    if data is not None and not isinstance(data, bytes):
        data = json.dumps(data).encode("utf-8")
        headers["Content-Type"] = "application/json"
    if content_type:
        headers["Content-Type"] = content_type
    req = Request("https://api.github.com/repos/" + repository + path, data=data, headers=headers, method=method)
    with urlopen(req, timeout=15) as response:
        if getattr(response, 'status', None) == 204:
            return None
        return response.read() if raw else json.load(response)

def pull_request(repository, title, head, base, body="", token=None):
    return _request(repository, "/pulls", token, {"title": title, "head": head, "base": base, "body": body})

def tag(repository, name, commit, token=None):
    return _request(repository, "/git/refs", token, {"ref": "refs/tags/" + name, "sha": commit})

def release_list(repository, token=None):
    result = []
    page = 1
    while True:
        batch = _request(repository, "/releases?per_page=100&page=" + str(page), token)
        if not isinstance(batch, list):
            raise ValueError("invalid release list")
        result.extend(batch)
        if len(batch) < 100:
            return result
        page += 1

def release_asset(repository, release_id, name, content, token=None, content_type="application/octet-stream"):
    if not str(release_id).isdigit() or not name or not isinstance(content, bytes):
        raise ValueError("invalid release asset")
    if token is None:
        token = login(repository)
    if not token or "\n" in token or "\r" in token:
        raise ValueError("a GitHub token is required")
    if not re.fullmatch(r"[A-Za-z0-9_.-]+/[A-Za-z0-9_.-]+", repository) or any(p in (".", "..") for p in repository.split("/")):
        raise ValueError("repository must be an owner/name pair")
    req = Request("https://uploads.github.com/repos/%s/releases/%s/assets?name=%s" % (repository, release_id, quote(name, safe="")), data=content, headers={"Authorization": "Bearer " + token, "User-Agent": "HaiveQueen", "Accept": "application/vnd.github+json", "Content-Type": content_type}, method="POST")
    with urlopen(req, timeout=15) as response:
        return json.load(response)

def pipeline(repository, ref, token=None):
    return _request(repository, "/commits/" + quote(ref, safe="") + "/status", token)


def device_sign_in(repository=None, run=None):
    """Run GitHub CLI's interactive web/device authorization and save its token."""
    import subprocess
    if run is None:
        def run(args):
            return subprocess.run(args, check=False).returncode
    if run(['gh', 'auth', 'login', '--hostname', 'github.com', '--web']) != 0:
        raise ValueError('GitHub device sign-in failed')
    from ..publish import _run
    code, value = _run(['gh', 'auth', 'token', '--hostname', 'github.com'], os.getcwd())
    if code or not value.strip():
        raise ValueError('GitHub CLI did not return a token')
    from ..config import save_git_credential
    save_git_credential('github', value.strip(), 'device')
    return True


def auth_status(repository=None):
    """Report whether a credential is available, without exposing it."""
    return bool(login(repository))


def pr_status(repository, number, token=None):
    return _request(repository, "/pulls/" + quote(str(number), safe=""), token)


CAPABILITIES |= {'release_promotion_tag', 'release_tag', 'release_state', 'release_prepare', 'release_upload', 'release_asset_digest'}


def release_tag(repository, name, commit, token=None):
    from urllib.error import HTTPError
    try:
        ref = _request(repository, '/git/ref/tags/' + quote(name, safe=''), token)
    except HTTPError as exc:
        if exc.code != 404:
            raise
        tag(repository, name, commit, token)
        return
    obj = ref['object']
    while obj.get('type') == 'tag':
        obj = _request(repository, '/git/tags/' + obj['sha'], token)['object']
    if obj.get('type') != 'commit' or obj.get('sha') != commit:
        raise ValueError('release tag points at a different commit')



def release_promotion_tag(repository, name, commit, source, token=None):
    """Tag the selected source so the release workflow can reuse its assets."""
    from urllib.error import HTTPError
    message = 'HaiveQueen prerelease: ' + source
    try:
        ref = _request(repository, '/git/ref/tags/' + quote(name, safe=''), token)
    except HTTPError as exc:
        if exc.code != 404:
            raise
        obj = _request(repository, '/git/tags', token,
                       {'tag': name, 'message': message, 'object': commit, 'type': 'commit'})
        _request(repository, '/git/refs', token,
                 {'ref': 'refs/tags/' + name, 'sha': obj['sha']})
        return
    obj = ref['object']
    if obj.get('type') != 'tag':
        raise ValueError('existing release tag has no promotion source')
    annotated = _request(repository, '/git/tags/' + obj['sha'], token)
    target = annotated.get('object', {})
    if annotated.get('message') != message or target.get('type') != 'commit' or target.get('sha') != commit:
        raise ValueError('existing release tag has a different promotion source or commit')


def release_state(repository, name, commit, token=None):
    from ._release import workflows
    from urllib.parse import urlencode
    rows = []
    page = 1
    while True:
        batch = _request(repository, '/actions/runs?' + urlencode({
            'event': 'push', 'head_sha': commit, 'per_page': 100, 'page': page}), token)['workflow_runs']
        rows.extend(r for r in batch if r.get('head_sha') == commit and
                    r.get('head_branch') == name and r.get('event') == 'push')
        if len(batch) < 100:
            return workflows(rows)
        page += 1


def release_prepare(repository, name, commit, token=None):
    result = publish_release(repository, name[1:] if re.fullmatch(r'v\d+\.\d+\.\d+', name) else name, commit, token)
    return _release_assets(repository, result, token)


def _release_assets(repository, result, token, cleanup=True):
    # The release response embeds only a bounded asset list. List every page.
    assets = []
    page = 1
    while True:
        batch = _request(repository, '/releases/%s/assets?per_page=100&page=%s' % (result['id'], page), token)
        assets.extend(batch)
        if len(batch) < 100:
            break
        page += 1
    # List every page before deleting placeholders so pagination cannot shift.
    for asset in assets:
        if asset.get('state') == 'starter':
            if not cleanup:
                raise ValueError('pre-release asset upload is incomplete')
            _request(repository, '/releases/assets/' + quote(str(asset['id']), safe=''), token, method='DELETE')
    return dict(result, assets=[a for a in assets if a.get('state') != 'starter'])


def release_upload(repository, release, name, content, token=None):
    return release_asset(repository, release['id'], name, content, token)


def release_asset_digest(repository, release, asset, token=None):
    if asset.get('state') != 'uploaded':
        raise ValueError('release asset upload is incomplete')
    if not str(asset.get('digest', '')).startswith('sha256:'):
        import hashlib
        return hashlib.sha256(_request(repository, '/releases/assets/' + quote(str(asset['id']), safe=''),
                                       token, raw=True)).hexdigest()
    return asset['digest'][7:]


CAPABILITIES |= {'release_history', 'release_download', 'release_snapshot'}


def release_snapshot(repository, name, commit, token=None):
    if release_commit(repository, name, token) != commit:
        raise ValueError('pre-release tag points at a different commit')
    result = _request(repository, '/releases/tags/' + quote(name, safe=''), token)
    if result.get('tag_name') != name or result.get('draft') or not result.get('prerelease'):
        raise ValueError('selected release is no longer a pre-release')
    return _release_assets(repository, result, token, cleanup=False)


def release_commit(repository, name, token=None):
    obj = _request(repository, '/git/ref/tags/' + quote(name, safe=''), token)['object']
    seen = set()
    while obj.get('type') == 'tag':
        if obj['sha'] in seen:
            raise ValueError('cyclic annotated release tag')
        seen.add(obj['sha'])
        obj = _request(repository, '/git/tags/' + obj['sha'], token)['object']
    if obj.get('type') != 'commit':
        raise ValueError('release tag does not identify a commit')
    return obj['sha']


def release_history(repository, token=None):
    from ._release import metadata
    rows = []
    for row in release_list(repository, token):
        if row.get('draft'):
            continue
        rows.append(metadata(row['tag_name'], row.get('published_at') or row.get('created_at', ''),
                             release_commit(repository, row['tag_name'], token), row.get('prerelease', False)))
    return rows


def release_download(repository, release, asset, token=None):
    return _request(repository, '/releases/assets/' + quote(str(asset['id']), safe=''), token, raw=True)


# Normalized operations own provider-specific integration behavior.
from . import _integration as _normalized
CAPABILITIES |= {'open_pr', 'find_pr', 'pr_state', 'pr_commit', 'pipeline_state', 'credential'}

def pr_state(repo, identifier, token=None):
    return _normalized.state(pr_status(repo, identifier, token))

def pr_commit(repo, identifier, token=None):
    return _normalized.merge_commit(pr_status(repo, identifier, token))

def credential(repo):
    return login(repo)

def open_pr(repo, title, source, target, token=None):
    return pull_request(repo, title, source, target, token=token)["number"]

def find_pr(repo, source, target, token=None, commit=None):
    from urllib.parse import urlencode
    page = 1
    while True:
        rows = _request(repo, "/pulls?" + urlencode({"state": "all" if commit else "open", "page": page, "per_page": 100, "limit": 100}), token)
        for row in rows:
            if row.get("head", {}).get("ref") == source and row.get("base", {}).get("ref") == target:
                head_repo = (row.get("head", {}).get("repo") or {}).get("full_name")
                base_repo = (row.get("base", {}).get("repo") or {}).get("full_name")
                if base_repo and head_repo != base_repo:
                    continue
                if _normalized.reusable(row, row.get("head", {}).get("sha"), commit):
                    return row["number"]
        if len(rows) < 100:
            return None
        page += 1

def pipeline_state(repo, commit, token=None):
    result = pipeline(repo, commit, token)
    values = [str(result.get("state", result.get("status", ""))).lower()] if result and result.get("statuses") else []
    page = 1
    while True:
        checks = _request(repo, "/commits/%s/check-runs?per_page=100&filter=latest&page=%s" % (commit, page), token).get("check_runs", [])
        for check in checks:
            if check.get("head_sha") != commit or check.get("status") != "completed":
                values.append("pending")
            else:
                conclusion = check.get("conclusion")
                values.append("success" if conclusion in ("success", "neutral", "skipped") else "failure" if conclusion in ("failure", "cancelled", "timed_out", "action_required", "startup_failure", "stale") else "pending")
        if len(checks) < 100:
            break
        page += 1
    return _normalized.pipeline(values)

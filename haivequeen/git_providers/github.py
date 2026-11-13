"""GitHub REST provider. No network access occurs during detection."""
import json
import os
import re
from urllib.request import Request, urlopen
from urllib.parse import quote

from ..releases import NoRelease, next_version

CAPABILITIES = frozenset({"pull_request", "tag", "publish_release", "release_list", "release_asset", "pipeline", "login", "latest_version", "check_latest", "device_sign_in", "auth_status", "login_methods", "pr_status"})

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
    next_version(version)
    if not re.fullmatch(r"[0-9a-fA-F]{40}", commit):
        raise ValueError("commit must be a full Git object ID")
    if token is None:
        token = login(repository)
    if not token or "\n" in token or "\r" in token:
        raise ValueError("a GitHub token is required")
    base = "https://api.github.com/repos/" + repository
    tag = "v" + version
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
            if existing.get("tag_name") != tag or existing.get("draft") or existing.get("prerelease"):
                raise ValueError("existing release is not a published stable release")
            return existing
        except HTTPError as exc:
            if exc.code != 404:
                raise
    return request("/releases", {"tag_name": tag, "target_commitish": commit,
                                  "name": tag, "draft": False, "prerelease": False})


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

def _request(repository, path, token=None, data=None, method=None, content_type=None):
    if not re.fullmatch(r"[A-Za-z0-9_.-]+/[A-Za-z0-9_.-]+", repository) or any(p in (".", "..") for p in repository.split("/")):
        raise ValueError("repository must be an owner/name pair")
    headers = {"Accept": "application/vnd.github+json", "User-Agent": "HaiveQueen", "X-GitHub-Api-Version": "2022-11-28"}
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
        return json.load(response)

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

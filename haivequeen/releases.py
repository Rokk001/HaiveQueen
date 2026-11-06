"""Version selection for integration releases."""
import re
from urllib.request import urlopen


def next_version(current: str, requested: str = "") -> str:
    """Increment the patch or accept a strictly newer three-part version."""
    def parse(value):
        if not re.fullmatch(r"(0|[1-9]\d*)\.(0|[1-9]\d*)\.(0|[1-9]\d*)", value):
            raise ValueError("version must be three non-negative integers")
        return tuple(map(int, value.split(".")))

    old = parse(current)
    if requested:
        new = parse(requested)
        if new <= old:
            raise ValueError("next version must be newer than the current version")
    else:
        new = (old[0], old[1], old[2] + 1)
    return ".".join(map(str, new))


def check_latest(repository: str, current: str):
    """Return the latest stable GitHub release if it is newer, otherwise None."""
    import json
    from urllib.request import Request

    if not re.fullmatch(r"[A-Za-z0-9_.-]+/[A-Za-z0-9_.-]+", repository) or any(part in (".", "..") for part in repository.split("/")):
        raise ValueError("repository must be an owner/name pair")
    next_version(current)  # Validate before making a network request.
    request = Request("https://api.github.com/repos/%s/releases/latest" % repository,
                      headers={"Accept": "application/vnd.github+json", "User-Agent": "HaiveQueen"})
    with urlopen(request, timeout=15) as response:
        release = json.load(response)
    version = str(release.get("tag_name", "")).removeprefix("v")
    try:
        if not version:
            return None
        next_version(current, version)
    except ValueError:
        return None
    return {"version": version, "url": str(release.get("html_url", ""))}


def latest_version(repository: str):
    """The version of the latest stable GitHub release of `repository`, or None when it has none (404) or its tag is no version."""
    import json
    from urllib.error import HTTPError
    from urllib.request import Request

    if not re.fullmatch(r"[A-Za-z0-9_.-]+/[A-Za-z0-9_.-]+", repository) or any(part in (".", "..") for part in repository.split("/")):
        raise ValueError("repository must be an owner/name pair")
    request = Request("https://api.github.com/repos/%s/releases/latest" % repository,
                      headers={"Accept": "application/vnd.github+json", "User-Agent": "HaiveQueen"})
    try:
        with urlopen(request, timeout=15) as response:
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


def publish_release(repository: str, version: str, commit: str, token: str):
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
        with urlopen(req, timeout=15) as response:
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

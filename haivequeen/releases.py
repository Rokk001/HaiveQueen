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

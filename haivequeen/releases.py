"""Version selection for integration releases."""
import re
from urllib.request import urlopen


def prerelease_tag(tags, postfix, requested="", start=""):
    """Choose a prerelease tag from all repository tags, without project files."""
    if not re.fullmatch(r"[A-Za-z0-9]+(?:-[A-Za-z0-9]+)*", postfix):
        raise ValueError("invalid release postfix")
    full = []
    for tag in tags:
        if re.fullmatch(r"v(0|[1-9]\d*)\.(0|[1-9]\d*)\.(0|[1-9]\d*)", tag):
            full.append(tuple(map(int, tag[1:].split("."))))
    if full:
        current = ".".join(map(str, max(full)))
        try:
            version = next_version(current, requested) if requested else next_version(current)
        except ValueError:
            version = next_version(current)
    else:
        if not start:
            raise ValueError("start version required")
        next_version(start)  # validate without incrementing the owner's start version
        version = start
    # The sequence belongs to the version, even when the configured postfix changes.
    pattern = re.compile(r"v" + re.escape(version) + r"-[A-Za-z0-9]+(?:-[A-Za-z0-9]+)*\.(\d+)$")
    numbers = [int(match.group(1)) for tag in tags if (match := pattern.fullmatch(tag))]
    return "v%s-%s.%d" % (version, postfix, max(numbers, default=0) + 1)


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


class NoRelease(LookupError):
    """GitHub answered HTTP 404 for the latest release: the repository has no published release yet."""


def _github(operation, repository, *args):
    from .git_provider import Provider, ProviderError
    from .git_providers import github
    try:
        return Provider("github", github, repository).call(operation, *args, _urlopen=urlopen)
    except ProviderError as exc:
        if exc.__cause__ is not None:
            raise exc.__cause__
        raise


def check_latest(repository: str, current: str):
    return _github("check_latest", repository, current)


def latest_version(repository: str):
    return _github("latest_version", repository)


def repository_from_remote(remote: str) -> str:
    from .git_providers.github import repository_from_remote as parse
    return parse(remote)


def publish_release(repository: str, version: str, commit: str, token: str):
    return _github("publish_release", repository, version, commit, token)

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

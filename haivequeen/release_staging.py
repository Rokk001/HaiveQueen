"""Fetch and stage an immutable GitHub release alongside installed versions."""
import io
from pathlib import Path, PurePosixPath
import re
import stat
import tempfile
from urllib.request import Request, urlopen
import zipfile

from . import releases, version_layout


def stage_release(root, repository, version, fetch=None):
    """Download the source archive for a release tag and install it atomically.

    Reject paths and links that could escape the private staging directory. The
    active pointer is deliberately untouched; the caller decides when to switch.
    """
    if not re.fullmatch(r"[A-Za-z0-9_.-]+/[A-Za-z0-9_.-]+", repository) or any(
        part in (".", "..") for part in repository.split("/")
    ):
        raise ValueError("repository must be an owner/name pair")
    releases.next_version(str(version))
    url = "https://api.github.com/repos/%s/zipball/v%s" % (repository, version)
    if fetch is None:
        def fetch(address):
            request = Request(address, headers={"User-Agent": "HaiveQueen", "Accept": "application/vnd.github+json"})
            with urlopen(request, timeout=60) as response:
                return response.read()
    data = fetch(url)
    with zipfile.ZipFile(io.BytesIO(data)) as archive, tempfile.TemporaryDirectory(prefix="hq-release-") as temp:
        root_names = set()
        members = []
        for entry in archive.infolist():
            name = entry.filename
            path = PurePosixPath(name)
            if (not name or "\\" in name or ":" in name or path.is_absolute() or
                    any(part in (".", "..") for part in path.parts) or
                    stat.S_ISLNK(entry.external_attr >> 16)):
                raise ValueError("unsafe release archive")
            if not path.parts:
                raise ValueError("unsafe release archive")
            root_names.add(path.parts[0])
            members.append((entry, path))
        if len(root_names) != 1:
            raise ValueError("release archive must have one root directory")
        destination = Path(temp)
        for entry, path in members:
            target = destination.joinpath(*path.parts)
            if entry.is_dir():
                target.mkdir(parents=True, exist_ok=True)
            else:
                target.parent.mkdir(parents=True, exist_ok=True)
                with archive.open(entry) as source, target.open("wb") as output:
                    import shutil
                    shutil.copyfileobj(source, output)
        return version_layout.install_version(root, version, destination / next(iter(root_names)))

"""Fetch and stage an immutable GitHub release alongside installed versions."""
import io
import json
from pathlib import Path, PurePosixPath
import re
import stat
import tempfile
from urllib.request import Request, urlopen
import zipfile

from . import releases, version_layout, release_compat, release_tree


def resolve_tree(repository, version, fetch):
    """Resolve the release authority independently of any installed marker."""
    base = "https://api.github.com/repos/%s" % repository
    try:
        ref = json.loads(fetch(base + "/git/ref/tags/v" + str(version)))
        obj = ref["object"]
        commit = obj["sha"]
        if obj["type"] != "commit" or not re.fullmatch(r"[0-9a-f]{40}", commit):
            raise ValueError("unverifiable tag")
        commit_data = json.loads(fetch(base + "/git/commits/" + commit))
        if commit_data["sha"] != commit:
            raise ValueError("commit response mismatch")
        tree = commit_data["tree"]["sha"]
        if not re.fullmatch(r"[0-9a-f]{40}", tree):
            raise ValueError("invalid tree identity")
        tree_data = json.loads(fetch(base + "/git/trees/" + tree + "?recursive=1"))
        if tree_data["sha"] != tree or tree_data["truncated"] is not False or not isinstance(tree_data["tree"], list):
            raise ValueError("incomplete tree")
    except (OSError, ValueError, TypeError, KeyError, AttributeError) as exc:
        raise ValueError("cannot establish release tree") from exc
    return commit, tree, tree_data


def _fetch(address):
    request = Request(address, headers={"User-Agent": "HaiveQueen", "Accept": "application/vnd.github+json"})
    with urlopen(request, timeout=60) as response:
        return response.read()


def verify_installed(directory, repository, version, fetch=None):
    """Independently re-resolve authority and compare the installed projection.

    A true result is only an instant byte comparison; callers cannot infer
    live-process, dependency or shared-state compatibility from it.
    """
    fetch = fetch or _fetch
    try:
        if not re.fullmatch(r"[A-Za-z0-9_.-]+/[A-Za-z0-9_.-]+", repository) or any(p in (".", "..") for p in repository.split("/")):
            return False
        releases.next_version(str(version))
        commit, tree, tree_data = resolve_tree(repository, version, fetch)
        base = "https://api.github.com/repos/%s" % repository
        decision = None
        try:
            release = json.loads(fetch(base + "/releases/tags/v" + str(version)))
            candidate = json.loads(release["body"])
            if (release.get("tag_name") == "v" + str(version) and not release.get("draft")
                    and not release.get("prerelease") and releases.valid_decision(candidate, repository, str(version), commit)):
                decision = candidate
        except (OSError, ValueError, TypeError, KeyError, AttributeError):
            pass
        generated = {
            '.haivequeen-release-provenance.json': json.dumps({"schema": 1, "repository": repository, "commit": commit, "tree": tree, "installed_trust": "unknown"}, sort_keys=True).encode('utf-8'),
            release_compat.METADATA: json.dumps(decision or {"compatible_with_older": False, "reason": release_compat.UNKNOWN_REASON}).encode('utf-8'),
            '.haivequeen-release-version': (str(version) + "\n").encode('utf-8'),
        }
        return release_tree.verify_installation(directory, tree_data['tree'], tree, generated)
    except (OSError, ValueError, TypeError, KeyError, AttributeError):
        return False


def stage_release(root, repository, version, fetch=None, return_token=False):
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
    commit, tree, tree_data = resolve_tree(repository, version, fetch)
    base = "https://api.github.com/repos/%s" % repository
    decision = None
    try:
        release = json.loads(fetch(base + "/releases/tags/v" + str(version)))
        candidate = json.loads(release["body"])
        if (release.get("tag_name") == "v" + str(version) and not release.get("draft")
                and not release.get("prerelease") and releases.valid_decision(candidate, repository, str(version), commit)):
            decision = candidate
    except (OSError, ValueError, TypeError, KeyError, AttributeError):
        pass
    url = base + "/zipball/" + commit
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
        source = destination / next(iter(root_names))
        if not release_tree.verify_tree(source, tree_data["tree"], tree):
            raise ValueError("release archive does not match commit tree")
        generated = {'.haivequeen-release-provenance.json', release_compat.METADATA, '.haivequeen-release-version'}
        if any((source / name).exists() for name in generated):
            raise ValueError("release collides with generated markers")
        # This record is an audit trail, not an authority: installation copies
        # may omit files and both the record and installed files can be changed.
        (source / '.haivequeen-release-provenance.json').write_text(json.dumps({
            "schema": 1, "repository": repository, "commit": commit,
            "tree": tree, "installed_trust": "unknown"
        }, sort_keys=True), encoding="utf-8")
        # This identity belongs to the archive being installed, not the mutable
        # active pointer and not any marker supplied inside the archive.
        (source / release_compat.METADATA).write_text(json.dumps(decision or {
            "compatible_with_older": False, "reason": release_compat.UNKNOWN_REASON
        }), encoding="utf-8")
        (source / '.haivequeen-release-version').write_text(str(version) + '\n', encoding='utf-8', newline='\n')
        generated_bytes = {name: (source / name).read_bytes() for name in generated}
        def verify_copy(path):
            return release_tree.verify_installation(path, tree_data["tree"], tree, generated_bytes)
        return version_layout.install_version(root, version, source, verify=verify_copy, return_token=return_token)

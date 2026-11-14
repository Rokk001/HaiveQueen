"""Conservative lifetime records for versioned installations.

Callers must hold the same coordination lock across activation, process launch,
lease publication and cleanup. An unfinished launch is retained conservatively;
recovery must establish that no child survived before releasing its reservation.
"""
import json
import os
from pathlib import Path
import shutil
import tempfile
import uuid

from . import filetxn, platform
from .version_layout import _version, version_dir


def _load(root):
    path = Path(root) / 'version-leases.json'
    try:
        value = json.loads(path.read_text(encoding='utf-8'))
    except FileNotFoundError:
        return {}
    if not isinstance(value, dict):
        raise ValueError('invalid version lease registry')
    return value


def _save(root, value):
    root = Path(root)
    root.mkdir(parents=True, exist_ok=True)
    fd, name = tempfile.mkstemp(prefix='.leases-', dir=root)
    try:
        with os.fdopen(fd, 'w', encoding='utf-8') as f:
            json.dump(value, f)
            f.flush()
            os.fsync(f.fileno())
        os.replace(name, root / 'version-leases.json')
    finally:
        if os.path.exists(name):
            os.unlink(name)


def reserve(root, version):
    """Create a durable reservation before launching a process."""
    version = _version(version)
    with filetxn.locked():
        if not version_dir(root, version).is_dir():
            raise FileNotFoundError(version)
        leases = _load(root)
        token = uuid.uuid4().hex
        leases[token] = {'version': version, 'pid': None, 'identity': None}
        _save(root, leases)
        return token


def publish(root, token, pid):
    """Bind a reservation to a process; retain it when identity is unavailable."""
    with filetxn.locked():
        leases = _load(root)
        record = leases[token]
        record.update(pid=int(pid), identity=platform.process_identity(pid))
        _save(root, leases)


def release(root, token):
    """Release only after the caller has established the job cannot use the version."""
    with filetxn.locked():
        leases = _load(root)
        leases.pop(token, None)
        _save(root, leases)


def collect(root):
    """Remove unused versions, conservatively retaining unverifiable leases.

    Activation and every process start must use this lock and create a lease
    before this function can safely be enabled by the update workflow.
    """
    root = Path(root)
    with filetxn.locked():
        active = _version((root / 'active-version').read_text(encoding='utf-8').strip())
        leases = _load(root)
        for token, record in list(leases.items()):
            pid, identity = record.get('pid'), record.get('identity')
            if pid and identity:
                current = platform.process_identity(pid)
                if current != identity and (current is not None or not platform.process_alive(pid)):
                    del leases[token]
        _save(root, leases)
        protected = {active} | {_version(r['version']) for r in leases.values()}
        versions = root / 'versions'
        removed = []
        if versions.is_dir():
            for path in versions.iterdir():
                if path.is_dir() and not path.is_symlink() and path.name not in protected:
                    try:
                        _version(path.name)
                    except ValueError:
                        continue
                    shutil.rmtree(path)
                    removed.append(path.name)
        return removed

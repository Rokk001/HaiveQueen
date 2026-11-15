"""Stable launch point and isolated version directories for release updates.

The launcher is installed once. Updates only add a version directory and atomically
replace the small active-version pointer; they never overwrite the launcher.
"""
import os
from pathlib import Path
import re
import shlex
import shutil
import sys
import tempfile
import uuid

_VERSION = re.compile(r"[A-Za-z0-9][A-Za-z0-9._-]*\Z")


def _version(value):
    if not _VERSION.fullmatch(str(value)) or value in (".", ".."):
        raise ValueError("invalid version")
    return str(value)


def version_dir(root, version):
    return Path(root) / "versions" / _version(version)


def install_version(root, version, source, verify=None, return_token=False):
    """Copy a complete release into its own directory, never replace a release."""
    from . import version_leases
    from .stable_launcher import lifetime_lock
    root, source = Path(root), Path(source)
    version = _version(version)
    target = version_dir(root, version)
    if not (source / "haivequeen" / "cli.py").is_file():
        raise ValueError("release does not contain haivequeen/cli.py")
    target.parent.mkdir(parents=True, exist_ok=True)
    # Protect the target before it becomes visible to the collector. If this
    # process dies, the reservation deliberately survives until recovery.
    with lifetime_lock(root):
        leases = version_leases._load(root)
        token = 'install-' + uuid.uuid4().hex
        leases[token] = {'version': version, 'pid': None, 'identity': None, 'installation': True,
                         'installer_pid': os.getpid(),
                         'installer_identity': version_leases.platform.process_identity(os.getpid())}
        version_leases._save(root, leases)
    staging = None
    try:
        staging = Path(tempfile.mkdtemp(prefix=".install-", dir=target.parent))
        shutil.copytree(source, staging, dirs_exist_ok=True, ignore=shutil.ignore_patterns('.git', '__pycache__', '*.pyc'))
        if verify is not None and not verify(staging):
            raise ValueError("installed release does not match authority")
        # A version is immutable. Rename only after the complete copy succeeds.
        if target.exists():
            raise FileExistsError(str(target))
        with lifetime_lock(root):
            staging.rename(target)
    except BaseException:
        version_leases.release(root, token)
        raise
    finally:
        if staging is not None and staging.exists():
            shutil.rmtree(staging)
    return (target, token) if return_token else target


def set_active(root, version, installation_token=None):
    """Atomically change the version selected by the stable launcher."""
    from .stable_launcher import lifetime_lock
    with lifetime_lock(root):
        _set_active_locked(root, version, installation_token=installation_token)


def _set_active_locked(root, version, installation_token=None):
    """Publish the active pointer while holding the lifetime coordination lock."""
    root, version = Path(root), _version(version)
    if not (version_dir(root, version) / "haivequeen" / "cli.py").is_file():
        raise FileNotFoundError("version is not installed: " + version)
    from . import version_leases
    leases = version_leases._load(root)
    if installation_token is not None:
        record = leases.get(installation_token, {})
        if (not record.get('installation') or record.get('version') != version
                or record.get('installer_pid') != os.getpid()
                or not record.get('installer_identity')
                or record.get('installer_identity') != version_leases.platform.process_identity(os.getpid())):
            raise ValueError("installation reservation does not belong to this installer and version")
    root.mkdir(parents=True, exist_ok=True)
    fd, name = tempfile.mkstemp(prefix=".active-", dir=root)
    try:
        with os.fdopen(fd, "w", encoding="utf-8", newline="\n") as f:
            f.write(version + "\n")
            f.flush()
            os.fsync(f.fileno())
        os.replace(name, root / "active-version")
        # Only the installer that owns a reservation may consume it.
        if installation_token is not None:
            leases.pop(installation_token)
            version_leases._save(root, leases)
    finally:
        if os.path.exists(name):
            os.unlink(name)


def install_launcher(root, python=None):
    """Create the fixed bzz and start entry points once, without replacing them."""
    root = Path(root)
    root.mkdir(parents=True, exist_ok=True)
    launcher = root / "launcher.py"
    files = {launcher: Path(__file__).with_name("stable_launcher.py").read_text(encoding="utf-8")}
    executable = str(python or sys.executable)
    if os.name == "nt":
        if any(c in executable for c in '\r\n%"'):
            raise ValueError("unsupported interpreter path")
        files[root / "bzz.cmd"] = '@echo off\r\n"' + executable + '" -I "%~dp0launcher.py" %*\r\n'
        files[root / "start.cmd"] = '@echo off\r\n"' + executable + '" -I "%~dp0launcher.py" start %*\r\n'
    else:
        command = shlex.quote(executable) + ' -I "$(dirname "$0")/launcher.py"'
        files[root / "bzz"] = '#!/bin/sh\nexec ' + command + ' "$@"\n'
        files[root / "start"] = '#!/bin/sh\nexec ' + command + ' start "$@"\n'
    for path, content in files.items():
        try:
            with path.open("x", encoding="utf-8", newline="") as f:
                f.write(content)
        except FileExistsError:
            continue
        if os.name != "nt":
            path.chmod(0o755)
    return root

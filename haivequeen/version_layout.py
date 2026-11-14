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

_VERSION = re.compile(r"[A-Za-z0-9][A-Za-z0-9._-]*\Z")


def _version(value):
    if not _VERSION.fullmatch(str(value)) or value in (".", ".."):
        raise ValueError("invalid version")
    return str(value)


def version_dir(root, version):
    return Path(root) / "versions" / _version(version)


def install_version(root, version, source):
    """Copy a complete release into its own directory, never replace a release."""
    root, source = Path(root), Path(source)
    target = version_dir(root, version)
    if not (source / "haivequeen" / "cli.py").is_file():
        raise ValueError("release does not contain haivequeen/cli.py")
    target.parent.mkdir(parents=True, exist_ok=True)
    staging = Path(tempfile.mkdtemp(prefix=".install-", dir=target.parent))
    try:
        shutil.copytree(source, staging, dirs_exist_ok=True, ignore=shutil.ignore_patterns('.git', '__pycache__', '*.pyc'))
        # A version is immutable. Rename only after the complete copy succeeds.
        if target.exists():
            raise FileExistsError(str(target))
        staging.rename(target)
    finally:
        if staging.exists():
            shutil.rmtree(staging)
    return target


def set_active(root, version):
    """Atomically change the version selected by the stable launcher."""
    from . import filetxn
    with filetxn.locked():
        _set_active_locked(root, version)


def _set_active_locked(root, version):
    """Publish the active pointer while holding the lifetime coordination lock."""
    root, version = Path(root), _version(version)
    if not (version_dir(root, version) / "haivequeen" / "cli.py").is_file():
        raise FileNotFoundError("version is not installed: " + version)
    root.mkdir(parents=True, exist_ok=True)
    fd, name = tempfile.mkstemp(prefix=".active-", dir=root)
    try:
        with os.fdopen(fd, "w", encoding="utf-8", newline="\n") as f:
            f.write(version + "\n")
            f.flush()
            os.fsync(f.fileno())
        os.replace(name, root / "active-version")
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

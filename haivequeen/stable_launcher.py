"""Fixed launcher: this file is copied once and is independent of any version."""
import os
import errno
import json
import tempfile
import time
import uuid
from contextlib import contextmanager
from pathlib import Path
import re
import sys
import subprocess


@contextmanager
def lifetime_lock(root):
    """Serialize version selection, publication, activation and collection."""
    root = Path(root)
    root.mkdir(parents=True, exist_ok=True)
    with open(root / '.version-lifetime.lock', 'a+b') as handle:
        handle.seek(0, os.SEEK_END)
        if handle.tell() == 0:
            handle.write(b'0')
            handle.flush()
        handle.seek(0)
        if os.name == 'nt':
            import msvcrt
            while True:
                try:
                    msvcrt.locking(handle.fileno(), msvcrt.LK_NBLCK, 1)
                    break
                except OSError as exc:
                    if exc.errno not in (errno.EACCES, errno.EAGAIN, errno.EDEADLK):
                        raise
                    time.sleep(.02)
        else:
            import fcntl
            fcntl.flock(handle, fcntl.LOCK_EX)
        try:
            yield
        finally:
            handle.seek(0)
            if os.name == 'nt':
                msvcrt.locking(handle.fileno(), msvcrt.LK_UNLCK, 1)
            else:
                fcntl.flock(handle, fcntl.LOCK_UN)


def _load(root):
    try:
        value = json.loads((root / 'version-leases.json').read_text(encoding='utf-8'))
    except FileNotFoundError:
        return {}
    if not isinstance(value, dict):
        raise ValueError('invalid version lease registry')
    return value


def _save(root, value):
    fd, name = tempfile.mkstemp(prefix='.leases-', dir=root)
    try:
        with os.fdopen(fd, 'w', encoding='utf-8') as handle:
            json.dump(value, handle)
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(name, root / 'version-leases.json')
    finally:
        if os.path.exists(name):
            os.unlink(name)

def main():
    root = Path(__file__).resolve().parent
    # A pending lease survives a launcher crash, including a crash between
    # starting the child and publishing its identity.
    with lifetime_lock(root):
        version = (root / "active-version").read_text(encoding="utf-8").strip()
        if not re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9._-]*", version) or version in (".", ".."):
            raise ValueError("invalid active version")
        cli = root / "versions" / version / "haivequeen" / "cli.py"
        if not cli.is_file():
            raise FileNotFoundError("active version is not installed")
        token = uuid.uuid4().hex
        leases = _load(root)
        leases[token] = {'version': version, 'pid': None, 'identity': None}
        _save(root, leases)
    try:
        process = subprocess.Popen([sys.executable, "-I", str(cli), *sys.argv[1:]])
    except BaseException:
        with lifetime_lock(root):
            leases = _load(root)
            leases.pop(token, None)
            _save(root, leases)
        raise
    # The pending record protects the child even if waiting or publication fails.
    result = process.wait()
    with lifetime_lock(root):
        leases = _load(root)
        leases.pop(token, None)
        _save(root, leases)
    return result


if __name__ == "__main__":
    sys.exit(main())

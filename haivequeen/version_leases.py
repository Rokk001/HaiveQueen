"""Conservative lifetime records for versioned installations.

Callers must hold the same coordination lock across activation, process launch,
lease publication and cleanup. An unfinished launch is retained conservatively;
recovery must establish that no child survived before releasing its reservation.
"""
import json
import os
from contextlib import contextmanager
from pathlib import Path
import shutil
import tempfile
import uuid

from . import platform, process_scan
from .stable_launcher import lifetime_lock
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


@contextmanager
def exclusive_registry_snapshot(root):
    """Read an existing Windows registry while holding its exclusive handle.

    This primitive does not establish that activation or registry replacement
    is safe while the handle is held.
    """
    if os.name != 'nt':
        raise OSError('exclusive registry snapshots require Windows')
    import ctypes
    import msvcrt
    from ctypes import wintypes

    kernel = ctypes.WinDLL('kernel32', use_last_error=True)
    create = kernel.CreateFileW
    create.argtypes = [wintypes.LPCWSTR, wintypes.DWORD, wintypes.DWORD,
                       wintypes.LPVOID, wintypes.DWORD, wintypes.DWORD,
                       wintypes.HANDLE]
    create.restype = wintypes.HANDLE
    close = kernel.CloseHandle
    close.argtypes = [wintypes.HANDLE]
    close.restype = wintypes.BOOL
    handle = create(str(Path(root) / 'version-leases.json'), 0x80000000,
                    0, None, 3, 0x80, None)  # GENERIC_READ, OPEN_EXISTING
    if handle == ctypes.c_void_p(-1).value:
        raise ctypes.WinError(ctypes.get_last_error())
    try:
        fd = msvcrt.open_osfhandle(handle, os.O_RDONLY | os.O_BINARY)
    except BaseException:
        close(handle)
        raise
    try:
        stream = os.fdopen(fd, 'r', encoding='utf-8')
    except BaseException:
        os.close(fd)
        raise
    with stream:
        value = json.load(stream)
        if not isinstance(value, dict):
            raise ValueError('invalid version lease registry')
        yield value


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
    with lifetime_lock(root):
        return _reserve_locked(root, version)


def _reserve_locked(root, version):
    version = _version(version)
    if not version_dir(root, version).is_dir():
        raise FileNotFoundError(version)
    leases = _load(root)
    token = uuid.uuid4().hex
    leases[token] = {'version': version, 'pid': None, 'identity': None}
    _save(root, leases)
    return token


def publish(root, token, pid):
    """Bind a reservation to a process; retain it when identity is unavailable."""
    with lifetime_lock(root):
        leases = _load(root)
        record = leases[token]
        record.update(pid=int(pid), identity=platform.process_identity(pid))
        _save(root, leases)


def release(root, token):
    """Release only after the caller has established the job cannot use the version."""
    with lifetime_lock(root):
        leases = _load(root)
        leases.pop(token, None)
        _save(root, leases)


def live_snapshot(root):
    """Snapshot possible users under the lifetime lock without changing leases.

    ``complete`` describes enumeration only, not artifact provenance or shared
    state compatibility. A positive compatibility decision needs both as well.
    """
    root = Path(root)
    with lifetime_lock(root):
        active = _version((root / 'active-version').read_text(encoding='utf-8').strip())
        leases = _load(root)
        versions = {active}
        uncertain, unresolved = set(), set()
        directory = root / 'versions'
        try:
            paths = list(directory.iterdir())
        except OSError:
            return {'versions': sorted(versions), 'unresolved': sorted(leases),
                    'uncertain': ['versions directory'], 'complete': False}
        scans = {}
        for path in paths:
            try:
                version = _version(path.name)
                if not path.is_dir() or path.is_symlink():
                    uncertain.add(path.name)
                    continue
                result = process_scan.scan(path)
            except (OSError, ValueError):
                uncertain.add(path.name)
                continue
            scans[version] = result
            if result is not False:
                versions.add(version)
            if result is None:
                uncertain.add(version)
        if active not in scans:
            uncertain.add(active)
        for token, record in leases.items():
            try:
                version = _version(record['version'])
                if version not in scans:
                    uncertain.add(version)
                pid, identity = record.get('pid'), record.get('identity')
                if record.get('installation'):
                    # An installation reservation does not identify a user.
                    unresolved.add(token)
                elif pid and identity:
                    current = platform.process_identity(pid)
                    if current == identity:
                        versions.add(version)
                    elif current is None and not platform.process_absent(pid):
                        unresolved.add(token)
                elif (pid and not platform.process_absent(pid)) or scans.get(version) is not False:
                    unresolved.add(token)
                if token in unresolved:
                    versions.add(version)
            except (KeyError, TypeError, ValueError, OSError):
                unresolved.add(token)
        return {'versions': sorted(versions), 'unresolved': sorted(unresolved),
                'uncertain': sorted(uncertain), 'complete': not (unresolved or uncertain)}


def collect(root):
    """Remove unused versions, conservatively retaining unverifiable leases.

    Activation and every process start must use this lock and create a lease
    before this function can safely be enabled by the update workflow.
    """
    root = Path(root)
    with lifetime_lock(root):
        active = _version((root / 'active-version').read_text(encoding='utf-8').strip())
        leases = _load(root)
        scans = {}

        def scan_version(version):
            if version not in scans:
                scans[version] = process_scan.scan(version_dir(root, version))
            return scans[version]

        for token, record in list(leases.items()):
            # A failed copy cannot leave a child using the target.  Keep a
            # completed installation reserved for later activation, however.
            if record.get('installation') and not version_dir(root, record['version']).exists():
                pid, identity = record.get('installer_pid'), record.get('installer_identity')
                if pid and identity:
                    current = platform.process_identity(pid)
                    if current != identity and (current is not None or platform.process_absent(pid)):
                        del leases[token]
                elif pid and platform.process_absent(pid):
                    del leases[token]
                continue
            pid, identity = record.get('pid'), record.get('identity')
            if pid and identity:
                current = platform.process_identity(pid)
                if current != identity and (current is not None or platform.process_absent(pid)):
                    del leases[token]
            elif (not record.get('installation') and (not pid or platform.process_absent(pid))
                  and scan_version(record['version']) is False):
                # A missing birth identity requires a clear scan even after
                # PID exit. The launch lock excludes an in-flight spawn.
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
                    if scan_version(path.name) is not False:
                        continue
                    try:
                        shutil.rmtree(path)
                    except OSError:
                        # A locked or inaccessible file is not proof that the
                        # version can be removed. Retry on a later collection.
                        continue
                    removed.append(path.name)
        return removed


def reserve_for_path(path):
    """Reserve the immutable version containing path, or None for a source checkout.

    The reservation is intentionally kept if publication is interrupted: safety
    takes priority over collecting a directory whose process might have started.
    """
    path = Path(path).resolve()
    if path.parent.name != 'versions' or not (path.parent.parent / 'active-version').is_file():
        return None
    return path.parent.parent, reserve(path.parent.parent, path.name)


def launch(path, popen, *args, **kwargs):
    """Reserve before spawning and bind the record to the resulting process."""
    path = Path(path).resolve()
    if path.parent.name != 'versions' or not (path.parent.parent / 'active-version').is_file():
        return popen(*args, **kwargs)
    root = path.parent.parent
    # Keep the lock through spawn and publication. Otherwise a collector could
    # scan an empty process table after reservation but before process creation.
    with lifetime_lock(root):
        token = _reserve_locked(root, path.name)
        process = popen(*args, **kwargs)
        try:
            leases = _load(root)
            leases[token].update(pid=int(process.pid), identity=platform.process_identity(process.pid))
            _save(root, leases)
        except Exception:
            import logging
            logging.exception("Could not publish version lease; reservation retained")
        return process

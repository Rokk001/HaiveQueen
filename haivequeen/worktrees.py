"""Safe cleanup of worktrees whose commits are integrated."""
import re
import subprocess

from . import db, runtime


# Run Git without shell expansion and capture errors for the caller.
def _git(repo, *args):
    """Run Git in the given repository and return its result."""
    return subprocess.run(['git', '-C', str(repo), *args], capture_output=True,
                          text=True, encoding='utf-8', errors='replace', timeout=60)


# Read Git's authoritative worktree inventory.
def list_worktrees(repo):
    """Return Git's worktree records, including detached and locked entries."""
    result = _git(repo, 'worktree', 'list', '--porcelain', '-z')
    if result.returncode:
        raise RuntimeError(result.stderr.strip())
    records, record = [], {}
    for field in result.stdout.split('\0'):
        if not field:
            if record:
                records.append(record)
                record = {}
            continue
        key, _, value = field.partition(' ')
        if key == 'branch':
            value = re.sub(r'^refs/heads/', '', value)
        record[key] = value if value else True
    if record:
        records.append(record)
    return records

# Find ignored files that the project has not explicitly classified as build output.
def _ignored_blocker(path, allowed):
    """Return the first protected ignored path, or an empty string when none exists."""
    result = _git(path, 'ls-files', '--others', '--ignored', '--exclude-standard', '-z')
    if result.returncode:
        raise RuntimeError(result.stderr.strip())
    import fnmatch
    for item in filter(None, result.stdout.split('\0')):
        if not any((pattern.replace('\\', '/').endswith('/') and
                    ('/' + pattern.replace('\\', '/').lstrip('/') in '/' + item + '/')) or
                   fnmatch.fnmatchcase(item, pattern.replace('\\', '/')) for pattern in allowed):
            return item
    return ''

# Compare paths while respecting the platform's path and case rules.
def _within(path, parent):
    """Return whether path is the same folder as parent or lies below it."""
    import os
    try:
        child = os.path.normcase(os.path.realpath(path))
        root = os.path.normcase(os.path.realpath(parent))
        return os.path.commonpath([child, root]) == root
    except (OSError, ValueError):
        return False


# Apply repository, queue, and active-agent protections to a worktree.
def _candidate_reason(env, record, first, queue, integration_ref=None):
    """Return a reason to keep a worktree, or an empty string if these checks pass."""
    from pathlib import Path
    path = record['worktree']
    protected = [env.code_repo, env.workspace]
    for item in env.settings.get('protectedWorktrees', []):
        item = Path(item)
        protected.extend([item] if item.is_absolute() else [env.code_repo / item, env.workspace / item])
    if first or any(_within(path, item) or _within(item, path) for item in protected):
        return 'protected worktree'
    if record.get('locked') or record.get('prunable'):
        return 'locked or missing'
    if any(row.get('branch') == record.get('branch') and row.get('status') in ('ready', 'in_run', 'blocked')
           for row in queue if record.get('branch')):
        return 'branch has an open queue row'
    agents = runtime.list_agents(env.conn, None, 'active')
    for agent in agents:
        if agent.get('status') in ('running', 'starting') and agent.get('cwd') and _within(agent['cwd'], path):
            return 'used by active agent'
    queens = [agent for agent in agents if agent.get('phase') == 'queen' and agent.get('status') in ('running', 'starting')]
    if queens:
        tables = {row[0] for row in env.conn.execute("SELECT name FROM sqlite_master WHERE type='table' AND name IN ('bee_assignments', 'bee_worktrees')")}
        if tables == {'bee_assignments', 'bee_worktrees'}:
            for agent in queens:
                rows = env.conn.execute('SELECT w.cwd FROM bee_worktrees w JOIN bee_assignments a ON '
                                        'a.project=w.project AND a.plan_path=w.plan_path AND a.package=w.package '
                                        'WHERE w.project=? AND w.plan_path=?', (agent.get('project'), agent.get('plan_path')))
                if any(_within(row[0], path) for row in rows):
                    return 'used by assigned Bee'
    status = _git(path, 'status', '--porcelain', '--untracked-files=all')
    if status.returncode:
        return 'git status failed'
    if status.stdout:
        return 'uncommitted or untracked changes'
    ignored = _ignored_blocker(path, env.settings.get('ignoredBuildOutput', []))
    if ignored:
        return 'ignored file: ' + ignored
    ref = integration_ref or 'origin/' + str(env.settings.get('integrationBranch') or 'main')
    ancestor = _git(env.code_repo, 'merge-base', '--is-ancestor', record['HEAD'], ref)
    if ancestor.returncode:
        return 'HEAD is not in the integration branch'
    return ''

# Identify links and reject other Windows reparse-point types.
def _link_kind(path):
    """Return the link type, or an empty string for a normal file or directory."""
    import os
    import stat
    info = os.lstat(path)
    if stat.S_ISLNK(info.st_mode):
        return 'symlink'
    if getattr(info, 'st_file_attributes', 0) & 0x400:
        if getattr(info, 'st_reparse_tag', 0) == 0xA0000003:
            return 'junction'
        raise OSError('unsupported reparse point: ' + str(path))
    return ''


# Capture the number and total size of the files under a link target.
def _target_size(target):
    """Count regular files and bytes without following further links."""
    import os
    from pathlib import Path
    target = Path(target)
    if target.is_file():
        return (1, target.stat().st_size)
    if not target.is_dir():
        raise OSError('link target is missing')
    count = size = 0
    def error(exc):
        """Propagate traversal failures so the worktree is kept."""
        raise exc
    for root, dirs, files in os.walk(target, followlinks=False, onerror=error):
        for name in dirs + files:
            item = Path(root) / name
            if _link_kind(item):
                raise OSError('nested link in target')
        for name in files:
            item = Path(root) / name
            if not item.is_file():
                raise OSError('non-regular target file')
            count += 1
            size += item.stat().st_size
    return count, size


# Find links without traversing them and record their external targets.
def _link_targets(path):
    """Return target paths and their file counts and sizes, or raise when unsafe."""
    import os
    from pathlib import Path
    targets = []
    def error(exc):
        """Propagate traversal failures so the worktree is kept."""
        raise exc
    for root, dirs, files in os.walk(path, followlinks=False, onerror=error):
        for name in dirs + files:
            item = Path(root) / name
            if not _link_kind(item):
                continue
            target = item.resolve(strict=True)
            if _within(target, path):
                raise OSError('link target is inside the worktree')
            targets.append((item, target, _target_size(target)))
        dirs[:] = [name for name in dirs if not _link_kind(Path(root, name))]
    return targets

# Run the requested cleanup for one or all registered projects.
def cmd_worktrees(args, conn):
    """Print the cleanup lists for the selected projects."""
    import json
    from . import config, scheduler
    envs = scheduler.load_projects(conn, config.load_config())
    selected = [env for env in envs if not args.project or env.name == args.project]
    if args.project and not selected:
        print(json.dumps({'ok': False, 'error': 'unknown Apiary'}))
        return 1
    print(json.dumps({env.name: prune(env, dry=args.dry_run) for env in selected}, ensure_ascii=False))
    return 0


# Add the worktree command to the main argument parser.
def register(sub):
    """Register the worktrees prune command and its options."""
    parser = sub.add_parser('worktrees')
    parser.add_argument('action', choices=['prune'])
    parser.add_argument('--project')
    parser.add_argument('--dry-run', action='store_true')
    parser.set_defaults(fn=cmd_worktrees)

# Evaluate and remove candidates only after every safety check succeeds.
def _prune_candidates(env, records, queue, process_busy, dry, integration_ref=None):
    """Return removed, kept, and failed worktrees after checking each candidate."""
    import os
    result = {'removed': [], 'kept': [], 'notRemovable': [], 'dryRun': bool(dry)}
    for index, record in enumerate(records):
        path = os.path.normpath(record['worktree'])
        try:
            reason = _candidate_reason(env, record, index == 0, queue, integration_ref)
            if not reason and process_busy(path) is not False:
                reason = 'process working directory or unknown process state'
            targets = _link_targets(path) if not reason else []
            if reason:
                result['kept'].append({'path': path, 'reason': reason})
                continue
            if dry:
                result['removed'].append(path)
                continue
            reason = _candidate_reason(env, record, index == 0, queue, integration_ref)
            if reason or process_busy(path) is not False:
                result['kept'].append({'path': path, 'reason': reason or 'process working directory or unknown process state'})
                continue
            removed = _remove_with_links(env.code_repo, path, targets)
            if removed.returncode:
                result['notRemovable'].append({'path': path, 'reason': removed.stderr.strip()})
                continue
            result['removed'].append(path)
            for _, target, before in targets:
                if _target_size(target) != before:
                    result['notRemovable'].append({'path': path, 'reason': 'link target changed: ' + str(target)})
        except (OSError, RuntimeError, subprocess.SubprocessError) as exc:
            result['notRemovable'].append({'path': path, 'reason': str(exc)})
    return result

# Refresh the integration ref before checking any candidate.
def _prune_with_checker(env, process_busy, dry):
    """Fetch the integration branch and evaluate its worktrees safely."""
    import os
    branch = str(env.settings.get('integrationBranch') or 'main')
    fetched = _git(env.code_repo, 'fetch', 'origin', branch)
    records = list_worktrees(env.code_repo)
    if fetched.returncode:
        return {'removed': [], 'kept': [{'path': os.path.normpath(record['worktree']),
                'reason': 'fetch failed: ' + fetched.stderr.strip()} for record in records],
                'notRemovable': [], 'dryRun': bool(dry)}
    tip = _git(env.code_repo, 'rev-parse', '--verify', 'FETCH_HEAD^{commit}')
    if tip.returncode:
        return {'removed': [], 'kept': [{'path': os.path.normpath(record['worktree']),
                'reason': 'cannot resolve fetched integration branch'} for record in records],
                'notRemovable': [], 'dryRun': bool(dry)}
    remote = _git(env.code_repo, 'ls-remote', '--refs', 'origin', 'refs/heads/' + branch)
    if remote.returncode or remote.stdout.strip().split('\t', 1)[0] != tip.stdout.strip():
        return {'removed': [], 'kept': [{'path': os.path.normpath(record['worktree']),
                'reason': 'integration branch changed during fetch'} for record in records],
                'notRemovable': [], 'dryRun': bool(dry)}
    return _prune_candidates(env, records, db.queue_rows(env.conn, env.name), process_busy, dry, tip.stdout.strip())

# Move untracked links aside before Git removal and restore them if removal fails.
def _remove_with_links(repo, path, targets):
    """Remove a worktree without allowing Git to traverse any of its links."""
    import os
    import tempfile
    from pathlib import Path
    if not targets:
        if _link_targets(path):
            raise OSError('new link appeared during cleanup')
        return _git(repo, 'worktree', 'remove', path)
    for link, _, _ in targets:
        relative = str(link.relative_to(path)).replace('\\', '/')
        tracked = _git(path, 'ls-files', '--', relative)
        if tracked.returncode or tracked.stdout:
            raise OSError('tracked link cannot be safely moved: ' + str(link))
    backup = Path(tempfile.mkdtemp(prefix='.haivequeen-links-', dir=str(Path(path).parent)))
    moved = []
    removed = None
    try:
        for index, (link, _, _) in enumerate(targets):
            saved = backup / str(index)
            os.replace(link, saved)
            moved.append((link, saved))
        if _link_targets(path):
            raise OSError('new link appeared during cleanup')
        removed = _git(repo, 'worktree', 'remove', path)
        if removed.returncode:
            return removed
        for _, target, before in targets:
            if _target_size(target) != before:
                raise OSError('link target changed; links retained at ' + str(backup))
        for _, saved in moved:
            kind = _link_kind(saved)
            if kind == 'junction' or (os.name == 'nt' and kind == 'symlink' and
                                       getattr(os.lstat(saved), 'st_file_attributes', 0) & 0x10):
                os.rmdir(saved)
            else:
                saved.unlink()
        moved.clear()
        return removed
    finally:
        if removed is None or removed.returncode:
            for link, saved in reversed(moved):
                if os.path.lexists(saved):
                    link.parent.mkdir(parents=True, exist_ok=True)
                    os.replace(saved, link)
            moved.clear()
        if not moved:
            os.rmdir(backup)

# Inspect readable process working directories before removing a worktree.
def _process_busy(path):
    """Return whether a readable process uses the worktree as its working directory."""
    import os
    if os.name != 'nt':
        for pid in os.listdir('/proc'):
            if pid.isdigit():
                try:
                    if _within(os.readlink('/proc/' + pid + '/cwd'), path):
                        return True
                except OSError:
                    pass
        return False
    import ctypes
    from ctypes import wintypes
    kernel = ctypes.WinDLL('kernel32', use_last_error=True)
    ntdll = ctypes.WinDLL('ntdll')
    class Entry(ctypes.Structure):
        _fields_ = [('size', wintypes.DWORD), ('usage', wintypes.DWORD),
                    ('pid', wintypes.DWORD), ('heap', ctypes.c_size_t),
                    ('module', wintypes.DWORD), ('threads', wintypes.DWORD),
                    ('parent', wintypes.DWORD), ('priority', ctypes.c_long),
                    ('flags', wintypes.DWORD), ('exe', wintypes.WCHAR * 260)]
    class Basic(ctypes.Structure):
        _fields_ = [('reserved', ctypes.c_void_p), ('peb', ctypes.c_void_p),
                    ('reserved2', ctypes.c_void_p * 2), ('pid', ctypes.c_size_t),
                    ('reserved3', ctypes.c_void_p)]
    kernel.CreateToolhelp32Snapshot.restype = wintypes.HANDLE
    kernel.OpenProcess.restype = wintypes.HANDLE
    kernel.OpenProcess.argtypes = [wintypes.DWORD, wintypes.BOOL, wintypes.DWORD]
    kernel.CloseHandle.argtypes = [wintypes.HANDLE]
    kernel.Process32FirstW.argtypes = [wintypes.HANDLE, ctypes.POINTER(Entry)]
    kernel.Process32NextW.argtypes = [wintypes.HANDLE, ctypes.POINTER(Entry)]
    kernel.ReadProcessMemory.argtypes = [wintypes.HANDLE, ctypes.c_void_p, ctypes.c_void_p,
                                         ctypes.c_size_t, ctypes.POINTER(ctypes.c_size_t)]
    ntdll.NtQueryInformationProcess.argtypes = [wintypes.HANDLE, wintypes.ULONG, ctypes.c_void_p,
                                                wintypes.ULONG, ctypes.c_void_p]
    snapshot = kernel.CreateToolhelp32Snapshot(2, 0)
    if snapshot == ctypes.c_void_p(-1).value:
        return None
    def read(handle, address, size):
        """Read exact bytes from a process or reject inaccessible memory."""
        buffer = ctypes.create_string_buffer(size)
        count = ctypes.c_size_t()
        if not kernel.ReadProcessMemory(handle, address, buffer, size, ctypes.byref(count)) or count.value != size:
            raise OSError('process memory is inaccessible')
        return buffer.raw
    try:
        entry = Entry()
        entry.size = ctypes.sizeof(entry)
        if not kernel.Process32FirstW(snapshot, ctypes.byref(entry)):
            return None
        while True:
            handle = kernel.OpenProcess(0x0410, False, entry.pid)
            if handle:
                try:
                    info = Basic()
                    wow = ctypes.c_void_p()
                    if ntdll.NtQueryInformationProcess(handle, 0, ctypes.byref(info), ctypes.sizeof(info), None):
                        raise OSError('process information is inaccessible')
                    ntdll.NtQueryInformationProcess(handle, 26, ctypes.byref(wow), ctypes.sizeof(wow), None)
                    width = 4 if wow.value else ctypes.sizeof(ctypes.c_void_p)
                    peb = wow.value or info.peb
                    pointer = lambda data: int.from_bytes(data, 'little')
                    params = pointer(read(handle, peb + (0x10 if width == 4 else 0x20), width))
                    item = read(handle, params + (0x24 if width == 4 else 0x38), 8 if width == 4 else 16)
                    length = int.from_bytes(item[:2], 'little')
                    address = pointer(item[4:8] if width == 4 else item[8:16])
                    if length and _within(read(handle, address, length).decode('utf-16-le'), path):
                        return True
                except (OSError, UnicodeError, ValueError, OverflowError):
                    pass
                finally:
                    kernel.CloseHandle(handle)
            if not kernel.Process32NextW(snapshot, ctypes.byref(entry)):
                if ctypes.get_last_error() != 18:
                    return None
                break
    finally:
        kernel.CloseHandle(snapshot)
    return False


# Run the cleanup with the process working-directory safety check.
def prune(env, dry=False):
    """Serialize cleanup and retain worktrees with unknown process state."""
    import os
    import time
    from . import loop, platform
    lock = platform.data_dir() / 'worktree-cleanup.lock'
    deadline = time.monotonic() + 30
    while loop._take_pid_lock(lock, own_ok=False) != 0:
        if time.monotonic() >= deadline:
            raise TimeoutError('worktree cleanup is busy')
        time.sleep(0.1)
    try:
        with loop.hive_schedule_lock():
            return _prune_with_checker(env, _process_busy, dry)
    finally:
        if lock.exists() and lock.read_text(encoding='ascii').strip() == str(os.getpid()):
            lock.unlink()

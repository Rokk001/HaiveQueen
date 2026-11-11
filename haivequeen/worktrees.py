"""Safe cleanup of worktrees whose commits are integrated."""
import contextlib
import re
import subprocess

from . import db, runtime, platform as osplat


# Run Git without shell expansion and capture errors for the caller.
def _git(repo, *args):
    """Run Git in the given repository and return its result."""
    return subprocess.run(['git', '-C', str(repo), *args], capture_output=True,
                          text=True, encoding='utf-8', errors='replace', timeout=60, creationflags=subprocess.CREATE_NO_WINDOW if osplat.is_windows() else 0)


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


# Tell whether a worktree lies where HaiveQueen creates its own worktrees.
def _unqueued_eligible(env, path):
    """True when the switch is on and the folder lies directly in the worktree location and starts with the worktree name prefix + '-'."""
    import os
    if not env.settings.get('deleteFinishedWorktrees', True):
        return False
    location = os.path.expanduser(str(env.settings.get('worktreeLocation') or ''))
    parent = os.path.join(str(env.code_repo), location) if location else os.path.dirname(str(env.code_repo).rstrip('\/'))
    norm = lambda item: os.path.normcase(os.path.abspath(item))
    prefix = str(env.settings.get('worktreePrefix') or 'wt') + '-'
    return norm(os.path.dirname(os.path.normpath(path))) == norm(parent) and         os.path.basename(os.path.normpath(path)).lower().startswith(prefix.lower())


# Find out whether a plan that is not finished used or owns a worktree.
def _plan_use(env, path):
    """True when an agent, an Agent assignment or the plan itself names the worktree for a plan that is not DONE or CANCELLED."""
    from . import bees
    conn = getattr(env, 'conn', None)
    if conn is None:
        return False
    plans = runtime.list_plans(conn, env.name)
    finished = {plan['path'] for plan in plans if plan['status'] in ('DONE', 'CANCELLED')}
    for agent in runtime.list_agents(conn, env.name, 'all'):
        if agent.get('plan_path') and agent['plan_path'] not in finished and agent.get('cwd') and _within(agent['cwd'], path):
            return True
    if conn.execute("SELECT 1 FROM sqlite_master WHERE type='table' AND name='bee_worktrees'").fetchone():
        for plan_path, cwd in conn.execute('SELECT plan_path, cwd FROM bee_worktrees WHERE project=?', (env.name,)).fetchall():
            if plan_path not in finished and _within(cwd, path):
                return True
    for plan in plans:
        if plan['path'] in finished:
            continue
        try:
            named = bees.plan_worktree(env, plan['path'])
        except (OSError, ValueError):
            continue
        if named and named[1] and _within(named[1], path) and _within(path, named[1]):
            return True
    return False


# Preserve all worktrees involved in an unresolved plan header conflict.
def _conflicting_plan_use(env, record):
    """Keep the header, checked-out branch, and cut-created worktree until the conflict is resolved."""
    import json
    from . import bees
    if getattr(env, 'conn', None) is None or not env.conn.execute("SELECT 1 FROM sqlite_master WHERE type='table' AND name='plans'").fetchone():
        return False
    for plan in runtime.list_plans(env.conn, env.name):
        if plan['status'] in ('DONE', 'CANCELLED'):
            continue
        try:
            path = plan['path']
            named = bees.plan_worktree(env, path)
            if not named or not bees.worktree_conflict(env, path):
                continue
            try:
                has_kv = env.conn.execute("SELECT 1 FROM sqlite_master WHERE type='table' AND name='kv'").fetchone()
                created = json.loads(runtime.kv_get(env.conn, env.name, 'cut-created:' + path) or 'null') if has_kv else None
            except (ValueError, TypeError):
                created = None
            folders = [named[1]]
            if has_kv:
                folders.append(bees.cut_default(env, path)[1])
            if isinstance(created, dict) and created.get('folder'):
                folders.append(created['folder'])
            if record.get('branch') == named[0] or any(_within(record['worktree'], folder) and _within(folder, record['worktree']) for folder in folders):
                return True
        except (OSError, ValueError, TypeError):
            continue
    return False


# Require completed branch evidence alongside repository and active-agent protections.
def _candidate_reason(env, record, first, queue, integration_ref=None):
    """Verify current branch completion and keep worktrees with any safety blocker."""
    from pathlib import Path
    path = record['worktree']
    protected = [env.code_repo, env.workspace]
    if first or any(_within(path, item) or _within(item, path) for item in protected):
        return 'protected worktree'
    if _conflicting_plan_use(env, record):
        return 'unresolved plan worktree conflict'
    if record.get('locked') or record.get('prunable'):
        return 'locked or missing'
    if any(row.get('branch') == record.get('branch') and row.get('status') in ('ready', 'in_run', 'blocked')
           for row in queue if record.get('branch')):
        return 'branch has an open queue row'
    completed = max((row for row in queue if record.get('branch') and row.get('branch') == record['branch']),
                    key=lambda row: row['id'], default=None)
    unqueued = not completed or completed.get('status') != 'integrated'
    if unqueued:
        # Without an integrated queue row the contents decide (below): only a prefixed worktree in the worktree location, with the switch on.
        if not _unqueued_eligible(env, path):
            return 'branch has no current integrated queue row'
        head = _git(path, 'rev-parse', '--verify', 'HEAD')
        if head.returncode:
            return 'HEAD cannot be read'
    else:
        tip = completed.get('tip') or ''
        if not re.fullmatch(r'[0-9a-fA-F]{7,64}', tip):
            return 'integrated queue tip is invalid'
        # The branch is read from Git only now: worktrees without integrated queue evidence were rejected above without a Git call.
        branch = _git(path, 'symbolic-ref', '--quiet', '--short', 'HEAD')
        if branch.returncode or not branch.stdout.strip() or branch.stdout.strip() != record.get('branch'):
            return 'current branch does not match the worktree inventory'
        resolved = _git(env.code_repo, 'rev-parse', '--verify', tip + '^{commit}')
        head = _git(path, 'rev-parse', '--verify', 'HEAD')
        if resolved.returncode or head.returncode or resolved.stdout.strip() != head.stdout.strip():
            return 'HEAD does not match the integrated queue tip'
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
                    return 'used by assigned Agent'
    if unqueued and _plan_use(env, path):
        return 'used by a plan that is not finished'
    status = _git(path, 'status', '--porcelain', '--untracked-files=all')
    if status.returncode:
        return 'git status failed'
    if status.stdout:
        return 'uncommitted or untracked changes'
    # Files the repository's .gitignore leaves out are build output: git status above already leaves them out, so they never keep a worktree.
    ref = integration_ref or str(env.settings.get('remoteName') or 'origin') + '/' + str(env.settings.get('integrationBranch') or 'main')
    ancestor = _git(env.code_repo, 'merge-base', '--is-ancestor', head.stdout.strip(), ref)
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
    # Propagate traversal failures so the worktree is kept.
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
    # Propagate traversal failures so the worktree is kept.
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
        print(json.dumps({'ok': False, 'error': 'unknown Project'}))
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
            with removal_lock('worktree', path) as mine:   # only this worktree is locked, and only while it is removed
                if not mine:
                    result['kept'].append({'path': path, 'reason': 'another cleanup is removing it'})
                    continue
                if not os.path.isdir(path):
                    result['kept'].append({'path': path, 'reason': 'already removed'})
                    continue
                reason = _candidate_reason(env, record, index == 0, db.queue_rows(env.conn, env.name), integration_ref)
                if reason or process_busy(path) is not False:
                    result['kept'].append({'path': path, 'reason': reason or 'process working directory or unknown process state'})
                    continue
                targets = _link_targets(path)
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

# Tell from the queue alone whether a worktree could ever be removed.
def _may_be_removable(record, queue, env=None):
    """True when the newest queue row of the worktree's branch is 'integrated' and no row of it is open (ready, in_run, blocked);
    with `env`, also a worktree of the worktree location with the name prefix and no open row (the contents decide later)."""
    branch = record.get('branch')
    rows = [row for row in queue if branch and row.get('branch') == branch]
    if any(row.get('status') in ('ready', 'in_run', 'blocked') for row in rows):
        return False
    if env is not None and _unqueued_eligible(env, record['worktree']):
        return True
    if not rows:
        return False
    return max(rows, key=lambda row: row['id']).get('status') == 'integrated'


# Refresh the integration ref before checking any candidate.
def _prune_with_checker(env, process_busy, dry):
    """Fetch the integration branch and evaluate its worktrees safely."""
    import os
    branch = str(env.settings.get('integrationBranch') or 'main')
    remote_name = str(env.settings.get('remoteName') or 'origin')
    records = list_worktrees(env.code_repo)
    queue = db.queue_rows(env.conn, env.name)
    if not any(_may_be_removable(record, queue, env) for record in records[1:]):
        # No worktree belongs to a finished, integrated branch, so nothing can be removed: skip the network (fetch, ls-remote);
        # the usual checks still run and only report why each worktree stays.
        return _prune_candidates(env, records, queue, process_busy, dry)
    fetched = _git(env.code_repo, 'fetch', remote_name, branch)
    if fetched.returncode:
        return {'removed': [], 'kept': [{'path': os.path.normpath(record['worktree']),
                'reason': 'fetch failed: ' + fetched.stderr.strip()} for record in records],
                'notRemovable': [], 'dryRun': bool(dry)}
    tip = _git(env.code_repo, 'rev-parse', '--verify', 'FETCH_HEAD^{commit}')
    if tip.returncode:
        return {'removed': [], 'kept': [{'path': os.path.normpath(record['worktree']),
                'reason': 'cannot resolve fetched integration branch'} for record in records],
                'notRemovable': [], 'dryRun': bool(dry)}
    remote = _git(env.code_repo, 'ls-remote', '--refs', remote_name, 'refs/heads/' + branch)
    if remote.returncode or remote.stdout.strip().split('\t', 1)[0] != tip.stdout.strip():
        return {'removed': [], 'kept': [{'path': os.path.normpath(record['worktree']),
                'reason': 'integration branch changed during fetch'} for record in records],
                'notRemovable': [], 'dryRun': bool(dry)}
    return _prune_candidates(env, records, queue, process_busy, dry, tip.stdout.strip())

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
    # Describe a Windows process snapshot entry.
    class Entry(ctypes.Structure):
        _fields_ = [('size', wintypes.DWORD), ('usage', wintypes.DWORD),
                    ('pid', wintypes.DWORD), ('heap', ctypes.c_size_t),
                    ('module', wintypes.DWORD), ('threads', wintypes.DWORD),
                    ('parent', wintypes.DWORD), ('priority', ctypes.c_long),
                    ('flags', wintypes.DWORD), ('exe', wintypes.WCHAR * 260)]
    # Describe Windows process basic information.
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
    # Read exact bytes from a process or reject inaccessible memory.
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


# The branches of plans that are still being worked on.
def _open_plan_branches(env, plan):
    """Branches named by the other plans of the Project that are not finished; a branch of one of them, or below it, is never cleaned up with `plan`."""
    from . import bees
    found = set()
    for other in runtime.list_plans(env.conn, env.name):
        if other['path'] == plan or other['status'] in ('DONE', 'CANCELLED'):
            continue
        try:
            named = bees.plan_worktree(env, other['path'])
        except (OSError, ValueError):
            continue
        if named:
            found.add(named[0])
    return found


# Delete a branch on the remote only while it is still the checked commit.
def _delete_remote(repo, remote, branch, sha):
    """Delete refs/heads/<branch> on the remote; the lease refuses when it moved after the check, and Git never asks for a password here."""
    import os
    return subprocess.run(['git', '-C', str(repo), 'push', '--force-with-lease=refs/heads/%s:%s' % (branch, sha), remote, ':refs/heads/' + branch], capture_output=True, text=True, encoding='utf-8', errors='replace',
                          timeout=120, env={**os.environ, 'GIT_TERMINAL_PROMPT': '0'}, creationflags=subprocess.CREATE_NO_WINDOW if osplat.is_windows() else 0)


# Clean up the branches of one integrated plan.
def _delete_plan_branches(env, plan, row, remote_name, heads, local, checked_out, protected, result):
    """Delete the plan branch and its sub-branches (`<branch>-...` or `<branch>/...`), local and remote, when each is contained in the integrated tip; everything else stays with its reason."""
    branch, tip = row['branch'], row['tip']
    def belongs(name):
        """The plan branch itself or a branch below it."""
        return name == branch or name.startswith((branch + '-', branch + '/'))
    others = _open_plan_branches(env, plan) - {branch}
    def blocker(name, commit):
        """Why this branch stays, or an empty text."""
        if name in protected:
            return 'protected branch'
        if any(name == other or name.startswith((other + '-', other + '/')) for other in others):
            return 'belongs to another open plan'
        if env.conn.execute("SELECT 1 FROM queue WHERE project=? AND branch=? AND status IN ('ready','in_run','blocked') LIMIT 1", (env.name, name)).fetchone():
            return 'branch has an open queue row'
        if _git(env.code_repo, 'merge-base', '--is-ancestor', commit, tip).returncode:   # also fails for a commit that is not here: unknown work stays
            return 'has commits that are not in the integrated tip'
        return ''
    def recheck(name, commit, local_branch=False):
        """Revalidate mutable start and branch state while holding the target lock."""
        if any(a.get('plan_path') == plan and a.get('status') in ('running', 'starting')
               for a in runtime.list_agents(env.conn, env.name, 'active')):
            return 'an agent of the plan is running'
        if any(name == other or name.startswith((other + '-', other + '/'))
               for other in _open_plan_branches(env, plan) - {branch}):
            return 'belongs to another open plan'
        if local_branch:
            current = _git(env.code_repo, 'rev-parse', '--verify', 'refs/heads/' + name)
            if current.returncode or current.stdout.strip() != commit:
                return 'branch changed during cleanup'
        return blocker(name, commit)
    for name, commit in sorted(local.items()):
        if not belongs(name):
            continue
        key = (env.code_repo, name)
        reason = blocker(name, commit) or ('checked out in a worktree' if name in checked_out else '')   # the slow checks run before the lock
        if not reason:
            with removal_lock('branch', key) as mine:   # only this branch is locked, and only while it is deleted
                reason = 'another cleanup is deleting it' if not mine else ''
                if not reason:
                    reason = recheck(name, commit, local_branch=True)
                if not reason and _git(env.code_repo, 'branch', '-D', name).returncode:
                    reason = 'git branch -D failed'
        if reason:
            result['kept'].append({'branch': name, 'where': 'local', 'reason': reason})
        else:
            result['deleted'].append('local ' + name)
    for name, commit in sorted(heads.items()):
        if not belongs(name):
            continue
        key = (env.code_repo, name)
        reason = blocker(name, commit)
        if not reason:
            with removal_lock('branch', key) as mine:
                reason = 'another cleanup is deleting it' if not mine else ''
                if not reason:
                    reason = recheck(name, commit)
                if not reason:
                    pushed = _delete_remote(env.code_repo, remote_name, name, commit)
                    reason = 'push failed: ' + pushed.stderr.strip()[:200] if pushed.returncode else ''
        if reason:
            result['kept'].append({'branch': name, 'where': 'remote', 'reason': reason})
        else:
            result['deleted'].append('remote ' + name)


# Find the integrated plans and clean up their branches.
def _delete_branches_locked(env):
    """Check every plan whose newest queue row is integrated and whose Plan knows that row; the integration branch is read fresh from the remote first."""
    result = {'deleted': [], 'kept': []}
    integration_branch = str(env.settings.get('integrationBranch') or 'main')
    remote_name = str(env.settings.get('remoteName') or 'origin')
    candidates = []
    active = runtime.list_agents(env.conn, env.name, 'active')
    for hive in db.rows(env.conn, 'SELECT * FROM hives WHERE project=?', (env.name,)):
        plan = hive['plan_path']
        row = env.conn.execute('SELECT * FROM queue WHERE project=? AND plan_path=? ORDER BY id DESC LIMIT 1', (env.name, plan)).fetchone()
        if not row or row['status'] != 'integrated' or hive.get('ready_id') != row['id'] or not row['branch'] or not re.fullmatch(r'[0-9a-fA-F]{7,64}', row['tip'] or ''):
            continue   # without the ready link the Hive still needs the branch to prove the integration
        if any(a.get('plan_path') == plan and a.get('status') in ('running', 'starting') for a in active):
            result['kept'].append({'branch': row['branch'], 'where': 'all', 'reason': 'an agent of the plan is running'})
            continue
        candidates.append((plan, dict(row)))
    if not candidates:
        return result
    fetched = _git(env.code_repo, 'fetch', remote_name, integration_branch)
    listed = _git(env.code_repo, 'ls-remote', '--symref', remote_name) if not fetched.returncode else None
    tip = _git(env.code_repo, 'rev-parse', '--verify', 'FETCH_HEAD^{commit}') if listed and not listed.returncode else None
    if not tip or tip.returncode:
        reason = (fetched.stderr or (listed.stderr if listed else '') or 'cannot read the remote').strip()[:200]
        result['kept'].extend({'branch': row['branch'], 'where': 'all', 'reason': 'remote not readable: ' + reason} for _, row in candidates)
        return result
    remote_tip = tip.stdout.strip()
    heads, protected = {}, {integration_branch, 'main', 'master'}
    for line in listed.stdout.splitlines():
        symref = re.match(r'ref: refs/heads/(\S+)\s+HEAD$', line)
        if symref:
            protected.add(symref.group(1))
        match = re.match(r'([0-9a-f]+)\s+refs/heads/(\S+)$', line)
        if match:
            heads[match.group(2)] = match.group(1)
    if heads.get(integration_branch) != remote_tip:
        result['kept'].extend({'branch': row['branch'], 'where': 'all', 'reason': 'integration branch changed during fetch'} for _, row in candidates)
        return result
    refs = _git(env.code_repo, 'for-each-ref', '--format=%(objectname) %(refname:short)', 'refs/heads')
    local = {name: commit for commit, name in (line.split(' ', 1) for line in refs.stdout.splitlines())} if not refs.returncode else {}
    checked_out = {record.get('branch') for record in list_worktrees(env.code_repo)}
    for plan, row in candidates:
        if _git(env.code_repo, 'merge-base', '--is-ancestor', row['tip'], remote_tip).returncode:
            result['kept'].append({'branch': row['branch'], 'where': 'all', 'reason': 'the integrated tip is not in the integration branch'})
            continue
        _delete_plan_branches(env, plan, row, remote_name, heads, local, checked_out, protected, result)
    return result


# The lock file of one removal: only the worktree or branch being removed is locked, never the scheduling.
def _removal_lock_path(kind, key):
    """Lock file for `kind` ('worktree' or 'branch') and `key` (a path, or (repository path, branch name)) in the data folder."""
    import hashlib
    import os
    from . import platform
    # A worktree is its resolved path; a branch is its repository (resolved path) and its name, so the same name in another repository is another lock.
    if kind == 'worktree':
        text = os.path.normcase(os.path.realpath(str(key)))
    else:
        repo, name = key
        text = os.path.normcase(os.path.realpath(str(repo))) + '|' + str(name)
    return platform.data_dir() / 'removal-locks' / ('%s-%s.lock' % (kind, hashlib.sha1(text.encode('utf-8')).hexdigest()[:20]))


# Hold the removal lock of exactly one worktree or branch while it is removed.
@contextlib.contextmanager
def _removal_guard(path):
    """Nonblocking OS guard for one target; a dead process releases it automatically.

    Keep the guard file in place so contenders always lock the same file, including
    while a stale PID file is recovered or a completed removal unlinks its PID.
    """
    import errno
    import os
    path.parent.mkdir(parents=True, exist_ok=True)
    guard = os.open(str(path) + '.guard', os.O_CREAT | os.O_RDWR, 0o600)
    try:
        try:
            if os.name == 'nt':
                import msvcrt
                msvcrt.locking(guard, msvcrt.LK_NBLCK, 1)
            else:
                import fcntl
                fcntl.flock(guard, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except OSError as exc:
            if exc.errno not in (errno.EACCES, errno.EAGAIN, errno.EDEADLK):
                raise
            yield False
            return
        # Closing the descriptor releases the OS lock, including on exceptions.
        yield True
    finally:
        os.close(guard)


@contextlib.contextmanager
def removal_lock(kind, key):
    """Yield True while this process holds the removal lock of the worktree or branch; yield False at once when another living cleanup holds it
    (no double removal). The lock of a dead holder is taken over."""
    import os
    path = _removal_lock_path(kind, key)
    with _removal_guard(path) as guarded:
        if not guarded or removal_holder(kind, key) not in (None, 0):
            yield False
            return
        path.write_text(str(os.getpid()), encoding='ascii')
        try:
            yield True
        finally:
            try:
                if path.exists() and path.read_text(encoding='ascii').strip() == str(os.getpid()):
                    path.unlink()
            except OSError:
                pass


@contextlib.contextmanager
def removal_locks(targets):
    """Exclude cleanup for these targets through startup; yield the busy target, or None.

    Acquisition never waits. A failed acquisition releases every partial lock before
    returning control, so a caller can wait without holding unrelated targets.
    """
    with contextlib.ExitStack() as held:
        seen = set()
        for kind, key in targets:
            path = _removal_lock_path(kind, key)
            if path in seen:
                continue
            seen.add(path)
            if not held.enter_context(removal_lock(kind, key)):
                held.close()
                yield kind, key
                return
        yield None


@contextlib.contextmanager
def start_with_removal_locks(targets, timeout=30.0):
    """Resolve and lock a direct start under scheduling; wait outside scheduling.

    Recompute the targets after every wait. Hold both locks until the start has
    recorded its running process or embedded assignment.
    """
    import time
    from . import loop
    deadline = time.monotonic() + timeout
    while True:
        with loop.hive_schedule_lock():
            with removal_locks(targets()) as busy:
                if busy is None:
                    yield
                    return
        wait_for_removal(*busy, timeout=max(0.0, deadline - time.monotonic()))
        if time.monotonic() >= deadline:
            raise removal_timeout(*busy)


# Tell whether a living cleanup is removing exactly this worktree or branch; takes no lock and blocks nobody.
def removal_holder(kind, key):
    """Process id of the living cleanup that removes the worktree or branch, 0 when its lock is just created and not written yet, None when nobody removes it."""
    from . import platform as osplat
    path = _removal_lock_path(kind, key)
    try:
        holder = int(path.read_text(encoding='ascii').strip())
    except (OSError, ValueError):
        return 0 if path.exists() else None
    return holder if osplat.process_alive(holder) else None


def removal_busy(kind, key):
    """True while the removal lock of the worktree or branch is held by a living process."""
    return removal_holder(kind, key) is not None


# The wait for a removal ran out.
class RemovalTimeout(TimeoutError):
    """A start waited as long as it waits for the scheduling lock and the removal still runs."""


class RemovalBusy(Exception):
    """Release scheduling before waiting, then repeat selection in the same call."""
    def __init__(self, target, result=None):
        self.target = target
        self.result = result
        super().__init__(*target)


def removal_timeout(kind, key, holder=None):
    """Use the existing translated timeout wording for every cleanup wait."""
    holder = removal_holder(kind, key) if holder is None else holder
    return RemovalTimeout('the %s %s is being removed by a cleanup (process id %s) that is still running' % (kind, key[1] if kind == 'branch' else key, holder or 'unknown'))


# A start for exactly the worktree or branch being removed waits for the end of the removal.
def wait_for_removal(kind, key, timeout=30.0):
    """Wait until no living cleanup removes the worktree or branch, at most `timeout` seconds (the wait for the scheduling lock); then raise TimeoutError naming the removal and the process id of its holder."""
    import time
    deadline = time.monotonic() + timeout
    while True:
        holder = removal_holder(kind, key)
        if holder is None:
            return
        if time.monotonic() >= deadline:
            raise removal_timeout(kind, key, holder)
        time.sleep(0.1)


# Delete the branches of integrated plans.
def delete_integrated_branches(env):
    """Delete local and remote plan and sub-branches of integrated plans when nothing can be lost; returns {deleted, kept}. The program does this, never an agent.
    It locks only the branch it deletes at that moment."""
    return _delete_branches_locked(env)


# The cleanup after an integration and in every round.
def cleanup(env):
    """Run the cleanup steps the Project switched on and log each result; a failing step never stops the next one or the caller."""
    import json
    if env.settings.get('deleteFinishedWorktrees', True):
        try:
            env.log('Worktree cleanup: ' + json.dumps(prune(env), ensure_ascii=False))
        except Exception as exc:
            env.log('Worktree cleanup failed: ' + str(exc))
    if env.settings.get('deleteIntegratedBranches', False):
        try:
            done = delete_integrated_branches(env)
            if done.get('deleted') or done.get('kept'):
                env.log('Branch cleanup: ' + json.dumps(done, ensure_ascii=False))
        except Exception as exc:
            env.log('Branch cleanup failed: ' + str(exc))


# Run the cleanup with the process working-directory safety check.
def prune(env, dry=False):
    """Remove finished worktrees, each under its own removal lock, and retain worktrees with unknown process state."""
    return _prune_with_checker(env, _process_busy, dry)

"""Process hooks and an end audit of the Agent's exclusively assigned worktree."""
import hashlib
import json
import os
from pathlib import Path
import re
import shlex
import subprocess
import sys
import uuid


def _hide_console_windows():
    """Git runs this file as a plain script (python -I): load the sibling platform module by path so the programs it starts open no console window."""
    import importlib.util
    spec = importlib.util.spec_from_file_location('_hq_branchguard_platform', Path(__file__).with_name('platform.py'))
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)   # importing it installs the CREATE_NO_WINDOW hook on subprocess.Popen


if __name__ == '__main__':
    _hide_console_windows()


# NUL cannot occur in a Git branch name. Keep unavailable evidence distinct
# from an identified foreign branch while retaining the existing string API.
AUDIT_ERROR = '\0audit:'


class AuditUnavailable(Exception):
    """The worktree's audit evidence cannot establish a successful check."""


def failure_fields(evidence):
    if evidence.startswith(AUDIT_ERROR):
        return {'branch_audit_error': evidence[len(AUDIT_ERROR):]}
    return {'foreign_branch': evidence} if evidence else {}


def failure_text(evidence):
    if evidence.startswith(AUDIT_ERROR):
        return 'Branch audit unavailable: %s; this package attempt failed.' % evidence[len(AUDIT_ERROR):]
    return 'Foreign branch commit: %s; this package attempt failed.' % evidence


def prepare(folder, cwd, branch):
    """Create an attempt's hooks and return its process environment overrides."""
    folder = Path(folder).resolve()
    folder.mkdir(parents=True, exist_ok=True)
    original = subprocess.run(['git', '-C', str(cwd), 'rev-parse', '--path-format=absolute', '--git-path', 'hooks'],
                              capture_output=True, text=True, check=True).stdout.strip()
    head_log = subprocess.run(['git', '-C', str(cwd), 'rev-parse', '--path-format=absolute', '--git-path', 'logs/HEAD'],
                              capture_output=True, text=True, check=True).stdout.strip()
    path = Path(head_log)
    baseline = path.read_bytes() if path.exists() else b''
    (folder / 'policy.json').write_text(json.dumps({'branch': branch, 'original': original,
        'head_log': str(path), 'log_offset': len(baseline), 'log_prefix': hashlib.sha256(baseline).hexdigest()}), encoding='utf-8')
    command = ' '.join(shlex.quote(str(x).replace('\\', '/')) for x in (sys.executable, '-I', Path(__file__).resolve()))
    # Preserve the repository's other hooks too. No shared Git config or hook is modified.
    hooks = {p.name for p in Path(original).iterdir() if p.is_file() and not p.name.endswith('.sample')} if Path(original).is_dir() else set()
    for hook in hooks | {'pre-commit', 'reference-transaction'}:
        path = folder / hook
        path.write_text('#!/bin/sh\nexec ' + command + ' ' + shlex.quote(str(folder).replace('\\', '/')) + ' ' + shlex.quote(hook) + ' "$@"\n', encoding='utf-8', newline='\n')
        path.chmod(0o755)
    count = int(os.environ.get('GIT_CONFIG_COUNT', '0'))
    overrides = {k: v for k, v in os.environ.items() if k.startswith(('GIT_CONFIG_KEY_', 'GIT_CONFIG_VALUE_'))}
    overrides.update({'GIT_CONFIG_COUNT': str(count + 1), 'GIT_CONFIG_KEY_%d' % count: 'core.hooksPath',
                      'GIT_CONFIG_VALUE_%d' % count: str(folder)})
    return overrides


def violation(folder):
    """Return hook evidence or audit new commits in this attempt's worktree."""
    try:
        path = Path(folder) / 'violation.json'
        if path.exists():
            return json.loads(path.read_text(encoding='utf-8'))['branch']
        policy_path = Path(folder) / 'policy.json'
        if not policy_path.exists():
            raise AuditUnavailable('attempt policy is missing')
        policy = json.loads(policy_path.read_text(encoding='utf-8'))
        foreign = audit(policy)
    except (AuditUnavailable, OSError, ValueError, KeyError, TypeError) as exc:
        foreign = AUDIT_ERROR + str(exc)
    if foreign:
        try:
            record_violation(folder, foreign)
        except OSError:
            # A removed/unwritable guard directory must not prevent the end
            # path from recording the failure in the runtime database.
            pass
    return foreign


def audit(policy):
    """Audit only HEAD's worktree-local log; shared branch logs mix other Agents.

    This fallback survives environment/hook overrides, but needs intact reflogs
    and exclusive use of the assigned worktree. It cannot identify commands in
    another worktree after their Agent environment has been discarded.
    """
    if 'head_log' not in policy:  # Attempts started before the audit was added.
        return ''
    path = Path(policy['head_log'])
    if not path.exists():
        raise AuditUnavailable('assigned worktree HEAD reflog is missing')
    content = path.read_bytes()
    offset = policy['log_offset']
    if hashlib.sha256(content[:offset]).hexdigest() != policy['log_prefix']:
        raise AuditUnavailable('assigned worktree HEAD reflog was expired or rewritten')
    branch = policy['branch']
    for line in content[offset:].decode('utf-8', errors='replace').splitlines():
        _, separator, message = line.partition('\t')
        if not separator:
            continue
        if message.startswith('checkout: moving from '):
            branch = message.rsplit(' to ', 1)[-1]
            if branch == 'HEAD' or re.fullmatch(r'[0-9a-f]{40}|[0-9a-f]{64}', branch):
                branch = '(detached HEAD)'
        elif message.startswith('Branch: renamed refs/heads/'):
            branch = message.rsplit(' to refs/heads/', 1)[-1]
        elif (message.startswith(('commit:', 'commit (', 'cherry-pick:', 'rebase (pick)',
                                  'rebase (reword)', 'rebase (squash)', 'rebase (fixup)'))
              or (message.startswith('merge ') and ': Merge made by ' in message)):
            if branch != policy['branch']:
                return branch
    return ''


def record_violation(folder, branch):
    """Persist detected evidence independently of subsequent reflog changes."""
    temporary = Path(folder) / ('violation-' + uuid.uuid4().hex + '.tmp')
    temporary.write_text(json.dumps({'branch': branch}), encoding='utf-8')
    os.replace(temporary, Path(folder) / 'violation.json')


def main():
    folder, hook, *args = sys.argv[1:]
    policy = json.loads((Path(folder) / 'policy.json').read_text(encoding='utf-8'))
    payload = sys.stdin.buffer.read() if hook == 'reference-transaction' else None
    foreign = ''
    if hook == 'pre-commit':
        current = subprocess.run(['git', 'symbolic-ref', '--quiet', '--short', 'HEAD'], capture_output=True, text=True)
        branch = current.stdout.strip() or '(detached HEAD)'
        if branch != policy['branch']:
            foreign = branch
    elif hook == 'reference-transaction' and args == ['prepared']:
        for line in payload.decode('utf-8').splitlines():
            old, new, ref = line.split(' ', 2)
            branch = ''
            if ref == 'HEAD':
                current = subprocess.run(['git', 'symbolic-ref', '--quiet', '--short', 'HEAD'], capture_output=True, text=True)
                if not current.stdout.strip():
                    branch = '(detached HEAD)'
            elif ref.startswith('refs/heads/') and ref != 'refs/heads/' + policy['branch']:
                branch = ref[len('refs/heads/'):]
            if branch and set(new) != {'0'}:
                # A checkout/reset to an existing commit is no new commit. During
                # 'prepared' the new commit has not reached any ref yet.
                reachable = subprocess.run(['git', 'for-each-ref', '--contains', new, '--format=%(refname)'], capture_output=True, text=True)
                if reachable.returncode == 0 and not reachable.stdout.strip():
                    foreign = branch
    if foreign:
        record_violation(folder, foreign)
        print('Agent commit refused on foreign branch: ' + foreign, file=sys.stderr)
        return 1
    original = Path(policy['original']) / hook
    if original.is_file() and os.access(original, os.X_OK):
        return subprocess.run(['sh', '-c', 'exec "$0" "$@"', str(original), *args], input=payload).returncode
    return 0


if __name__ == '__main__':
    sys.exit(main())

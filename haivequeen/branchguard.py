"""Git hooks inherited only by one Agent's commands, never repository configuration."""
import json
import os
from pathlib import Path
import shlex
import subprocess
import sys
import uuid


def prepare(folder, cwd, branch):
    """Create an attempt's hooks and return its process environment overrides."""
    folder = Path(folder).resolve()
    folder.mkdir(parents=True, exist_ok=True)
    original = subprocess.run(['git', '-C', str(cwd), 'rev-parse', '--path-format=absolute', '--git-path', 'hooks'],
                              capture_output=True, text=True, check=True).stdout.strip()
    (folder / 'policy.json').write_text(json.dumps({'branch': branch, 'original': original}), encoding='utf-8')
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
    """The durable, attempt-specific evidence survives reflog expiration and checkout changes."""
    path = Path(folder) / 'violation.json'
    return json.loads(path.read_text(encoding='utf-8'))['branch'] if path.exists() else ''


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
        temporary = Path(folder) / ('violation-' + uuid.uuid4().hex + '.tmp')
        temporary.write_text(json.dumps({'branch': foreign}), encoding='utf-8')
        os.replace(temporary, Path(folder) / 'violation.json')
        print('Agent commit refused on foreign branch: ' + foreign, file=sys.stderr)
        return 1
    original = Path(policy['original']) / hook
    if original.is_file() and os.access(original, os.X_OK):
        return subprocess.run(['sh', str(original), *args], input=payload).returncode
    return 0


if __name__ == '__main__':
    sys.exit(main())

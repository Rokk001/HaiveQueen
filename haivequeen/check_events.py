"""Durable, run-bound check results; arbitrary console text is never a verdict."""
import hashlib
import json
import os
import subprocess
from pathlib import Path


def environment(path, token, commit):
    return {'HQ_CHECK_EVENTS': str(path), 'HQ_CHECK_RUN': token,
            'HQ_CHECK_COMMIT': commit or ''}


def report(event, **fields):
    """Runner API: one writer, append after the permitted retries have been decided."""
    record = dict(fields, event=event, run=os.environ['HQ_CHECK_RUN'],
                  commit=os.environ['HQ_CHECK_COMMIT'])
    with open(os.environ['HQ_CHECK_EVENTS'], 'ab') as stream:
        stream.write((json.dumps(record) + '\n').encode('utf-8'))
        stream.flush()
        os.fsync(stream.fileno())


def consume(job):
    """Read complete records only; retain a failure even if later records say pass."""
    try:
        return _consume(job)
    except OSError as exc:
        state = job['checks']
        state['failures']['protocol'] = 'cannot read check events: %s' % exc
        return state


def _consume(job):
    state = job.setdefault('checks', {'offset': 0, 'results': {}, 'failures': {}, 'attempts': []})
    path = job.get('check_events')
    if not path:
        return state
    if not Path(path).exists():
        if state['offset']:
            state['failures']['protocol'] = 'check event journal disappeared'
        return state
    with open(path, 'rb') as stream:
        if Path(path).stat().st_size < state['offset']:
            state['failures']['protocol'] = 'check event journal was truncated'
            return state
        stream.seek(state['offset'])
        for _ in range(1000):
            line = stream.readline(65537)
            if len(line) > 65536:
                state['failures']['protocol'] = 'check event exceeds 64 KiB; use a durable output reference'
                break
            if not line or not line.endswith(b'\n'):
                break
            state['offset'] = stream.tell()
            try:
                row = json.loads(line)
                if row.get('run') != job.get('check_run', job['token']) or row.get('commit') != job.get('head'):
                    raise ValueError('check event belongs to another run or commit')
                event = row['event']
                if event == 'suite':
                    if 'suite' in state:
                        raise ValueError('suite manifest changed during the run')
                    ids = row['checks']
                    if not isinstance(ids, list) or not ids or any(not isinstance(x, str) or not x for x in ids) or len(set(ids)) != len(ids):
                        raise ValueError('invalid required check list')
                    state['suite'] = row
                elif event == 'result':
                    name, code = row['check'], row['exit']
                    if name not in state.get('suite', {}).get('checks', []) or type(code) is not int or type(row.get('final')) is not bool:
                        raise ValueError('invalid check result')
                    if name in state['results']:
                        raise ValueError('a final check result cannot be replaced')
                    state['attempts'].append(row)
                    if row['final']:
                        state['results'][name] = row
                        if code:
                            state['failures'][name] = 'exit %s' % code
                elif event == 'shared_writes_finished':
                    state['shared_writes_finished'] = True
                else:
                    raise ValueError('unknown check event')
            except (ValueError, KeyError, TypeError, AttributeError) as exc:
                state['failures']['protocol'] = str(exc)
    return state


def verify(job, folder, head):
    """Verify the frozen checkout and artifact identities at release and completion."""
    checks = job.get('checks', {})
    suite = checks.get('suite', {})
    if not job.get('head') or head != job['head']:
        return 'the checked commit changed'
    if not _clean_checkout(folder):
        return 'tracked files differ from the checked commit'
    artifacts = suite.get('artifacts')
    if not isinstance(artifacts, dict):
        return 'artifact identities are not declared'
    root = Path(folder).resolve()
    for relative, digest in artifacts.items():
        if not isinstance(relative, str) or Path(relative).is_absolute() or not isinstance(digest, str):
            return 'invalid artifact identity'
        path = (root / relative).resolve()
        if not path.is_relative_to(root) or not path.is_file():
            return 'an artifact is missing or outside the checked worktree'
        try:
            with path.open('rb') as stream:
                actual = hashlib.file_digest(stream, 'sha256').hexdigest()
        except OSError:
            return 'a checked artifact cannot be read'
        if actual != digest:
            return 'a checked artifact changed'
    return ''


def _clean_checkout(folder):
    try:
        return subprocess.run(['git', '-C', str(folder), 'diff', '--quiet', 'HEAD', '--'],
                              stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL,
                              stderr=subprocess.DEVNULL, timeout=30).returncode == 0
    except (OSError, subprocess.TimeoutExpired):
        return False


def release_reason(job, folder, head):
    state = job.get('checks', {})
    suite = state.get('suite', {})
    if 'protocol' in state.get('failures', {}):
        return 'the check reporting protocol is invalid'
    if not state.get('shared_writes_finished'):
        return 'shared integration/deployment writes are not confirmed finished'
    resources = suite.get('resources')
    if not isinstance(resources, list) or any(not isinstance(x, str) or not x for x in resources):
        return 'remaining resource reservations are not declared'
    if suite.get('isolated_worktree') is not True:
        return 'remaining checks do not confirm an isolated worktree'
    return verify(job, folder, head)


def summary(checks):
    from .i18n import tr
    return tr('Integration failed: %d checks red. Further checks running for diagnosis: %d/%s completed.',
        len(checks.get('failures', {})), len(checks.get('results', {})),
        len(checks['suite']['checks']) if checks.get('suite') else '?')


def finish(job, code, output):
    state = consume(job)
    while True:
        offset = state['offset']
        state = consume(job)
        if state['offset'] == offset:
            break
    path = job.get('check_events')
    try:
        if path and Path(path).exists() and Path(path).stat().st_size != state['offset']:
            state['failures']['protocol'] = 'unfinished check event at command completion'
    except OSError:
        state['failures']['protocol'] = 'check event journal cannot be verified'
    if state.get('suite'):
        for name in state['suite']['checks']:
            if name not in state['results']:
                state['failures'][name] = 'no final result (command exit %s)' % code
    if code:
        state['failures']['command'] = 'exit %s' % code
    state.update(complete=True, command_exit=code, output=output)
    return state


def resource_wait(conn, required=None, exclude=None):
    """Keep diagnostic leases independent of FIFO integration ownership."""
    if not isinstance(required, list) or any(not isinstance(x, str) for x in required):
        required = None
    for project, key, value in conn.execute("SELECT project,key,value FROM kv WHERE key LIKE 'orch:%'"):
        if (project, key[5:]) == exclude:
            continue
        try:
            state = json.loads(value)
        except (TypeError, ValueError):
            continue
        if not isinstance(state, dict) or state.get('phase') != 'diagnosing' or not state.get('job'):
            continue
        held = state['job'].get('checks', {}).get('suite', {}).get('resources')
        if not isinstance(held, list) or any(not isinstance(x, str) for x in held):
            held = None
        if held is None or (held and (required is None or set(required) & set(held))):
            return 'waiting for diagnostic resources held by %s: %s' % (key[5:], ', '.join(held or ['undeclared footprint']))
    return ''

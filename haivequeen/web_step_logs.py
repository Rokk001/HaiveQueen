"""Read-only presentation of integration journals and legacy command results."""
import json
import re
from datetime import datetime, timezone
from pathlib import Path

from . import action_logs, orchestrator, runtime, plans


def read(conn, project, plan, step):
    state = orchestrator.load(conn, project, plan) or {}
    status = (state.get('steps') or {}).get(step, 'todo')
    current = state.get('step') == step
    job = state.get('job') or {}
    blocker = runtime.kv_get(conn, project, 'hive-blocker:' + plan) or ''
    waiting = current and state.get('phase') == 'pipeline' and bool(blocker) and not job
    journal = action_logs.latest(project, plan, step)
    historical = False
    reason = ''
    if journal:
        record = journal
        same_run = bool(state.get('log_file')) and Path(state['log_file']) == Path(journal['path'])
        historical = bool(state) and (waiting or status == 'todo' or
                                     (current and status == 'running' and not same_run))
        if not historical:
            if journal['result']:
                status = {'succeeded':'done', 'failed':'red', 'cancelled':'cancelled'}.get(journal['result'], journal['result'])
                reason = journal['message']
            elif same_run and current:
                status = 'running'
            elif not state:
                status = 'unknown'  # An unfinished old journal is not proof of a live process.
    else:
        raw = runtime.kv_get(conn, project, 'orch-log:' + plan + ':' + step)
        record = json.loads(raw) if raw else {}
        if not record and step == 'Check':
            raw = runtime.kv_get(conn, project, 'orch-check:' + plan)
            record = json.loads(raw) if raw else {}
            if record:
                record['exit_code'] = record.get('exit')
        historical = status in ('running', 'todo') and bool(record)
        if not state and record.get('exit_code') is not None:
            status = 'done' if record['exit_code'] == 0 else 'red'
            historical = False
    if waiting:
        status, reason = 'waiting', blocker
    fix = state.get('fix') or {}
    console = record.get('output', '')
    references = referenced_logs(conn, project, plan, console + '\n' + reason)
    output = console
    for item in references:
        output += '\n\n--- ' + item['path'] + ' ---\n' + (item.get('content') or item.get('error', ''))
    return {'console_output':console, 'references':references, 'step':step, 'status':status, 'phase':state.get('phase', ''),
            'exit_code':record.get('exit_code'), 'output':output,
            'error':fix.get('error', '') if fix.get('step') == step else '',
            'reason':reason, 'historical':historical,
            'started_at':record.get('started_at'), 'ended_at':record.get('ended_at')}


def referenced_logs(conn, project, plan, text):
    """Read only explicitly named log files inside this plan's worktree."""
    names = list(dict.fromkeys(match.group(1).replace('\\', '/')
        for match in re.finditer(r"(?i)\b(?:see|log)\s+[\"']?(out[\\/][^\r\n\"']*?\.(?:txt|log))(?=[\s\"']|$)", text)))
    if not names:
        return []
    row = conn.execute('SELECT workspace FROM projects WHERE name=?', (project,)).fetchone()
    root = None
    try:
        workspace = Path(row[0]).resolve()
        source = (workspace / plan).resolve()
        if source.is_relative_to(workspace):
            named = plans.load_plan(str(source)).worktree
            if named and Path(named).is_absolute():
                root = Path(named).resolve()
    except (OSError, ValueError, TypeError):
        pass
    result = []
    for name in names:
        item = {'path':name}
        try:
            if root is None:
                raise ValueError('Plan worktree unavailable')
            path = (root / name).resolve()
            if not path.is_relative_to(root / 'out') or '..' in Path(name).parts:
                raise ValueError('Path outside plan output folder')
            stat = path.stat()
            item['modified_at'] = datetime.fromtimestamp(stat.st_mtime, timezone.utc).isoformat()
            item['size'] = stat.st_size
            # Bound each response; the original file remains untouched.
            with path.open('rb') as stream:
                raw = stream.read(8 * 1024 * 1024)
            encoding = 'utf-16' if raw.startswith((b'\xff\xfe', b'\xfe\xff')) else 'utf-8-sig'
            item['content'] = raw.decode(encoding, errors='replace')
            item['truncated'] = stat.st_size > len(raw)
        except (OSError, ValueError) as error:
            item['error'] = 'Referenced log unavailable: ' + name + ' (' + str(error) + ')'
        result.append(item)
    return result

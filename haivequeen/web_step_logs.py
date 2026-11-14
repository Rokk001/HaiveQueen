"""Read-only presentation of integration journals and legacy command results."""
import json
from pathlib import Path

from . import action_logs, orchestrator, runtime


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
    return {'step':step, 'status':status, 'phase':state.get('phase', ''),
            'exit_code':record.get('exit_code'), 'output':record.get('output', ''),
            'error':fix.get('error', '') if fix.get('step') == step else '',
            'reason':reason, 'historical':historical,
            'started_at':record.get('started_at'), 'ended_at':record.get('ended_at')}

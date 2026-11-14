"""Compatibility declarations and the graceful-update waiting gate.

A release declares whether it can share the data directory with all older
versions. Missing or invalid declarations are deliberately treated as unsafe.
"""
import json
from pathlib import Path

from . import agent_process, db, loop, runtime
from . import platform as osplat

METADATA = 'haivequeen-update.json'
UNKNOWN_REASON = 'Release compatibility with older versions is unknown'


def compatibility(release):
    """Return a conservative compatibility decision for a staged release."""
    try:
        value = json.loads((Path(release) / METADATA).read_text(encoding='utf-8'))
        if not isinstance(value, dict) or type(value.get('compatible_with_older')) is not bool:
            raise ValueError('missing compatibility declaration')
        compatible = value['compatible_with_older']
        reason = value.get('reason', '')
        if not isinstance(reason, str) or (not compatible and not reason.strip()):
            raise ValueError('missing incompatibility reason')
        return {'compatible': compatible, 'reason': reason.strip()}
    except (OSError, ValueError, TypeError):
        return {'compatible': False, 'reason': UNKNOWN_REASON}


def running_jobs(conn):
    """Describe registered work conservatively, including native subagents."""
    jobs = []
    for row in runtime.list_agents(conn, scope='all'):
        if not row.get('archived') and agent_process.counts(row):
            jobs.append({'kind': 'Queen' if row.get('phase') == 'queen' else 'Agent',
                         'name': row['name'], 'project': row['project']})
    if conn.execute("SELECT 1 FROM sqlite_master WHERE type='table' AND name='embedded_bees'").fetchone():
        for row in conn.execute('SELECT project,name FROM embedded_bees WHERE ended_at IS NULL'):
            jobs.append({'kind': 'Agent', 'name': row['name'], 'project': row['project']})
    for row in db.rows(conn, "SELECT project,key,value FROM kv WHERE key LIKE 'orch:%'"):
        try:
            job = json.loads(row['value']).get('job') or {}
            step = job.get('step')
            if step not in ('Build', 'Deploy'):
                continue
            pid = job.get('pid')
            # A not-yet-published PID is still starting. A result file means
            # a detached job has completed even before the next round adopts it.
            result = job.get('file')
            if result and Path(result).is_file():
                continue
            if pid and not osplat.process_alive(pid):
                continue
            jobs.append({'kind': step, 'name': row['key'][len('orch:'):], 'project': row['project']})
        except (ValueError, TypeError, AttributeError):
            # A malformed state cannot prove that no job is running.
            jobs.append({'kind': 'Unknown', 'name': row['key'], 'project': row['project']})
    return jobs


def begin_wait(conn, release, jobs=running_jobs):
    """Stop new work before collecting blockers for an incompatible release.

    The caller persists and refreshes the returned status while it waits and
    must wait for the old core to exit before switching. This function never
    stops running jobs. Compatible releases leave scheduling untouched.
    """
    info = compatibility(release)
    if info['compatible']:
        return {'waiting': False, 'reason': '', 'jobs': []}
    with loop.hive_schedule_lock(reason='incompatible update'):
        stop = loop.stop_flag()
        stop.parent.mkdir(parents=True, exist_ok=True)
        stop.write_text('stop requested %s (self-update incompatible release)\n' % db.now(), encoding='utf-8')
        blockers = jobs(conn)
    return {'waiting': bool(blockers), 'reason': info['reason'], 'jobs': blockers}

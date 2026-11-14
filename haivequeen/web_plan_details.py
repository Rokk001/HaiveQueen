"""Package descriptions and their recorded progress for the plan reader."""
from dataclasses import asdict
from pathlib import Path
import re

from . import db, hives, plans, runtime
from . import platform as osplat


def substeps(prompt, reported):
    section = re.search(r'^Steps:\s*\n(.*?)^Paths:', prompt, re.M | re.S)
    if not section:
        return []
    items = re.findall(r'^\d+\.\s+(.*?)(?=^\d+\.\s|\Z)', section[1], re.M | re.S)
    count = re.fullmatch(r'(\d+)/(\d+)', reported or '')
    known = bool(count and int(count[2]) == len(items))
    return [{'number':i+1, 'text':re.sub(r'^\d+\.\s+', '', text.strip()),
             'status':('done' if i < int(count[1]) else 'pending') if known else 'unknown'}
            for i, text in enumerate(items)]


def package_prompt(project, package, agents):
    newest = max((a for a in agents if a.get('package') == package and a.get('phase') == 'bee'),
                 key=lambda a: a.get('id', 0), default=None)
    if not newest:
        return ''
    root = osplat.runs_dir().resolve()
    path = (root / project / newest['name'] / 'prompt.md').resolve()
    if not path.is_relative_to(root):
        return ''
    try:
        return path.read_text(encoding='utf-8-sig')
    except OSError:
        return ''


def packages(conn, project, plan, text):
    declared = plans.parse_plan_text(text, plan).packages
    stored = {p['id']: p for p in runtime.list_packages(conn, project, plan)}
    progress = {}
    if conn.execute("SELECT 1 FROM sqlite_master WHERE name='package_progress'").fetchone():
        progress = {p['package']: p for p in db.rows(conn,
                    'SELECT * FROM package_progress WHERE project=? AND plan_path=?', (project, plan))}
    agents = [a for a in runtime.list_agents(conn, project, 'all') if a.get('plan_path') == plan]
    notes = db.rows(conn, "SELECT project,agent,text FROM events WHERE project=? AND kind='note' ORDER BY id", (project,))
    ids = list(dict.fromkeys([p.id for p in declared] + list(stored)))
    steps = hives.package_steps(conn, project, plan, ids, agents, notes)
    definitions = {p.id: asdict(p) for p in declared}
    result = []
    for pid in ids:
        definition = definitions.get(pid, {})
        package = {**definition, **stored.get(pid, {})}
        record = progress.get(pid, {})
        active = next((a for a in reversed(agents) if a.get('package') == pid and
                       not a.get('archived') and a.get('status') in runtime.ACTIVE_STATUSES), None)
        status = record.get('state', 'open')
        if active and status not in ('done', 'rejected'):
            status = 'working'
        result.append({'id':pid, 'title':package.get('title') or pid, 'status':status,
                       'agent':(active or {}).get('person') or (active or {}).get('name') or record.get('agent', ''),
                       'reason':record.get('reason') or package.get('lock_reason') or '',
                       'depends_on':package.get('depends_on') or [],
                       'requirements':definition.get('requirements') or [],
                       'verify':package.get('verify', ''), 'class':package.get('class') or package.get('cls', ''),
                       'substeps':substeps(package_prompt(project, pid, agents), steps.get(pid, {}).get('step', '')),
                       **steps.get(pid, {})})
    return result

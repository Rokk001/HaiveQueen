"""Browser draft writing through the existing file transaction and runtime guards."""
import fnmatch
import hashlib
import os
import re
from pathlib import Path, PurePosixPath

import tomlkit

from . import config, db, filetxn, hives, loop, plans, runtime


def revision(raw):
    return hashlib.sha256(raw).hexdigest()


def matches(path, pattern):
    """Match project-relative glob segments, including recursive **."""
    parts, tokens = os.path.normcase(path).replace('\\', '/').split('/'), os.path.normcase(pattern).replace('\\', '/').split('/')
    tokens = [token for token in tokens if token not in ('', '.')]
    def match(i, j):
        if j == len(tokens):
            return i == len(parts)
        if tokens[j] == '**':
            return match(i, j + 1) or i < len(parts) and not parts[i].startswith('.') and match(i + 1, j)
        if i < len(parts) and parts[i].startswith('.') and not tokens[j].startswith('.'):
            return False
        return i < len(parts) and fnmatch.fnmatchcase(parts[i], tokens[j]) and match(i + 1, j + 1)
    return match(0, 0)


def project_options(workspace):
    cfg = config.load_project(workspace)
    globs = cfg['planGlobs'] or plans.DEFAULT_GLOBS
    prefix = []
    for part in globs[0].replace('\\', '/').split('/')[:-1]:
        if any(c in part for c in '*?['):
            break
        if part not in ('', '.'):
            prefix.append(part)
    return {'plan_globs':list(globs), 'plan_exclude':cfg['planExclude'], 'suggested_plan_folder':'/'.join(prefix)}


def target(conn, project, name):
    registered = conn.execute('SELECT workspace FROM projects WHERE name=?', (project,)).fetchone()
    if not registered or not registered[0]:
        raise ValueError('Unknown project')
    if not isinstance(name, str) or not name or '\\' in name or ':' in name or any(ord(c) < 32 for c in name):
        raise ValueError('Use a project-relative Markdown path')
    if PurePosixPath(name).is_absolute() or any(part in ('', '.', '..') or part.endswith((' ', '.')) for part in name.split('/')) or not name.endswith('.md'):
        raise ValueError('Use a project-relative Markdown path')
    workspace = Path(registered[0]).resolve()
    path = (workspace / name).resolve()
    if not path.is_relative_to(workspace):
        raise ValueError('Plan is outside the project workspace')
    options = project_options(workspace)
    if not any(matches(name, pattern) for pattern in options['plan_globs']) or any(matches(name, pattern) for pattern in options['plan_exclude']):
        raise ValueError('Plan path must match the project plan patterns and exclusions')
    return path


def draft_text(text):
    if not isinstance(text, str) or not text.strip():
        raise ValueError('Write or import Markdown first')
    text = text.lstrip('\ufeff')
    parsed = plans.parse_plan_text(text)
    if parsed.errors:
        raise ValueError('; '.join(parsed.errors))
    if not parsed.title:
        raise ValueError('The plan needs a Markdown heading')
    lines = text.splitlines(keepends=True)
    if lines[0].strip() == '+++':
        end = next(i for i in range(1, len(lines)) if lines[i].strip() == '+++')
        header = tomlkit.parse(''.join(lines[1:end]))
        body = ''.join(lines[end + 1:])
    else:
        header, body = tomlkit.document(), text
    header['status'] = 'DRAFT'
    return '+++\n' + tomlkit.dumps(header).rstrip('\r\n') + '\n+++\n' + body


def save(conn, data, *, create=False):
    with loop.hive_schedule_lock(), filetxn.locked():
        project = data.get('project')
        name = data.get('filename') if create else data.get('plan')
        path = target(conn, project, name)
        blocked = filetxn.recovery_block_reason(conn, project)
        if blocked:
            raise ValueError(blocked)
        raw = filetxn.read(path)
        stored = conn.execute('SELECT status FROM plans WHERE project=? AND path=?', (project, name)).fetchone()
        if create:
            if raw is not None or stored:
                raise ValueError('A plan already exists at this path')
        else:
            if any(a.get('plan_path') == name and a.get('status') in runtime.ACTIVE_STATUSES for a in runtime.list_agents(conn, project, 'active')):
                raise ValueError('Only an idle DRAFT can be edited')
            hive = hives.get(conn, project, name)
            if hive and hive.get('state') in ('running', 'starting', 'integrating', 'integration_wait'):
                raise ValueError('Only an idle DRAFT can be edited')
            if raw is None or not stored or stored[0] != 'DRAFT' or plans.parse_plan_text(raw.decode('utf-8-sig')).status != 'DRAFT':
                raise ValueError('Only an existing DRAFT can be edited')
            if data.get('revision') != revision(raw):
                raise ValueError('Plan changed since loading; reload before saving')
        contents = draft_text(data.get('content')).encode('utf-8')
        with filetxn.transaction(conn):
            filetxn.write(path, contents)
            db.add_event(conn, project, 'plan-draft', 'Created draft' if create else 'Saved draft', data={'plan':name})
        runtime.sync_plans(conn, project)
        return {'ok':True, 'plan':name, 'revision':revision(contents), 'status':'DRAFT'}


def question_context(conn, question):
    """Expose recorded evidence only; an answer is never an acceptance verdict."""
    project, plan = question['project'], question['plan_path']
    item = question.get('item') or ''
    question['kind'] = 'acceptance' if item == 'owner-check' or item.startswith('owner-check-') else 'question'
    question['owner_check_result'] = db.owner_check_result(conn, question['id'])
    question['context'] = {'project':project, 'plan':plan, 'item':item}
    state = ''
    registered = conn.execute('SELECT workspace FROM projects WHERE name=?', (project,)).fetchone()
    if registered and registered[0] and plan:
        workspace = Path(registered[0]).resolve()
        source = Path(plans.state_file_path(workspace / plan)).resolve()
        if source.is_relative_to(workspace) and source.is_file():
            state = source.read_text(encoding='utf-8-sig')
    def section(name):
        found = re.search(r'^##\s+' + name + r'\s*\r?\n(.*?)(?=^##\s|\Z)', state, re.M | re.S | re.I)
        return found[1].strip() if found else ''
    decisions = [d for d in runtime.list_decisions(conn, project) if not d.get('plan_path') or d['plan_path'] == plan]
    question['evidence'] = {'requirements':section('Requirements'), 'completeness':section('Completeness'), 'decisions':decisions}
    return question

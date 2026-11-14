"""Evidence-bound package diagnosis and one-use retry authorization."""
import hashlib
import json

from . import bees, db, repair_review, roles, runtime


def _digest(value):
    return hashlib.sha256(json.dumps(value, sort_keys=True).encode('utf-8')).hexdigest()


def _key(plan, package):
    return 'package-diagnosis:' + json.dumps([plan, package], separators=(',', ':'))


def scope(conn, project, plan, package, commit):
    """Bind a review to all recorded attempts without copying them into prompts."""
    known = repair_review.decisions(conn, project, plan)
    digest = hashlib.sha256()
    if conn.execute("SELECT 1 FROM sqlite_master WHERE name='bee_attempt_signals' AND type='table'").fetchone():
        for row in conn.execute('SELECT rowid,class,attempt,open_requirements,error,counted FROM bee_attempt_signals '
                                'WHERE project=? AND plan_path=? AND package=? ORDER BY rowid', (project, plan, package)):
            digest.update(json.dumps(list(row)).encode('utf-8'))
            digest.update(b'\n')
    return {'commit': commit, 'failure_log': 'package-attempts:' + digest.hexdigest(),
            'decisions': _digest(known)}


def _load(conn, project, plan, package):
    raw = runtime.kv_get(conn, project, _key(plan, package))
    return json.loads(raw) if raw else {}


def accept(conn, project, plan, package, path, expected, current_class, offered):
    """Validate a diagnosis; a technical report grants one scoped retry only."""
    if scope(conn, project, plan, package, expected['commit']) != expected:
        raise ValueError('diagnosis does not match current attempts and decisions')
    known = repair_review.decisions(conn, project, plan)
    report = repair_review.read(path, expected['commit'], expected['failure_log'], known)
    selected = report.get('repair_class', current_class)
    if report['kind'] == 'technical':
        if selected not in offered:
            raise ValueError('diagnosed repair class is not configured for this package')
        if selected != current_class and (not isinstance(report.get('class_reason'), str) or not report['class_reason'].strip()):
            raise ValueError('a repair class change requires a diagnosis reason')
    signature = _digest([expected['commit'], report['evidence'], report.get('next_action'), selected])
    with db.transaction(conn):
        state = _load(conn, project, plan, package)
        accepted = state.get('accepted', [])
        if signature in accepted:
            raise ValueError('this diagnosis has already authorized a retry')
        state.update(scope=expected, report=report, accepted=[*accepted, signature],
                     authorization={'class': selected, 'instruction': '\n'.join(
                         report[k] for k in ('analysis', 'next_action', 'why_new'))}
                     if report['kind'] == 'technical' else None)
        runtime.kv_set(conn, project, _key(plan, package), json.dumps(state))
    return report


def authorization(conn, project, plan, package, current):
    if scope(conn, project, plan, package, current['commit']) != current:
        return None
    state = _load(conn, project, plan, package)
    return state.get('authorization') if state.get('scope') == current else None


def consume(conn, project, plan, package, current):
    """Consume only after a successful start; no attempt counters are reset."""
    with db.transaction(conn):
        if scope(conn, project, plan, package, current['commit']) != current:
            return False
        state = _load(conn, project, plan, package)
        if state.get('scope') != current or not state.get('authorization'):
            return False
        state['authorization'] = None
        runtime.kv_set(conn, project, _key(plan, package), json.dumps(state))
        return True


def prompt(conn, project, plan, package, commit, output_path, current_class, offered):
    known = repair_review.decisions(conn, project, plan)
    current = scope(conn, project, plan, package, commit)
    history = bees.attempt_history(conn, project, plan, package, limit=3)
    sources = ('\nRead relevant existing evidence only. The failure_log value is an attempt-history identity, '
               'not a file path. Retrieve older attempts with --before and complete error detail with --attempt.\n'
               'bzz agent history --project %s --plan %s --package %s\n'
               'bzz decisions list --project %s\n' % tuple(json.dumps(v) for v in (project, plan, package, project)))
    previous = _load(conn, project, plan, package).get('report', {})
    if previous:
        sources += '\nPrevious diagnosis preview: ' + json.dumps({k: str(previous[k])[:300]
            for k in ('analysis', 'next_action', 'why_new') if k in previous})
        sources += '\nFull previous diagnosis: bzz kv get --project %s --key %s\n' % (
            json.dumps(project), json.dumps(_key(plan, package)))
    return roles.role_text('diagnosis', {
        'output_path': output_path, 'commit': commit, 'failure_log': current['failure_log'],
        'decision_ids': json.dumps([d['id'] for d in known]),
        'decisions': json.dumps([{'id': d['id'], 'text': str(d.get('text', ''))[:200]} for d in known[-8:]]),
        'history': json.dumps(history) + sources,
    }) + '\nCurrent repair class: %s\nAvailable repair classes: %s\n' % (current_class, json.dumps(offered))

def request(env, plan, package, current_class):
    """Queue a scoped diagnosis without asking the owner or resetting attempts."""
    state = _load(env.conn, env.name, plan, package)
    if state.get('phase') in ('pending', 'running'):
        return
    if state.get('phase') == 'owner' and state.get('question'):
        question = env.conn.execute('SELECT status FROM questions WHERE id=? AND project=? AND plan_path=? AND item=?',
                                    (state['question'], env.name, plan, package)).fetchone()
        if question and question[0] == 'open':
            return
    state.update(phase='pending', current_class=current_class, authorization=None)
    runtime.kv_set(env.conn, env.name, _key(plan, package), json.dumps(state))


def completed(env, plan, package):
    state = _load(env.conn, env.name, plan, package)
    if state:
        state.update(phase='completed', authorization=None)
        runtime.kv_set(env.conn, env.name, _key(plan, package), json.dumps(state))


def reviews(env, plan):
    return {p['id']: state for p in env.packages(plan)
            if (state := _load(env.conn, env.name, plan, p['id']))
            and state.get('phase') != 'completed'}


def waiting(env, plan):
    """Do not churn Queens while all remaining work awaits diagnosis or its answer."""
    active = reviews(env, plan)
    blocked = {key for key, state in active.items() if state.get('phase') in ('pending', 'running', 'owner')}
    if not blocked:
        return False
    pending = [p for p in env.packages(plan) if not bees.package_done(env.conn, env.name, plan, p['id'])]
    while True:
        dependent = {p['id'] for p in pending if any(dep in blocked for dep in p['depends'])}
        if dependent <= blocked:
            break
        blocked.update(dependent)
    return bool(pending) and all(p['id'] in blocked for p in pending)


def checkout(env, plan, package):
    bees.ensure_attempts(env.conn)
    pair = bees.plan_worktree(env, plan)
    row = env.conn.execute('SELECT cwd FROM bee_worktrees WHERE project=? AND plan_path=? AND package=?',
                           (env.name, plan, package)).fetchone()
    return row[0] if row else (pair[1] if pair else None)


def current_commit(env, plan, package):
    from . import integration
    folder = checkout(env, plan, package)
    return integration.head_commit(folder) if folder else ''


def advance(env, free_mb, alive=None):
    """Start or harvest package reviews independently of a Queen's lifetime."""
    from . import agent_process, hives, loop, plans as plan_model, platform, scheduler
    conn = env.conn
    for entry in runtime.kv_list(conn, env.name, 'package-diagnosis:'):
        plan, package = json.loads(entry['key'].split(':', 1)[1])
        state = json.loads(entry['value'])
        plan_row = conn.execute('SELECT status FROM plans WHERE project=? AND path=?', (env.name, plan)).fetchone()
        if not plan_row or plan_row[0] not in plan_model.QUEEN_STATUSES:
            continue
        if not any(p['id'] == package for p in env.packages(plan)):
            continue
        if bees.package_done(conn, env.name, plan, package):
            completed(env, plan, package)
            continue
        if state.get('phase') == 'owner':
            request(env, plan, package, state['current_class'])
            state = _load(conn, env.name, plan, package)
        if state.get('phase') not in ('pending', 'running'):
            continue
        if hives.start_block_reason(conn, env.name, plan) or bees.package_stopped(conn, env.name, plan, package):
            continue
        head = current_commit(env, plan, package)
        if not head:
            continue
        offered = sorted({cls for p in bees.escalation_providers(env, plan, package)
                          if p['name'] not in env.settings.get('excludedProviders', [])
                          for cls, value in p.get('classes', {}).items() if value.get('model')})
        if state['phase'] == 'running':
            row = next((a for a in runtime.list_agents(conn, env.name, 'all') if a['name'] == state['agent']), None)
            if row and agent_process.counts(row, alive or platform.process_alive):
                continue
            try:
                if head != state['scope']['commit']:
                    raise ValueError('The package commit changed during diagnosis')
                report = accept(conn, env.name, plan, package,
                                env.run_dir(state['agent']) / 'package-diagnosis.json',
                                state['scope'], state['current_class'], offered)
                state = _load(conn, env.name, plan, package)
                state['phase'] = 'authorized' if report['kind'] == 'technical' else 'owner'
                if report['kind'] != 'technical':
                    text = '\n\n'.join((report['question'], report['why_owner'], report['analysis'], '\n'.join(report['evidence'])))
                    existing = runtime.list_questions(conn, env.name, status='open', plan=plan, item=package)
                    state['question'] = next((q['id'] for q in existing if q['question'] == text), None)
                    if state['question'] is None:
                        state['question'] = db.add_question(conn, env.name, plan, package, text, report['recommendation'])
                    bees.set_package_state(conn, env.name, plan, package, 'waiting', reason='question:%s' % state['question'])
            except (OSError, ValueError, KeyError) as exc:
                state.update(phase='pending', feedback=str(exc))
            runtime.kv_set(conn, env.name, entry['key'], json.dumps(state))
            continue
        if bees.drain_refusal() or free_mb / 1024 < loop._min_ram(env):
            continue
        running = [{**a, 'status': 'running'} for a in runtime.list_agents(conn, None, 'active')
                   if agent_process.counts(a, alive or platform.process_alive)]
        # Do not inspect a checkout while its repair process can still modify it.
        if any(a.get('project') == env.name and a.get('plan_path') == plan and a.get('package') == package for a in running):
            continue
        free = scheduler.provider_free(env.providers, running, conn)
        providers = [{**p, 'use': 'preferred'} for p in env.providers
                     if p['name'] not in env.settings.get('excludedProviders', [])
                     and 'Coding' in scheduler.provider_capabilities(p) and not scheduler.reserve_zero(p, conn)]
        cls = env.settings.get('diagnosisClass', 'medium')
        pick = scheduler.select_provider(providers, cls, [], free, conn=conn)
        folder = checkout(env, plan, package)
        if not pick or not folder:
            continue
        names = [a['name'] for a in runtime.list_agents(conn, env.name, 'all')]
        name = loop.agent_identity(conn)['Person']
        base, suffix = name, 1
        while name in names:
            suffix += 1
            name = '%s-%s' % (base, suffix)
        current = scope(conn, env.name, plan, package, head)
        text = prompt(conn, env.name, plan, package, head, env.run_dir(name) / 'package-diagnosis.json',
                      state['current_class'], offered)
        text += '\nPlan: %s\nPackage: %s\nOwner topics: %s\nReview feedback: %s\n' % (
            plan, package, json.dumps(env.settings.get('ownerTopics', [])), state.get('feedback', ''))
        start = {'names': names, 'busy': [], 'plan_status': {scheduler.norm_plan(plan): 'RUNNING'},
                 'by_path': {scheduler.norm_plan(plan): {'path': plan}}}
        ctx = {'env': env, 'state': start, 'pool': loop.Pool(free, 0), 'report': [], 'dry': False, 'settle': 0}
        action = {'name': name, 'type': pick['name'], 'phase': 'fix', 'plan': plan, 'package': '',
                  'cwd': str(folder), 'class': cls, 'prompt': text}
        if loop._do_start(ctx, action, forced_pick=pick):
            state.update(phase='running', agent=start['names'][-1], scope=current)
            runtime.kv_set(conn, env.name, entry['key'], json.dumps(state))

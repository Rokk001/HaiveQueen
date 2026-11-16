"""Evidence-bound package diagnosis and one-use retry authorization."""
import hashlib
import json
import math
import re
from pathlib import Path

from . import bees, db, repair_contract, repair_review, roles, runtime


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
            'decisions': _digest(known),
            'completion_generation': completion_generation(conn, project, plan, package)}


def _load(conn, project, plan, package):
    raw = runtime.kv_get(conn, project, _key(plan, package))
    return json.loads(raw) if raw else {}


def unchanged_retry(state, current):
    """A new attempt or new prose is not implementation progress."""
    consumed = state.get('consumed_scopes', [])
    if any(item.get('commit') == current['commit'] and item.get('decisions') == current['decisions'] for item in consumed):
        return True
    # Existing installations have accepted reports but no consumption ledger.
    # Treat these conservatively instead of resetting protection at upgrade.
    old = state.get('accepted_scope')
    if old is None:
        # scope belongs to the running review and is overwritten on every start.
        # The retained report, unlike that scope, identifies the accepted commit.
        report = state.get('report') or {}
        old = {'commit': report.get('commit'),
               'decisions': (state.get('scope') or {}).get('decisions')}
    return bool(state.get('accepted') and old.get('commit') == current['commit']
                and old.get('decisions') == current['decisions'])


def experiment_evidence(report, path, state, expected):
    """Require a fresh local observation, not a renamed report or attempt counter."""
    experiment = report.get('experiment')
    if not isinstance(experiment, dict):
        raise ValueError('unchanged package requires new experiment evidence before another retry')
    for key in ('question', 'command', 'finding', 'log'):
        if not isinstance(experiment.get(key), str) or not experiment[key].strip():
            raise ValueError('experiment requires question, command, finding and log')
    if type(experiment.get('exit')) is not int:
        raise ValueError('experiment requires its actual integer exit code')
    relative = Path(experiment['log'])
    root = Path(path).resolve().parent
    log = (root / relative).resolve()
    if relative.is_absolute() or not log.is_relative_to(root) or not log.is_file():
        raise ValueError('experiment log must exist inside this diagnosis run directory')
    if log.stat().st_size == 0 or log.stat().st_size > 4 * 1024 * 1024:
        raise ValueError('experiment log must be nonempty and at most 4 MiB')
    digest = hashlib.sha256(log.read_bytes()).hexdigest()
    identity = {k: expected[k] for k in ('commit', 'decisions')}
    identity['digest'] = digest
    used = identity in state.get('experiment_scopes', [])
    # Unscoped legacy hashes cannot prove use against a different commit.
    # Retain their protection only at the last accepted report's commit.
    old = state.get('accepted_scope') or {}
    old_commit = old.get('commit') or (state.get('report') or {}).get('commit')
    if ('experiment_scopes' not in state and old_commit == expected['commit']
            and old.get('decisions', expected['decisions']) == expected['decisions']):
        used = digest in state.get('experiment_digests', [])
    if used:
        raise ValueError('this experiment evidence already authorized a retry; collect a different observation')
    return identity


def continuation(conn, project, plan, package, state, report, selected):
    """A fresh observation is not a changed repair instruction."""
    previous = state.get('report') or {}
    if previous.get('kind') != 'technical' or report['kind'] != 'technical':
        return None
    follow = report.get('continuation')
    history = bees.attempt_history(conn, project, plan, package, limit=1)['attempts']
    if (not isinstance(follow, dict) or follow.get('previous') != _digest(previous)
            or not history or follow.get('attempt') != history[0]['attempt']):
        raise ValueError('continuation must review the last accepted diagnosis and latest repair attempt')
    if follow.get('outcome') not in ('no_progress', 'partial', 'step_complete'):
        raise ValueError('continuation must distinguish no_progress, partial and step_complete')
    if follow.get('outcome') == 'step_complete' and not state.get('progress', {}).get(follow['attempt']):
        raise ValueError('step_complete requires a verified repair receipt')
    for field in ('reason', 'target', 'change', 'verification'):
        if not isinstance(follow.get(field), str) or not follow[field].strip():
            raise ValueError('continuation requires reason, target, change and focused verification')
    normalize = lambda value: ' '.join(value.casefold().split())
    action = _digest([normalize(follow['target']), normalize(follow['change']), selected])
    # Keep this ledger across incidental commits and new experiment output.
    if action in state.get('repair_actions', []) or normalize(follow['change']) == normalize(previous['next_action']):
        raise ValueError('repair action already attempted; resolve its failed prerequisite or narrow the implementation step')
    return follow, action


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
        if signature in accepted and not (report['kind'] == 'technical' and report.get('experiment')):
            raise ValueError('this diagnosis has already authorized a retry')
        experiment = None
        latest = bees.attempt_history(conn, project, plan, package, limit=1)['attempts']
        progress = state.get('progress', {}).get(latest[0]['attempt']) if latest else None
        verified_progress = progress and progress.get('commit') == expected['commit']
        if report['kind'] == 'technical' and ((unchanged_retry(state, expected) and not verified_progress) or report.get('experiment')):
            experiment = experiment_evidence(report, path, state, expected)
        follow = continuation(conn, project, plan, package, state, report, selected)
        contract = repair_contract.validate(report, state.get('report')) if report['kind'] == 'technical' else None
        if experiment:
            signature = _digest([signature, experiment])
            if 'experiment_scopes' not in state:
                old = state.get('accepted_scope') or {}
                commit = old.get('commit') or (state.get('report') or {}).get('commit')
                state['experiment_scopes'] = [dict(commit=commit,
                    decisions=old.get('decisions', expected['decisions']), digest=digest)
                    for digest in state.get('experiment_digests', []) if commit]
            state['experiment_scopes'].append(experiment)
            state['experiment_log'] = str((Path(path).parent / report['experiment']['log']).resolve())
        instruction = '\n'.join(report[k] for k in ('analysis', 'next_action', 'why_new')) if report['kind'] == 'technical' else ''
        steps = None
        if contract:
            instruction = repair_contract.instruction(report)
            steps = ['Implement the authoritative repair contract without assuming unresolved prerequisites.',
                     'Verify every criterion in the authoritative repair contract.',
                     'Report the repair result separately from remaining package requirements.']
            instruction += '\n' + roles.role_text('repair-result')
        if experiment:
            instruction += '\nNew observation: ' + report['experiment']['finding'] + '\nEvidence: ' + state['experiment_log']
        if report['kind'] == 'technical':
            instruction += ('\nRepair the diagnosed boundary first, with a failing-before regression and '
                            'focused passing verification. Supporting fixture changes alone do not complete '
                            'this repair unless fixtures are the diagnosed cause. Report the exact remaining '
                            'boundary and evidence if only partial progress is possible; do not substitute '
                            'a broader package task for this scoped repair.')
        state.update(scope=expected, report=report, accepted=[*accepted, signature],
                     authorization={'class': selected, 'instruction': instruction, 'steps': steps,
                                    'action': follow[1] if follow else None}
                     if report['kind'] == 'technical' else None)
        if contract:
            state['authorization'].update(contract=contract, protocol=2)
        if report['kind'] == 'technical':
            state['accepted_scope'] = dict(expected)
        runtime.kv_set(conn, project, _key(plan, package), json.dumps(state))
    return report


def authorization(conn, project, plan, package, current):
    if scope(conn, project, plan, package, current['commit']) != current:
        return None
    state = _load(conn, project, plan, package)
    grant = state.get('authorization')
    return grant if state.get('scope') == current and grant and grant.get('protocol') == 2 else None


def consume(conn, project, plan, package, current, attempt=''):
    """Consume only after a successful start; no attempt counters are reset."""
    with db.transaction(conn):
        if scope(conn, project, plan, package, current['commit']) != current:
            return False
        state = _load(conn, project, plan, package)
        if state.get('scope') != current or (state.get('authorization') or {}).get('protocol') != 2:
            return False
        scopes = state.setdefault('consumed_scopes', [])
        basis = {k: current[k] for k in ('commit', 'decisions')}
        if basis not in scopes:
            scopes.append(basis)
        action = state['authorization'].get('action')
        if action:
            state.setdefault('repair_actions', []).append(action)
        contract = state['authorization'].get('contract')
        if contract:
            state['assigned'] = {'attempt': attempt, 'id': repair_contract.identity(contract),
                                 'contract': contract}
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
    sources += '\n' + roles.role_text('repair-contract') + '\n'
    previous = _load(conn, project, plan, package).get('report', {})
    if previous:
        sources += '\n' + roles.role_text('package-continuation', {'previous': json.dumps(_digest(previous))}) + '\n'
        sources += '\nPrevious diagnosis preview: ' + json.dumps({k: str(previous[k])[:300]
            for k in ('analysis', 'next_action', 'why_new') if k in previous})
        sources += '\nFull previous diagnosis: bzz kv get --project %s --key %s\n' % (
            json.dumps(project), json.dumps(_key(plan, package)))
        sources += ('Continue the unresolved repair objective from that diagnosis. Compare the latest '
                    'repair result with its requested verification; incidental commits or fixture fixes '
                    'do not prove that objective complete. Reuse applicable findings and narrow the next '
                    'implementation step; repeat diagnosis only to resolve a concrete evidence gap.\n')
    state = _load(conn, project, plan, package)
    latest = history.get('attempts', [])
    progress = state.get('progress', {}).get(latest[0]['attempt']) if latest else None
    if progress:
        sources += ('\nThe latest assigned repair step passed every recorded criterion. The package is still open. '
                    'Do not treat remaining package requirements as failure of that step. Reuse its receipt at '
                    + progress['result_path'] + '; select the next unresolved prerequisite, preserving the verified work.\n')
    if unchanged_retry(state, current) and not progress:
        sources += ('\nUNCHANGED CODE AFTER AN AUTHORIZED REPAIR: another history entry, renamed agent, '
                    'paraphrased approach or higher model class is not new evidence. Do not authorize '
                    'the same broad task again. Investigate why the previous repair produced no verified '
                    'progress and perform one narrow, safe experiment (focused reproduction, coordination '
                    'probe or execution-environment check). Preserve project files and running processes. '
                    'Record its actual output in this diagnosis run directory. For a technical retry, add '
                    'experiment: {question, command, exit: integer, finding, log: relative filename} to '
                    'the JSON. Explain in why_new how this observation changes the next bounded repair '
                    'step and name its focused verification. Do not fabricate an experiment or just copy '
                    'a previous report. If blocked by an actual external prerequisite, identify that '
                    'prerequisite; exhausted attempts alone remain a technical problem.\n')
    return roles.role_text('diagnosis', {
        'output_path': output_path, 'commit': commit, 'failure_log': current['failure_log'],
        'decision_ids': json.dumps([d['id'] for d in known]),
        'decisions': json.dumps([{'id': d['id'], 'text': str(d.get('text', ''))[:200]} for d in known[-8:]]),
        'history': json.dumps(history) + sources,
    }) + '\nCurrent repair class: %s\nAvailable repair classes: %s\n' % (current_class, json.dumps(offered))

def completion_key(plan, package):
    return 'package-completion-generation:' + json.dumps([plan, package], separators=(',', ':'))


def completion_generation(conn, project, plan, package):
    return int(runtime.kv_get(conn, project, completion_key(plan, package)) or 0)


def request(env, plan, package, current_class):
    with db.transaction(env.conn):
        _request(env, plan, package, current_class)


def _request(env, plan, package, current_class):
    """Queue a scoped diagnosis without asking the owner or resetting attempts."""
    state = _load(env.conn, env.name, plan, package)
    if state.get('phase') in ('pending', 'running'):
        return
    if state.get('phase') == 'owner' and state.get('question'):
        question = env.conn.execute('SELECT status FROM questions WHERE id=? AND project=? AND plan_path=? AND item=?',
                                    (state['question'], env.name, plan, package)).fetchone()
        if question and question[0] == 'open':
            return
    state.update(phase='pending', current_class=current_class, authorization=None,
                 completion_generation=completion_generation(env.conn, env.name, plan, package))
    runtime.kv_set(env.conn, env.name, _key(plan, package), json.dumps(state))


def completed(env, plan, package):
    """Only a success recorded after this diagnosis request can supersede it."""
    with db.transaction(env.conn):
        state = _load(env.conn, env.name, plan, package)
        if not state or state.get('phase') == 'completed':
            return False
        generation = completion_generation(env.conn, env.name, plan, package)
        if 'completion_generation' not in state:
            # Legacy reviews have no ordering evidence: retain them, never infer
            # that a historical success supersedes an explicitly queued review.
            state['completion_generation'] = generation
            runtime.kv_set(env.conn, env.name, _key(plan, package), json.dumps(state))
            return False
        if generation <= state['completion_generation']:
            return False
        state.update(phase='completed', authorization=None)
        runtime.kv_set(env.conn, env.name, _key(plan, package), json.dumps(state))
        return True


def reviews(env, plan):
    return {p['id']: state for p in env.packages(plan)
            if (state := _load(env.conn, env.name, plan, p['id']))
            and state.get('phase') != 'completed'}


def running_packages(conn, project, plan, packages, alive=None):
    """Read confirmed running diagnosis agents; never advance or repair state."""
    from . import agent_process
    result = []
    for package in sorted(packages):
        try:
            state = _load(conn, project, plan, package)
        except (ValueError, TypeError):
            continue
        if not isinstance(state, dict) or state.get('phase') != 'running' or not state.get('agent'):
            continue
        agent = runtime.get_agent(conn, project, state['agent'])
        if agent and agent.get('plan_path') == plan and agent_process.is_live(agent, alive):
            result.append(package)
    return result


def waiting(env, plan):
    """Do not churn Queens while all remaining work awaits diagnosis or its answer."""
    active = reviews(env, plan)
    blocked = {key for key, state in active.items() if state.get('phase') in ('pending', 'running', 'owner')}
    if not blocked:
        return False
    pending = [p for p in env.packages(plan) if p['id'] in blocked or not bees.package_done(env.conn, env.name, plan, p['id'])]
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


def resolve_memory_wait(env, plan, package, free_mb):
    """Retire a legacy RAM-only question once its observable prerequisite recovers.

    Do not infer intent from arbitrary questions mentioning memory. Only the
    old diagnostic wait template is recognized, within this package and plan.
    This is a resource observation, not a new owner decision or retry grant.
    """
    from . import loop
    if not math.isfinite(free_mb) or free_mb / 1024 < loop._min_ram(env):
        return
    pattern = (r'The ' + re.escape(package) + r' retry diagnosis has remained pending for several minutes '
               r'while free physical memory is below the configured ([0-9]+(?:\.[0-9]+)?) GiB start threshold\. '
               r'Can you free enough memory for the diagnosis and subsequent agent to start\?')
    for question in runtime.list_questions(env.conn, env.name, status='open', plan=plan, item=package + '-memory'):
        match = re.fullmatch(pattern, question['question'])
        if not match or free_mb / 1024 < float(match[1]):
            continue
        text = 'The observed free memory now meets the start threshold. Normal scheduling and retry guards remain in force.'
        with db.transaction(env.conn):
            changed = env.conn.execute(
                "UPDATE questions SET status='answered', answer=?, answered_by='auto', answered_at=? WHERE id=? AND status='open'",
                (text, db.now(), question['id'])).rowcount
            if changed:
                db.add_event(env.conn, env.name, 'answer', text,
                             data={'question_id': question['id'], 'by': 'auto', 'reason': 'memory_recovered'})


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
        if state.get('phase') in ('pending', 'running', 'authorized'):
            resolve_memory_wait(env, plan, package, free_mb)
        if completed(env, plan, package):
            continue
        state = _load(conn, env.name, plan, package)
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
                state.pop('review_rejection', None)
                state['phase'] = 'authorized' if report['kind'] == 'technical' else 'owner'
                if report['kind'] != 'technical':
                    text = '\n\n'.join((report['question'], report['why_owner'], report['analysis'], '\n'.join(report['evidence'])))
                    existing = runtime.list_questions(conn, env.name, status='open', plan=plan, item=package)
                    state['question'] = next((q['id'] for q in existing if q['question'] == text), None)
                    if state['question'] is None:
                        state['question'] = db.add_question(conn, env.name, plan, package, text, report['recommendation'])
                    bees.set_package_state(conn, env.name, plan, package, 'waiting', reason='question:%s' % state['question'])
            except (OSError, ValueError, KeyError) as exc:
                current = scope(conn, env.name, plan, package, head)
                message = repair_review.reject(state, current, str(exc), state.get('agent', ''))
                state.update(phase='pending', feedback=str(exc))
                if message:
                    qid = db.add_question(conn, env.name, plan, package, message,
                                          'Restore valid diagnosis output without clearing attempt history.')
                    state.update(phase='owner', question=qid)
                    bees.set_package_state(conn, env.name, plan, package, 'waiting', reason='question:%s' % qid)
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

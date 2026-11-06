"""Bee routing, package attempts and scoped Queen instructions."""
import json
import re

from . import buildjobs, db, roles, runtime, scheduler
from .i18n import tr


# Return the next available configured class above the current one.
def next_class(providers, current, excluded):
    """Return the next class offered by a non-excluded provider, or None."""
    for name in scheduler.CLASS_NAMES[scheduler.class_index(current) + 1:]:
        if any(p['name'] not in excluded and (p.get('classes', {}).get(name) or {}).get('model') for p in providers):
            return name
    return None


# Pick a permitted free provider and class for a Bee.
# List the providers a Bee of this package may run on.
def route_candidates(providers, hive_provider, capability, escalated):
    """A package that needs capabilities only runs on providers that offer all of them, an escalated package may use any provider, else only the Queen's provider and endpoint providers."""
    if capability:
        return capable_providers(providers, capability)
    providers = [p for p in providers if "Coding" in scheduler.provider_capabilities(p)]
    if escalated:
        return list(providers)
    candidates = [p for p in providers if p.get('endpoint') or p['name'] == hive_provider]
    candidates.sort(key=lambda p: not bool(p.get('endpoint')))
    return candidates


def capable_providers(providers, need) -> list:
    """The providers that offer every capability in `need`."""
    return [p for p in providers if ('Confidential data' not in [scheduler.canonical_capability(n) for n in need] or scheduler._local_confidential(p)) and all(scheduler.canonical_capability(n) in [scheduler.canonical_capability(c) for c in p.get('capabilities', [])] for n in need)]


def select_route(providers, free, hive_provider, package_class, capability, excluded, escalated, limited=()):
    """Pick a permitted Bee provider and class, returning None when it must wait."""
    providers = [p for p in providers if p['name'] not in excluded]
    candidates = route_candidates(providers, hive_provider, capability, escalated)
    index = scheduler.class_index(package_class)
    for cls in scheduler.CLASS_NAMES[index:]:
        matching = [p for p in candidates if (p.get('classes', {}).get(cls) or {}).get('model')]
        if not matching:
            continue
        for p in matching:
            if free.get(p['name'], 0) > 0 and p['name'] not in limited:
                return p['name'], cls
        return None
    return None


# Create persistent package completion and per-class attempt tables.
def ensure_attempts(conn):
    """Create persistent completion and failure records if absent."""
    conn.execute('CREATE TABLE IF NOT EXISTS bee_attempts (project TEXT NOT NULL, plan_path TEXT NOT NULL, package TEXT NOT NULL, class TEXT NOT NULL, failures INTEGER NOT NULL DEFAULT 0, PRIMARY KEY(project,plan_path,package,class))')
    conn.execute('CREATE TABLE IF NOT EXISTS bee_attempt_reports (project TEXT NOT NULL, plan_path TEXT NOT NULL, package TEXT NOT NULL, attempt TEXT NOT NULL, PRIMARY KEY(project,plan_path,package,attempt))')
    conn.execute('CREATE TABLE IF NOT EXISTS bee_attempt_signals (project TEXT NOT NULL, plan_path TEXT NOT NULL, package TEXT NOT NULL, class TEXT NOT NULL, attempt TEXT NOT NULL, open_requirements TEXT NOT NULL, error TEXT NOT NULL, counted INTEGER NOT NULL, PRIMARY KEY(project,plan_path,package,attempt))')
    conn.execute('CREATE TABLE IF NOT EXISTS bee_assignments (project TEXT NOT NULL, plan_path TEXT NOT NULL, package TEXT NOT NULL, class TEXT NOT NULL, provider TEXT NOT NULL, PRIMARY KEY(project,plan_path,package))')
    conn.execute('CREATE TABLE IF NOT EXISTS bee_worktrees (project TEXT NOT NULL, plan_path TEXT NOT NULL, package TEXT NOT NULL, cwd TEXT NOT NULL, PRIMARY KEY(project,plan_path,package))')
    conn.execute('CREATE TABLE IF NOT EXISTS bee_completed (project TEXT NOT NULL, plan_path TEXT NOT NULL, package TEXT NOT NULL, PRIMARY KEY(project,plan_path,package))')
    conn.execute('CREATE TABLE IF NOT EXISTS package_progress (project TEXT NOT NULL, plan_path TEXT NOT NULL, package TEXT NOT NULL, state TEXT NOT NULL, agent TEXT NOT NULL, updated_at TEXT NOT NULL, PRIMARY KEY(project,plan_path,package))')
    if 'reason' not in [r[1] for r in conn.execute('PRAGMA table_info(package_progress)')]:
        conn.execute("ALTER TABLE package_progress ADD COLUMN reason TEXT NOT NULL DEFAULT ''")
    conn.execute("CREATE TABLE IF NOT EXISTS embedded_bees (project TEXT NOT NULL, plan_path TEXT NOT NULL, package TEXT NOT NULL, name TEXT NOT NULL, class TEXT NOT NULL, provider TEXT NOT NULL, model TEXT NOT NULL DEFAULT '', effort TEXT NOT NULL DEFAULT '', started_at TEXT NOT NULL, ended_at TEXT, outcome TEXT NOT NULL DEFAULT '')")
    conn.execute('CREATE TABLE IF NOT EXISTS bee_step_counts (project TEXT NOT NULL, plan_path TEXT NOT NULL, package TEXT NOT NULL, steps INTEGER NOT NULL, PRIMARY KEY(project,plan_path,package))')


# Remember how many numbered steps the Bee of a package got.
def record_step_count(conn, project, plan, package, steps):
    """Store the number of steps of the newest Bee of the package: the window shows 0/steps from its start, until it reports a step."""
    ensure_attempts(conn)
    conn.execute('INSERT INTO bee_step_counts(project,plan_path,package,steps) VALUES (?,?,?,?) ON CONFLICT(project,plan_path,package) DO UPDATE SET steps=excluded.steps', (project, plan, package, int(steps)))


# Remember a Bee that runs as an embedded subagent of the Queen.
def record_embedded_start(conn, project, plan, package, name, cls, provider, model='', effort=''):
    """An embedded Bee has no agent row of its own: store it so the window can show it under its Queen. An older open row of the package is closed first."""
    ensure_attempts(conn)
    close_embedded(conn, project, plan, package, 'replaced')
    conn.execute('INSERT INTO embedded_bees(project,plan_path,package,name,class,provider,model,effort,started_at) VALUES (?,?,?,?,?,?,?,?,?)',
                 (project, plan, package, name, cls, provider, model, effort, db.now()))


# Names that open embedded Bees hold.
def open_embedded_names(conn, skip=None) -> set:
    """The names of the embedded Bees that have not ended; `skip` is a (project, plan, package) whose Bee does not count because it is being replaced."""
    if not conn.execute("SELECT 1 FROM sqlite_master WHERE type='table' AND name='embedded_bees'").fetchone():
        return set()
    rows = conn.execute('SELECT name,project,plan_path,package FROM embedded_bees WHERE ended_at IS NULL').fetchall()
    return {r[0] for r in rows if (r[1], r[2], r[3]) != tuple(skip or ())}


# Mark embedded Bees as ended.
def close_embedded(conn, project, plan, package=None, outcome='done'):
    """Close the open embedded Bees of a package, or of the whole plan when no package is given, with the outcome that ended them."""
    ensure_attempts(conn)
    sql, args = 'UPDATE embedded_bees SET ended_at=?, outcome=? WHERE project=? AND plan_path=? AND ended_at IS NULL', [db.now(), outcome, project, plan]
    if package:
        sql += ' AND package=?'
        args.append(package)
    conn.execute(sql, args)


# Count a failed attempt, ignoring technical interruptions.
def record_attempt(conn, project, plan, package, cls, outcome, attempt=None, open_requirements=None, error='', verify='code'):
    """Record an attempt once. A failed attempt counts only when the same error came again or, for a package closed by code, the same requirement stayed open. A package in state implemented never counts; for a package closed by build or owner open requirements alone never count."""
    ensure_attempts(conn)
    if outcome == 'succeeded':
        conn.execute('INSERT OR IGNORE INTO bee_completed VALUES(?,?,?)', (project, plan, package))
        set_package_state(conn, project, plan, package, 'done')
    if outcome == 'interrupted':
        return
    with db.transaction(conn):
        if attempt and conn.execute('INSERT OR IGNORE INTO bee_attempt_reports(project,plan_path,package,attempt) VALUES(?,?,?,?)', (project, plan, package, attempt)).rowcount == 0:
            return
        if attempt:
            previous = conn.execute('SELECT open_requirements,error FROM bee_attempt_signals WHERE project=? AND plan_path=? AND package=? AND class=? ORDER BY rowid DESC LIMIT 1', (project, plan, package, cls)).fetchone()
            state = conn.execute('SELECT state FROM package_progress WHERE project=? AND plan_path=? AND package=?', (project, plan, package)).fetchone()
            same_requirement = verify == 'code' and previous and set(open_requirements or []) & set(json.loads(previous[0]))
            same_error = previous and error and error == previous[1]
            counted = bool(outcome == 'failed' and not (state and state[0] == 'implemented') and (same_requirement or same_error))
            conn.execute('INSERT INTO bee_attempt_signals VALUES(?,?,?,?,?,?,?,?)', (project, plan, package, cls, attempt, json.dumps(open_requirements or []), error, int(counted)))
        if attempt and counted:
            conn.execute('INSERT INTO bee_attempts(project,plan_path,package,class,failures) VALUES(?,?,?,?,1) ON CONFLICT(project,plan_path,package,class) DO UPDATE SET failures=failures+1', (project, plan, package, cls))


# Remember how far a package of a Hive is.
def set_package_state(conn, project, plan, package, state, agent='', reason=''):
    """Store the progress state of one package (working, waiting, implemented, done or rejected; no row means open). `reason` says what a waiting package waits for (question:<id>, build, owner, package or provider). The Hive progress shown in the window counts the done ones, a rejected package is not done."""
    if state not in PACKAGE_STATES:
        raise ValueError('package state must be one of: ' + ', '.join(PACKAGE_STATES))
    ensure_attempts(conn)
    conn.execute('INSERT INTO package_progress(project,plan_path,package,state,agent,updated_at,reason) VALUES(?,?,?,?,?,?,?) ON CONFLICT(project,plan_path,package) DO UPDATE SET state=excluded.state,agent=excluded.agent,updated_at=excluded.updated_at,reason=excluded.reason', (project, plan, package, state, agent, db.now(), reason if state == 'waiting' else ''))


# The packages of a plan the owner did not accept.
def rejected_packages(conn, project, plan):
    """Ids of the packages of the plan in state rejected, sorted; empty when none (or the progress table does not exist yet)."""
    ensure_attempts(conn)
    return sorted(r[0] for r in conn.execute("SELECT package FROM package_progress WHERE project=? AND plan_path=? AND state='rejected'", (project, plan)))


# Check whether any successful attempt completed a package.
def package_done(conn, project, plan, package):
    """Return whether the package has a persisted successful attempt."""
    ensure_attempts(conn)
    return conn.execute('SELECT 1 FROM bee_completed WHERE project=? AND plan_path=? AND package=?', (project, plan, package)).fetchone() is not None


# Refuse a start when a declared dependency is missing or unfinished.
def check_dependencies(env, plan, package):
    """Raise the logged reason for the first unsatisfied package dependency."""
    packages = env.packages(plan)
    ids = {p['id'] for p in packages}
    row = next((p for p in packages if p['id'] == package), None)
    if not row:
        return
    for dep in row['depends']:
        reason = ('not in plan' if dep not in ids else
                  'not done' if not package_done(env.conn, env.name, plan, dep) else None)
        if reason:
            message = 'dependency %s of package %s %s' % (dep, package, reason)
            env.log(message)
            raise ValueError(message)


# Read failures for one package and class.
def failure_count(conn, project, plan, package, cls):
    """Read the persisted failure count for a package and class."""
    ensure_attempts(conn)
    row = conn.execute('SELECT failures FROM bee_attempts WHERE project=? AND plan_path=? AND package=? AND class=?', (project, plan, package, cls)).fetchone()
    return row[0] if row else 0


# Determine the current or next escalation class.
def next_attempt(conn, project, plan, package, cls, providers, excluded, threshold):
    """Return the current class, its next offered class, or None at the top."""
    if failure_count(conn, project, plan, package, cls) < threshold:
        return cls
    return next_class(providers, cls, excluded)


# Write only the package task and relevant paths to a Bee; the complete project rules are loaded by the runner, a `rules` argument never replaces them.
def bee_prompt(name, package, task, steps, paths, rules, build_rules=''):
    """Build a slim Bee task with numbered progress reports."""
    return roles.role_text('bee', {'package': package, 'task': task, 'steps': '\n'.join('%d. %s' % (i + 1, s) for i, s in enumerate(steps)), 'paths': ', '.join(paths), 'rules': '\n'.join(rules), 'name': name, 'step_count': len(steps), 'build_rules': build_rules}).rstrip()


PACKAGE_STATES = ('working', 'waiting', 'implemented', 'done', 'rejected')   # progress states of a package; a package without a row is open; waiting = needs an answer, a build or the owner (the reason says which); rejected = the owner did not accept it


# Describe the plan's Queen and Bee workflow with configured limits.
def queen_instructions(queen_class, bee_limit, threshold, context_limit, language=None):
    """Return Queen task instructions for the configured classes and limits."""
    return roles.role_text('queen', {'class': queen_class, 'bee_limit': bee_limit, 'escalate_after': threshold, 'context_limit': context_limit, 'language': language or roles.language({})})


# Packages of a plan that can still start although one provider is missing.
def runnable_packages(env, plan, provider) -> list:
    """Ids of the packages that are not finished or waiting, whose dependencies are done and that do not need a capability only the missing provider has (a capability another provider offers too does not count): the Queen must work them before the Hive waits."""
    from . import routine
    ensure_attempts(env.conn)
    states = {r[0]: r[1] for r in env.conn.execute('SELECT package,state FROM package_progress WHERE project=? AND plan_path=?', (env.name, plan))}
    others = [p for p in env.providers if p['name'] != provider['name']]
    packages = env.packages(plan)
    ids = {p['id'] for p in packages}
    found = []
    for p in packages:
        if states.get(p['id']) in ('done', 'implemented', 'waiting') or package_done(env.conn, env.name, plan, p['id']):
            continue
        need = routine.package_needs_for(env, plan, p['id'])
        if need and not capable_providers(others, need):
            continue   # only the missing provider can work it
        if all(dep in ids and package_done(env.conn, env.name, plan, dep) for dep in p['depends']):
            found.append(p['id'])
    return found


# Set the Hive to wait for a particular Bee provider.
def wait_for_provider(conn, project, plan, provider, is_codex=None, reset_at=None):
    """Persist the provider a Hive is waiting for."""
    ensure_attempts(conn)
    conn.execute('CREATE TABLE IF NOT EXISTS bee_provider_wait (project TEXT NOT NULL, plan_path TEXT NOT NULL, provider TEXT NOT NULL, PRIMARY KEY(project,plan_path))')
    with db.transaction(conn):
        conn.execute('INSERT INTO bee_provider_wait VALUES (?,?,?) ON CONFLICT(project,plan_path) DO UPDATE SET provider=excluded.provider', (project, plan, provider))
        conn.execute('UPDATE hives SET state=?,reset_at=? WHERE project=? AND plan_path=?', ('codex_wait' if (provider == 'codex' if is_codex is None else is_codex) else 'provider_wait', reset_at, project, plan))
        conn.execute('DELETE FROM hive_integration WHERE project=? AND plan_path=?', (project, plan))


# Wake a waiting Hive only when its provider has a slot and no limit.
def provider_available(conn, project, plan, provider, free, limited):
    """Wake the Hive when its waiting provider is usable."""
    if free <= 0 or limited:
        return False
    row = conn.execute('SELECT provider FROM bee_provider_wait WHERE project=? AND plan_path=?', (project, plan)).fetchone()
    if not row or row[0] != provider:
        return False
    with db.transaction(conn):
        conn.execute('DELETE FROM bee_provider_wait WHERE project=? AND plan_path=?', (project, plan))
        conn.execute("UPDATE hives SET state='waiting',reset_at=NULL WHERE project=? AND plan_path=?", (project, plan))
    return True


# Restrict the escalation of a package that needs capabilities to the providers that offer them.
def escalation_providers(env, plan, package):
    """Return the providers eligible to supply the next package class."""
    from . import routine
    need = routine.package_needs_for(env, plan, package)
    return capable_providers(env.providers, need) if need else env.providers


# Record an attempt and file one owner question when the top class is exhausted.
def report_attempt(env, plan, package, cls, outcome, attempt=None, open_requirements=None, error='', blocked=''):
    """Record an outcome and ask once after the highest class fails repeatedly. `blocked` (question:<id>, build, owner, package or provider) means the attempt only waits for that: nothing is counted and the package is set to waiting."""
    close_embedded(env.conn, env.name, plan, package, 'blocked' if blocked else outcome)   # the embedded Bee of this package has reported
    if blocked:
        set_package_state(env.conn, env.name, plan, package, 'waiting', 'queen', blocked)
        return
    row = next((p for p in env.packages(plan) if p['id'] == package), None)
    record_attempt(env.conn, env.name, plan, package, cls, outcome, attempt, open_requirements, error, row['verify'] if row else 'code')
    if outcome == 'succeeded':
        env.conn.execute('DELETE FROM bee_assignments WHERE project=? AND plan_path=? AND package=?', (env.name, plan, package))
    threshold = int(env.settings.get('escalateAfter', 3))
    excluded = env.settings.get('excludedProviders', [])
    if outcome == 'failed' and next_attempt(env.conn, env.name, plan, package, cls, escalation_providers(env, plan, package), excluded, threshold) is None:
        language = roles.language(env.settings)
        text = tr('The package remains unsolved at the highest available class. How should it continue?', language=language)
        if not any(q.get('question') == text for q in runtime.list_questions(env.conn, env.name, plan=plan, item=package)):
            db.add_question(env.conn, env.name, plan, package, text, tr('Review the failed attempts and choose the next approach.', language=language))


# Verify the Bee has a distinct worktree in the same repository.
def valid_bee_worktree(env, path, queen_cwd):
    """Check Git ownership and reject the Queen's checkout and the main checkout."""
    import subprocess
    from pathlib import Path
    path = Path(path)
    if not path.is_dir() or path.resolve() == env.code_repo.resolve() or (queen_cwd and path.resolve() == Path(queen_cwd).resolve()):
        return False
    def common(directory):
        """Return the canonical Git common directory of a checkout."""
        result = subprocess.run(['git', '-C', str(directory), 'rev-parse', '--path-format=absolute', '--git-common-dir'], capture_output=True, text=True, encoding='utf-8', errors='replace')
        return Path(result.stdout.strip()).resolve() if result.returncode == 0 else None
    repo_common = common(env.code_repo)
    if repo_common is None or common(path) != repo_common:
        return False
    result = subprocess.run(['git', '-C', str(path), 'symbolic-ref', '--quiet', '--short', 'HEAD'], capture_output=True, text=True, encoding='utf-8', errors='replace')
    return result.returncode == 0 and bool(result.stdout.strip())


# Check that a Queen works in a separate worktree of the project repository.
def valid_queen_worktree(env, path):
    """Reject the main checkout and directories outside the repository."""
    return valid_bee_worktree(env, path, None)


LAST_REFUSAL = ''   # why the last start_process call refused to start a Bee; the bee command hands it to the Queen


# Refuse a Bee start and remember why.
def refuse(env, package, reason):
    """Log and store the reason a Bee start was refused and return False, so a caller can write `return refuse(...)`."""
    global LAST_REFUSAL
    LAST_REFUSAL = reason
    env.log('Bee start refused for package %s: %s' % (package, reason))
    return False


# The reason no Bee may start while an update waits for running agents.
def drain_refusal(draining=None):
    """H-46(i): while the update worker waits for running agents no Bee starts, an embedded one included; the reason, else None."""
    from . import updates
    if (draining or updates.draining)():
        return 'an update of HaiveQueen waits for the running agents to end; no Bee starts until it has restarted'
    return None


# Start a separate Bee process after validating context, memory, slots and routing.
def start_process(env, plan, package, name, cls, task, steps, paths, rules, cwd=None, no_subagents=False):
    """Start an eligible Bee through the agent runner. Every package goes to a Bee, the Queen never works one herself; the provider setting beeMode decides whether
    the Bee of the Queen's own provider is an embedded subagent or a separate process.
    A refusal returns False and leaves its reason in LAST_REFUSAL."""
    global LAST_REFUSAL
    LAST_REFUSAL = ''
    from pathlib import Path
    from . import hives, loop, routine, usage
    with loop.hive_schedule_lock():
        check_dependencies(env, plan, package)
        draining = drain_refusal()
        if draining:
            return refuse(env, package, draining)
        hive = hives.get(env.conn, env.name, plan)
        if not hive or not hives.can_start_bee(env.conn, env.name, plan, False, env.settings.get('contextLimitTokens', 300000)):
            return refuse(env, package, 'the Hive may not start a Bee now (Hive state %s, context limit or HANDOFF reached)' % (hive['state'] if hive else 'missing'))
        active = runtime.list_agents(env.conn, None, 'active')
        queen = next((a for a in active if a.get('project') == env.name and a.get('plan_path') == plan and a.get('phase') == 'queen' and a.get('status') in ('running', 'starting')), None)
        if not queen:
            return refuse(env, package, 'no running Queen of this plan')
        held = {str(a.get('person') or a.get('name')) for a in active if a.get('status') in ('running', 'starting')} | open_embedded_names(env.conn, (env.name, plan, package))
        if re.sub(r'-\d+$', '', str(name)) in held:   # a name is held by one Queen or Bee at a time
            return refuse(env, package, 'the name %s is already held by a running Queen or Bee; use the free name %s' % (name, loop.agent_identity(env.conn)['Person']))
        from . import platform as osplat
        from . import agent_process
        running = [{**a, 'status': 'running'} for a in active if agent_process.counts(a)]
        if sum(1 for a in running if a.get('project') == env.name and a.get('plan_path') == plan and a.get('phase') == 'bee') >= int(env.settings.get('beesPerQueen', 3)):
            return refuse(env, package, 'the Queen already has %s Bees running' % env.settings.get('beesPerQueen', 3))
        plan_row = next((p for p in runtime.list_plans(env.conn, env.name) if p['path'] == plan), None)
        if plan_row and plan_row['status'] == 'SUBMITTED':
            return refuse(env, package, 'the plan is not cut yet: write its packages into the header and run bzz plan check first')
        package_row = next((p for p in env.packages(plan) if p['id'] == package), None)
        if not package_row:
            return refuse(env, package, 'the package is not in the plan')
        requested_class = package_row['cls']
        threshold = int(env.settings.get('escalateAfter', 3))
        excluded = env.settings.get('excludedProviders', [])
        need = routine.package_needs_for(env, plan, package)
        allowed = [p for p in env.providers if p['name'] not in excluded and
                   (p in capable_providers(env.providers, need) if need else
                    (p.get('endpoint') or p['name'] == hive['provider']))]
        base_class = next((name for name in scheduler.CLASS_NAMES[scheduler.class_index(requested_class):]
                           if any((p.get('classes', {}).get(name) or {}).get('model') for p in allowed)), None)
        use_other_provider = False
        if base_class is None and not need:
            others = [p for p in env.providers if p['name'] not in excluded]
            base_class = next((name for name in scheduler.CLASS_NAMES[scheduler.class_index(requested_class):]
                               if any((p.get('classes', {}).get(name) or {}).get('model') for p in others)), None)
            use_other_provider = base_class is not None
        if base_class is None:
            language = roles.language(env.settings)
            text = (tr('No configured provider offers the capability %s. How should this package continue?', ', '.join(need), language=language) if need
                    else tr('No configured provider offers the required class or a higher class. How should this package continue?', language=language))
            if not any(q.get('question') == text for q in runtime.list_questions(env.conn, env.name, plan=plan, item=package)):
                db.add_question(env.conn, env.name, plan, package, text, tr('Configure an eligible provider.', language=language))
            return refuse(env, package, 'no configured provider offers the required class or the capability %s (question filed)' % ', '.join(need) if need else 'no configured provider offers the required class (question filed)')
        current = base_class
        queen_class = env.settings.get('queenClass', 'medium')
        if (scheduler.class_index(requested_class) < scheduler.class_index(queen_class) and
                failure_count(env.conn, env.name, plan, package, queen_class) >= threshold):
            current = next_class(escalation_providers(env, plan, package), queen_class, excluded)
            if current is None:
                return refuse(env, package, 'the package failed %d times at the Queen class %s and no higher class is offered' % (threshold, queen_class))
        while failure_count(env.conn, env.name, plan, package, current) >= threshold:
            current = next_class(escalation_providers(env, plan, package), current, excluded)
            if current is None:
                return refuse(env, package, 'the package failed %d times and no higher class is offered' % threshold)
        ensure_attempts(env.conn)
        stored = env.conn.execute('SELECT class FROM bee_assignments WHERE project=? AND plan_path=? AND package=?', (env.name, plan, package)).fetchone()
        if stored and stored[0] == cls and scheduler.class_index(cls) > scheduler.class_index(current):
            current = cls   # the package was already escalated to this higher class: keep it even when the failure counters were reset
        if scheduler.class_index(cls) != scheduler.class_index(current) and not (current == base_class and scheduler.class_index(cls) == scheduler.class_index(requested_class)):
            return refuse(env, package, 'class %s was requested but the system computes class %s (plan class %s, no stored escalation)' % (cls, current, requested_class))
        queen_provider = scheduler.get_provider(env.providers, hive['provider'])
        embedded = bool(queen_provider and queen_provider.get('beeMode') == 'subagent' and not queen_provider.get('endpoint') and not no_subagents)   # the Queen's provider runs her Bees as embedded subagents
        free = scheduler.provider_free(env.providers, running)
        route_free = dict(free)
        if embedded:
            route_free[hive['provider']] = max(1, route_free.get(hive['provider'], 0))
        limited = [p['name'] for p in env.providers if any(w['used_pct'] >= 100 for w in usage.limit_windows(env.conn, p))]
        ensure_attempts(env.conn)
        assigned = env.conn.execute('SELECT class,provider FROM bee_assignments WHERE project=? AND plan_path=? AND package=?', (env.name, plan, package)).fetchone()
        if assigned and assigned[0] == current and assigned[1] not in {p['name'] for p in route_candidates(env.providers, hive['provider'], need, current != base_class or use_other_provider)}:
            assigned = None   # the stored provider is no longer allowed for this package: route it again
        if assigned and assigned[0] == current:
            provider = scheduler.get_provider(env.providers, assigned[1])
            if not provider or provider['name'] in excluded or provider['name'] in limited or route_free.get(provider['name'], 0) <= 0:
                return refuse(env, package, 'the assigned provider %s is unknown, excluded, at its limit or has no free slot' % assigned[1])
            route = (provider['name'], current)
        else:
            route = select_route(env.providers, route_free, hive['provider'], current, need, excluded, current != base_class or use_other_provider, limited)
        if not route:
            return refuse(env, package, 'no provider with a free slot offers class %s' % current)
        provider, selected_class = route
        env.conn.execute('INSERT INTO bee_assignments(project,plan_path,package,class,provider) VALUES(?,?,?,?,?) ON CONFLICT(project,plan_path,package) DO UPDATE SET class=excluded.class,provider=excluded.provider', (env.name, plan, package, selected_class, provider))
        pick = scheduler.get_provider(env.providers, provider)
        saved = env.conn.execute('SELECT cwd FROM bee_worktrees WHERE project=? AND plan_path=? AND package=?', (env.name, plan, package)).fetchone()
        if saved:
            cwd = saved[0]
        else:
            for previous in reversed(runtime.list_agents(env.conn, env.name, 'all')):
                previous_cwd = previous.get('cwd')
                if (previous.get('plan_path') == plan and previous.get('package') == package and
                        previous.get('phase') == 'bee' and previous_cwd and
                        valid_bee_worktree(env, previous_cwd, queen.get('cwd'))):
                    cwd = previous_cwd
                    break
        if provider == hive['provider'] and embedded:
            if cwd:
                if Path(cwd).exists() and not valid_bee_worktree(env, cwd, queen.get('cwd')):
                    return refuse(env, package, 'the worktree %s is not a valid separate worktree with a branch of this repository' % cwd)
                env.conn.execute('INSERT OR IGNORE INTO bee_worktrees VALUES (?,?,?,?)', (env.name, plan, package, str(Path(cwd).absolute())))
                cwd = env.conn.execute('SELECT cwd FROM bee_worktrees WHERE project=? AND plan_path=? AND package=?', (env.name, plan, package)).fetchone()[0]
            entry = (pick.get('classes') or {}).get(selected_class) or {}
            record_embedded_start(env.conn, env.name, plan, package, name, selected_class, provider, str(entry.get('model') or ''), str(entry.get('effort') or ''))
            record_step_count(env.conn, env.name, plan, package, len(steps))
            # The subagent gets the same Bee rules as a Bee process: the Queen hands this prompt on as the task of the subagent.
            prompt = roles.role_text('bee-start', {'language': roles.language(env.settings)}) + '\n\n' + bee_prompt(name, package, task, steps, paths, rules, buildjobs.rule_text(env.settings.get('buildJobs')))
            return {'kind': 'subagent', 'provider': provider, 'class': selected_class, 'cwd': cwd, 'prompt': prompt}
        if not hives.can_start_bee(env.conn, env.name, plan, True, env.settings.get('contextLimitTokens', 300000)) or loop.ram_gb() < loop._min_ram(env) or free.get(provider, 0) <= 0:
            return refuse(env, package, 'a Bee process may not start now (starts paused or stopped, too little free RAM, or no free slot of provider %s)' % provider)
        path = Path(cwd or '')
        if not cwd or not valid_bee_worktree(env, path, queen.get('cwd')):
            return refuse(env, package, 'no valid separate worktree for the Bee was given')
        existing = [a['name'] for a in runtime.list_agents(env.conn, env.name, 'all')]
        state = {'names': existing, 'busy': [], 'plan_status': {scheduler.norm_plan(plan): 'IN_PROGRESS'}, 'by_path': {scheduler.norm_plan(plan): {'path': plan}}}
        ctx = {'env': env, 'state': state, 'pool': loop.Pool(free, 0), 'report': [], 'dry': False, 'settle': 0}
        action = {'name': name, 'type': provider, 'phase': 'bee', 'plan': plan, 'package': package, 'cwd': str(path), 'prompt': bee_prompt(name, package, task, steps, paths, rules, buildjobs.rule_text(env.settings.get('buildJobs'))), 'class': selected_class}
        if not loop._do_start(ctx, action, forced_pick=pick):
            return refuse(env, package, 'the agent runner could not start the Bee process')
        env.conn.execute('INSERT OR IGNORE INTO bee_worktrees VALUES (?,?,?,?)', (env.name, plan, package, str(path.absolute())))
        record_step_count(env.conn, env.name, plan, package, len(steps))
        return {'kind': 'process', 'provider': provider, 'class': selected_class, 'cwd': str(path.absolute())}


# Read the branch from the 'Plan: X; branch Y; baseline Z' line of the plan's state file next to it.
def state_branch(env, plan):
    """Return the branch named in the state file beside the plan (<name>-STATE.md, with a trailing -PLAN of the name dropped), or ''."""
    import re
    from pathlib import Path
    stem = re.sub(r'-PLAN$', '', Path(plan).stem)
    for name in (Path(plan).stem + '-STATE.md', stem + '-STATE.md'):
        try:
            text = (env.workspace / Path(plan).parent / name).read_text(encoding='utf-8-sig')
        except OSError:
            continue
        found = re.search(r'(?m)^\s*Plan:[^\n]*?;\s*branch\s+([\w./-]+?)\s*;', text)
        if found:
            return found.group(1)
    return ''


# Resolve the plan's named branch and optional named worktree.
def plan_worktree(env, plan):
    """Read an unambiguous branch and worktree, including numbered Branches rules."""
    import re
    from pathlib import Path
    text = (env.workspace / plan).read_text(encoding='utf-8-sig')
    text = re.sub(r'\*\*([^*\n]+)\*\*', r'\1', text)
    feature = re.search(r'(?im)^\s*[-*]?\s*feature[ -]branch\s*:\s*`?([\w./-]+)`?', text)
    pair = re.search(r'(?im)\bworktree\s*`([^`]+)`\s*,?\s*branch\s*`([^`]+)`', text)
    reverse = re.search(r'(?im)(?<!sub-)\bbranch(?:es)?\s*:?\s*`([^`]+)`\s+in\s+worktree\s*`([^`]+)`', text)
    git_line = re.search(r'(?im)^\s*[-*]?\s*git\s*:\s*branch\s*`([\w./-]+)`', text)
    named = feature or git_line
    if named:
        branch = named.group(1).rstrip('.')
        worktree = pair.group(1) if pair and pair.group(2) == branch else (reverse.group(2) if reverse and reverse.group(1) == branch else None)
    elif pair:
        worktree, branch = pair.groups()
    elif reverse:
        branch, worktree = reverse.groups()
    elif state_branch(env, plan):
        branch, worktree = state_branch(env, plan), None
    else:
        answers = runtime.list_questions(env.conn, env.name, status='answered', plan=plan, item='worktree')
        if not answers:
            return None
        lines = (answers[-1].get('answer') or '').strip().splitlines()
        if len(lines) != 2:
            return None
        branch, worktree = (line.strip() for line in lines)
        if not branch or not worktree:
            return None
    return branch, Path(worktree) if worktree else Path(__import__('haivequeen.routine', fromlist=['new_worktree']).new_worktree(env, scheduler.to_slug(branch)))


# Recognize a plan branch already owned by work outside the Beekeeper.
def foreign_plan_branch(env, plan):
    """Leave a queued or existing branch with no Hive history untouched."""
    import subprocess
    resolved = plan_worktree(env, plan)
    if not resolved:
        return False
    branch, _ = resolved
    if env.conn.execute("SELECT 1 FROM queue WHERE project=? AND branch=? AND status IN ('ready','blocked','in_run') LIMIT 1", (env.name, branch)).fetchone():
        return True
    if any(a.get('plan_path') == plan for a in runtime.list_agents(env.conn, env.name, 'all')):
        return False
    remote_name = str(env.settings.get('remoteName') or 'origin')
    for ref in ('refs/heads/' + branch, 'refs/remotes/' + remote_name + '/' + branch):
        result = subprocess.run(['git', '-C', str(env.code_repo), 'show-ref', '--verify', '--quiet', ref], capture_output=True)
        if result.returncode == 0:
            return True
    return False


# Reuse or create the explicitly named branch in a separate Git worktree.
def prepare_queen_worktree(env, plan):
    """Prepare the plan branch without changing the main checkout."""
    import subprocess
    from pathlib import Path
    resolved = plan_worktree(env, plan)
    if resolved is None:
        return None
    branch, target = resolved
    git = ['git', '-C', str(env.code_repo)]
    listing = subprocess.run(git + ['worktree', 'list', '--porcelain'], capture_output=True, text=True, encoding='utf-8', errors='replace')
    if listing.returncode:
        return None
    entries = listing.stdout.replace('\r\n', '\n').split('\n\n')
    for entry in entries:
        fields = entry.splitlines()
        if 'branch refs/heads/' + branch in fields:
            directory = next((line[9:] for line in fields if line.startswith('worktree ')), '')
            if directory and Path(directory).resolve() != env.code_repo.resolve():
                return str(Path(directory))
            return None
    if target.exists() or target.resolve() == env.code_repo.resolve():
        return None
    exists = subprocess.run(git + ['show-ref', '--verify', '--quiet', 'refs/heads/' + branch], capture_output=True).returncode == 0
    if exists:
        args = ['worktree', 'add', str(target), branch]
    else:
        remote_name = str(env.settings.get('remoteName') or 'origin')
        remote = subprocess.run(git + ['show-ref', '--verify', '--quiet', 'refs/remotes/' + remote_name + '/' + branch], capture_output=True).returncode == 0
        base = remote_name + '/' + (branch if remote else str(env.settings.get('integrationBranch') or 'main'))
        args = ['worktree', 'add', '-b', branch, str(target), base]
    result = subprocess.run(git + args, capture_output=True, text=True, encoding='utf-8', errors='replace')
    if result.returncode:
        env.log('cannot prepare plan worktree: ' + result.stderr.strip())
        return None
    return str(target)

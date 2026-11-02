"""Bee routing, package attempts and scoped Queen instructions."""
import json

from . import db, runtime, scheduler


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
    """Image work needs an image-capable Codex provider, an escalated package may use any provider, else only the Queen's provider and endpoint providers."""
    if 'images' in capability:
        return [p for p in providers if p.get('protocol') == 'codex' and 'images' in p.get('capabilities', [])]
    if escalated:
        return list(providers)
    candidates = [p for p in providers if p.get('endpoint') or p['name'] == hive_provider]
    candidates.sort(key=lambda p: not bool(p.get('endpoint')))
    return candidates


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


# Remember a Bee that runs as an embedded subagent of the Queen.
def record_embedded_start(conn, project, plan, package, name, cls, provider, model='', effort=''):
    """An embedded Bee has no agent row of its own: store it so the window can show it under its Queen. An older open row of the package is closed first."""
    ensure_attempts(conn)
    close_embedded(conn, project, plan, package, 'replaced')
    conn.execute('INSERT INTO embedded_bees(project,plan_path,package,name,class,provider,model,effort,started_at) VALUES (?,?,?,?,?,?,?,?,?)',
                 (project, plan, package, name, cls, provider, model, effort, db.now()))


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


# Write only the package task, relevant paths and rules to a Bee.
def bee_prompt(name, package, task, steps, paths, rules):
    """Build a slim Bee task with numbered progress reports."""
    return ('Work only package %s: %s\nSteps:\n%s\nPaths: %s\nRules: %s\n'
            'Use your own worktree and sub-branch for code; merge locally through your Queen. '
            'Report each completed step with bzz note: %s %s: step n/%d <short text>. '
            'Check your HANDOFF file after each step; at context limit finish your current step, write a handoff, and end. '
            'Report unsolved work and failing tests to your Queen.' %
            (package, task, '\n'.join('%d. %s' % (i + 1, s) for i, s in enumerate(steps)), ', '.join(paths), '\n'.join(rules), name, package, len(steps)))


PACKAGE_STATES = ('working', 'waiting', 'implemented', 'done', 'rejected')   # progress states of a package; a package without a row is open; waiting = needs an answer, a build or the owner (the reason says which); rejected = the owner did not accept it


# Describe the plan's Queen and Bee workflow with configured limits.
def queen_instructions(queen_class, bee_limit, threshold, context_limit):
    """Return Queen task instructions for the configured classes and limits."""
    return ('''Your class is %s. Keep token use economical. You coordinate and never work a package yourself: every package goes to a Bee, at most %s Bees at once, and only packages that depend on no unfinished package and share no files with a running Bee start together. Besides that you answer technical questions, merge the Bee branches locally, check the results and, when every requirement is done, integrate: git, compile check and build. If Codex exec cannot start subagents, supply --no-subagents: every Bee then runs as a separate process.
Give every Bee a short female name. A Bee gets only its package task, numbered steps, needed paths and rules, never the whole plan or CLAUDE.md. Code Bees work on their own worktree and sub-branch and you merge locally. Each reports every step via bzz note. Use bzz bee start with the project, plan, package, class, name, task, numbered steps, paths, rules and the Bee worktree. When it returns subagent, start the Bee as your native subagent without another process, using the returned cwd even if it differs from the requested path; when it returns process, the Bee already runs as its own agent. A refused start answers waiting with a reason: read it and never assume that the provider is busy. Report the progress of every package of your plan with bzz package --project --plan --package --state working (when you start it), waiting with --reason question:<id>, build, owner, package or provider (it needs an answer, a build, the owner, another package or a provider; this is no failure), implemented (everything possible without that is done, not yet verified) or done (verified against its requirements); also keep the state file current. A package whose plan entry says verify build or owner is closed by the build or the owner, not by its Bee: when the Bee has done all code work, set it implemented. A package in state rejected was not accepted by the owner: rework it, then set it to implemented or done; bzz ready is refused while one is rejected. If an owner-check answer says something is not right and no package is marked rejected, mark the packages it concerns rejected yourself. The window shows this progress. The system files the question after the highest class failed; do not file another one for the same package. Record each outcome with bzz bee result, a unique attempt name, the class that attempted it and outcome failed, interrupted or succeeded; for a process Bee use the exact Bee name as the attempt name. Before starting a dependent package, record a succeeded result once for any dependency completed before success recording was available, using the state file as evidence. If a dependency is not in the plan, ask the owner with bzz ask. When only a package waiting on a provider remains, use bzz bee wait with that provider, write a handoff and end. Prefer a free local provider offering the class; otherwise use your own provider. Codex is only for images or escalation, unless it is your own provider or another provider is needed because neither your provider nor the local model offers the required or a higher class. Images always use Codex; if unavailable ask the owner once when no eligible provider exists, otherwise continue independent work while waiting. Excluded providers are never used.
Count failed attempts per package and class persistently: an attempt is a Bee ending unsolved or with required tests red. After each attempt check its open requirements; report them with --open-requirements as a JSON list and the last error line with --error (for a native Bee, the first failing test or error message); a failed result without either is refused. A Bee that waits for an answer, a build or the owner is no failure: report it with --blocked-by question (with --blocked-ref <question id>), build, owner, package or provider; nothing is counted and the package is set to waiting. After the answer the package works again and its Bee restarts at the same class. A failed attempt counts only if the same last error came again at this class or, for a package closed by code, an open requirement was already open after the previous attempt at this class; open requirements of a package closed by build or owner never count, and neither does any attempt of a package in state implemented. Both signals together count once. Incompleteness by itself is never a reason to escalate. After %s failed attempts escalate to the next higher offered class; start the counter at zero in the new class. Never count an interruption, API or usage limit as failure. For image packages escalate only through Codex classes; after failures at the highest Codex class ask the owner. For other packages ask the owner after the same threshold at the highest available class. When a provider is busy or limited wait for it, never silently change it. Read each Bee result, including after a Queen restart. If a Bee process vanished, restart its package with the same class and provider; its interruption is never a failure. Start a replacement for the remaining package after a context handoff.
Never measure your context yourself: the token numbers of bzz agents and bzz usage are summed over all steps (Codex counts the whole context again at every step) and are far above the real context size. The Beekeeper watches your current context and writes the file HANDOFF in your run folder when it reaches %s; only when that file exists, start no new Bees, let running Bees finish, finish your current step, write a handoff and end. If only unanswered owner questions remain, continue independent work first, then handoff and wait for an answer. Preserve dependencies, branch, state and completed steps.
For technical blockers, identify at least two technical ways and choose the first that works without owner action or elevation. Ask the owner only if no alternative works, explaining why no alternative works; the missing image provider and highest class failure exceptions above still apply. Triage every question yourself before you file it: first read bzz decisions list, the plan, the state file and the code. A technical question (documentation, tests, code structure, a value that follows from the plan or the code, a tool or permission you can work around) you decide yourself: record the decision with bzz decisions add (--source auto, --plan, --item, --text with the decision, the reason and the source) and in the state file, and go on; never file it. Only game, balance and design questions go to the owner with bzz ask; in doubt treat it as an owner question. A product question pauses only that item; all independent work continues.
Before integration answer for each requirement with evidence (file, commit or test): Wurden alle Tasks aus dem Plan umgesetzt? Gibt es Anforderungen, welche nicht umgesetzt wurden? Write the answer into the state file and finish with Offene Anforderungen: 0 only when no requirement remains open. A partial requirement counts as open. Continue work while open. List owner-only play checks separately as owner questions without counting them as open: points only the owner can check (named as an owner check in the plan), and points the owner has set aside or excluded in a decision (bzz decisions list), are never counted in Offene Anforderungen; this wins over any project rule or Codex rule that says open or partial requirements block integration. If you cannot go on (rules contradict each other, a decision or a permission is missing), never end silently as unfinished: file one concrete question with bzz ask (project, plan, item, question, recommendation), write a handoff and end your final report with WAITING_FOR_OWNER. Then merge origin/<integrationBranch> into your branch, keep both sides, run the project compile check until green, register your own branch and perform the project's integration steps for your branch only. Wait for the project integration place and handoff if it is occupied. After integration and deploy, if points remain that only the owner can check, file one question with bzz ask --item owner-check saying what to check, write a handoff and end with WAITING_FOR_OWNER; the plan stays open until he has answered. If the answer to an owner-check question says something is not right, fix exactly that and integrate again; if it says ok, the plan is done. You end only when one of three things is true: the plan is integrated and deployed (and its owner check, if it has one, is answered); only questions remain that the owner must answer (file them with bzz ask, write a handoff, end your final report with WAITING_FOR_OWNER); or the file HANDOFF exists in your run folder (context limit). Otherwise never end after an intermediate step: a finished package, a Bee that ended, a green test run or a progress report is not an end; report progress with bzz note and go on with the next open package in the same run. This wins over any rule that asks for a report or a status at the end of a run or after a step.''' % (queen_class, bee_limit, threshold, context_limit))


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


# Restrict image escalation to configured Codex providers.
def escalation_providers(env, plan, package):
    """Return the providers eligible to supply the next package class."""
    from . import routine
    if 'images' in routine.package_needs_for(env, plan, package):
        return [p for p in env.providers if p.get('protocol') == 'codex' and 'images' in p.get('capabilities', [])]
    return env.providers


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
        text = 'The package remains unsolved at the highest available class. How should it continue?'
        if not any(q.get('question') == text for q in runtime.list_questions(env.conn, env.name, plan=plan, item=package)):
            db.add_question(env.conn, env.name, plan, package, text, 'Review the failed attempts and choose the next approach.')


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


# Start a separate Bee process after validating context, memory, slots and routing.
def start_process(env, plan, package, name, cls, task, steps, paths, rules, cwd=None, no_subagents=False, parallel=False):
    """Start an eligible Bee through the agent runner. Every package goes to a Bee, the Queen never works one herself; the provider setting beeMode decides whether
    the Bee of the Queen's own provider is an embedded subagent or a separate process (`parallel` is kept for old callers and has no effect).
    A refusal returns False and leaves its reason in LAST_REFUSAL."""
    global LAST_REFUSAL
    LAST_REFUSAL = ''
    from pathlib import Path
    from . import hives, loop, routine, usage
    with loop.hive_schedule_lock():
        check_dependencies(env, plan, package)
        hive = hives.get(env.conn, env.name, plan)
        if not hive or not hives.can_start_bee(env.conn, env.name, plan, False, env.settings.get('contextLimitTokens', 300000)):
            return refuse(env, package, 'the Hive may not start a Bee now (Hive state %s, context limit or HANDOFF reached)' % (hive['state'] if hive else 'missing'))
        active = runtime.list_agents(env.conn, None, 'active')
        queen = next((a for a in active if a.get('project') == env.name and a.get('plan_path') == plan and a.get('phase') == 'queen' and a.get('status') in ('running', 'starting')), None)
        if not queen:
            return refuse(env, package, 'no running Queen of this plan')
        from . import platform as osplat
        from . import agent_process
        running = [{**a, 'status': 'running'} for a in active if agent_process.counts(a)]
        if sum(1 for a in running if a.get('project') == env.name and a.get('plan_path') == plan and a.get('phase') == 'bee') >= int(env.settings.get('beesPerQueen', 3)):
            return refuse(env, package, 'the Queen already has %s Bees running' % env.settings.get('beesPerQueen', 3))
        package_row = next((p for p in env.packages(plan) if p['id'] == package), None)
        if not package_row:
            return refuse(env, package, 'the package is not in the plan')
        requested_class = package_row['cls']
        threshold = int(env.settings.get('escalateAfter', 3))
        excluded = env.settings.get('excludedProviders', [])
        need = routine.package_needs_for(env, plan, package)
        allowed = [p for p in env.providers if p['name'] not in excluded and
                   ((p.get('protocol') == 'codex' and 'images' in p.get('capabilities', [])) if 'images' in need else
                    (p.get('endpoint') or p['name'] == hive['provider']))]
        base_class = next((name for name in scheduler.CLASS_NAMES[scheduler.class_index(requested_class):]
                           if any((p.get('classes', {}).get(name) or {}).get('model') for p in allowed)), None)
        use_other_provider = False
        if base_class is None and 'images' not in need:
            others = [p for p in env.providers if p['name'] not in excluded]
            base_class = next((name for name in scheduler.CLASS_NAMES[scheduler.class_index(requested_class):]
                               if any((p.get('classes', {}).get(name) or {}).get('model') for p in others)), None)
            use_other_provider = base_class is not None
        if base_class is None:
            text = ('No configured provider offers the images capability. How should this package continue?' if 'images' in need
                    else 'No configured provider offers the required class or a higher class. How should this package continue?')
            if not any(q.get('question') == text for q in runtime.list_questions(env.conn, env.name, plan=plan, item=package)):
                db.add_question(env.conn, env.name, plan, package, text, 'Configure an eligible provider.')
            return refuse(env, package, 'no configured provider offers the required class or the images capability (question filed)')
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
            return {'kind': 'subagent', 'provider': provider, 'class': selected_class, 'cwd': cwd}
        if not hives.can_start_bee(env.conn, env.name, plan, True, env.settings.get('contextLimitTokens', 300000)) or loop.ram_gb() < loop._min_ram(env) or free.get(provider, 0) <= 0:
            return refuse(env, package, 'a Bee process may not start now (starts paused or stopped, too little free RAM, or no free slot of provider %s)' % provider)
        path = Path(cwd or '')
        if not cwd or not valid_bee_worktree(env, path, queen.get('cwd')):
            return refuse(env, package, 'no valid separate worktree for the Bee was given')
        existing = [a['name'] for a in runtime.list_agents(env.conn, env.name, 'all')]
        state = {'names': existing, 'busy': [], 'plan_status': {scheduler.norm_plan(plan): 'IN_PROGRESS'}, 'by_path': {scheduler.norm_plan(plan): {'path': plan}}}
        ctx = {'env': env, 'state': state, 'pool': loop.Pool(free, 0), 'report': [], 'dry': False, 'settle': 0}
        action = {'name': name, 'type': provider, 'phase': 'bee', 'plan': plan, 'package': package, 'cwd': str(path), 'prompt': bee_prompt(name, package, task, steps, paths, rules), 'class': selected_class}
        if not loop._do_start(ctx, action, forced_pick=pick):
            return refuse(env, package, 'the agent runner could not start the Bee process')
        env.conn.execute('INSERT OR IGNORE INTO bee_worktrees VALUES (?,?,?,?)', (env.name, plan, package, str(path.absolute())))
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
    for ref in ('refs/heads/' + branch, 'refs/remotes/origin/' + branch):
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
        remote = subprocess.run(git + ['show-ref', '--verify', '--quiet', 'refs/remotes/origin/' + branch], capture_output=True).returncode == 0
        base = 'origin/' + (branch if remote else str(env.settings.get('integrationBranch', 'main')))
        args = ['worktree', 'add', '-b', branch, str(target), base]
    result = subprocess.run(git + args, capture_output=True, text=True, encoding='utf-8', errors='replace')
    if result.returncode:
        env.log('cannot prepare plan worktree: ' + result.stderr.strip())
        return None
    return str(target)

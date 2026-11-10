"""Agent routing, package attempts and scoped Queen instructions."""
import json
import re
import time

from . import buildjobs, db, roles, runtime, scheduler, servers
from . import plans as plan_mod
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
    candidates = [p for p in providers if (p.get('endpoint') or p.get('server')) or p['name'] == hive_provider]
    candidates.sort(key=lambda p: not bool((p.get('endpoint') or p.get('server'))))
    return candidates


def capable_providers(providers, need) -> list:
    """The providers that offer every capability in `need`."""
    return [p for p in providers if ('Confidential data' not in [scheduler.canonical_capability(n) for n in need] or scheduler._local_confidential(p)) and all(scheduler.canonical_capability(n) in [scheduler.canonical_capability(c) for c in p.get('capabilities', [])] for n in need)]


def select_route(providers, free, hive_provider, package_class, capability, excluded, escalated, limited=(), conn=None):
    """Pick a permitted Agent provider and class, returning None when it must wait.

    when the permitted providers of the class (a free local provider, the Queen's own one) have no free slot, the Agent goes to another
    provider by the normal provider choice (class, capabilities, quota reserve, free slot); with no free slot anywhere it waits."""
    providers = [p for p in providers if p['name'] not in excluded and not p.get('unavailable')]   # a disabled or offline provider gets no new Bee
    candidates = route_candidates(providers, hive_provider, capability, escalated)
    index = scheduler.class_index(package_class)
    for cls in scheduler.CLASS_NAMES[index:]:
        matching = [p for p in candidates if (p.get('classes', {}).get(cls) or {}).get('model')]
        if not matching:
            continue
        if escalated:   # an escalated package takes, among the providers it may use, the one with the best reserve, not list order
            others = [{**p, 'slots': p.get('slots', 1), 'use': p.get('use', 'preferred'), 'limit': p.get('limit', ''), 'protocol': p.get('protocol', ''),
                       'capabilities': scheduler.provider_capabilities(p)} for p in providers if p['name'] not in limited]
            pick = scheduler.select_provider(others, cls, capability, free, conn=conn)
            return (pick['name'], cls) if pick else None
        for p in matching:   # with a needed capability every capable provider is a candidate: only the Queen's own one and a local one are taken directly
            if capability and p['name'] != hive_provider and not (p.get('endpoint') or p.get('server')):
                continue
            if free.get(p['name'], 0) > 0 and p['name'] not in limited:
                return p['name'], cls
        others = [{**p, 'slots': p.get('slots', 1), 'use': p.get('use', 'preferred'), 'limit': p.get('limit', ''), 'protocol': p.get('protocol', ''),
                   'capabilities': scheduler.provider_capabilities(p)} for p in providers if p['name'] not in limited]
        pick = scheduler.select_provider(others, cls, capability, free, conn=conn)
        return (pick['name'], cls) if pick else None
    return None


# Create persistent package completion and per-class attempt tables.
def ensure_attempts(conn):
    """Create persistent completion and failure records if absent."""
    conn.execute('CREATE TABLE IF NOT EXISTS bee_attempts (project TEXT NOT NULL, plan_path TEXT NOT NULL, package TEXT NOT NULL, class TEXT NOT NULL, failures INTEGER NOT NULL DEFAULT 0, PRIMARY KEY(project,plan_path,package,class))')
    conn.execute('CREATE TABLE IF NOT EXISTS bee_attempt_reports (project TEXT NOT NULL, plan_path TEXT NOT NULL, package TEXT NOT NULL, attempt TEXT NOT NULL, PRIMARY KEY(project,plan_path,package,attempt))')
    conn.execute('CREATE TABLE IF NOT EXISTS bee_attempt_signals (project TEXT NOT NULL, plan_path TEXT NOT NULL, package TEXT NOT NULL, class TEXT NOT NULL, attempt TEXT NOT NULL, open_requirements TEXT NOT NULL, error TEXT NOT NULL, counted INTEGER NOT NULL, PRIMARY KEY(project,plan_path,package,attempt))')
    conn.execute('CREATE TABLE IF NOT EXISTS bee_retry_answers (question_id INTEGER PRIMARY KEY, project TEXT NOT NULL, plan_path TEXT NOT NULL, package TEXT NOT NULL, signal_rowid INTEGER NOT NULL, previous_counts TEXT NOT NULL)')
    conn.execute('CREATE TABLE IF NOT EXISTS bee_assignments (project TEXT NOT NULL, plan_path TEXT NOT NULL, package TEXT NOT NULL, class TEXT NOT NULL, provider TEXT NOT NULL, PRIMARY KEY(project,plan_path,package))')
    conn.execute('CREATE TABLE IF NOT EXISTS bee_worktrees (project TEXT NOT NULL, plan_path TEXT NOT NULL, package TEXT NOT NULL, cwd TEXT NOT NULL, PRIMARY KEY(project,plan_path,package))')
    conn.execute('CREATE TABLE IF NOT EXISTS bee_worktree_branches (project TEXT NOT NULL, plan_path TEXT NOT NULL, package TEXT NOT NULL, branch TEXT NOT NULL, PRIMARY KEY(project,plan_path,package))')
    conn.execute('CREATE TABLE IF NOT EXISTS bee_completed (project TEXT NOT NULL, plan_path TEXT NOT NULL, package TEXT NOT NULL, PRIMARY KEY(project,plan_path,package))')
    conn.execute('CREATE TABLE IF NOT EXISTS package_progress (project TEXT NOT NULL, plan_path TEXT NOT NULL, package TEXT NOT NULL, state TEXT NOT NULL, agent TEXT NOT NULL, updated_at TEXT NOT NULL, PRIMARY KEY(project,plan_path,package))')
    if 'reason' not in [r[1] for r in conn.execute('PRAGMA table_info(package_progress)')]:
        conn.execute("ALTER TABLE package_progress ADD COLUMN reason TEXT NOT NULL DEFAULT ''")
    conn.execute("CREATE TABLE IF NOT EXISTS embedded_bees (project TEXT NOT NULL, plan_path TEXT NOT NULL, package TEXT NOT NULL, name TEXT NOT NULL, class TEXT NOT NULL, provider TEXT NOT NULL, model TEXT NOT NULL DEFAULT '', effort TEXT NOT NULL DEFAULT '', started_at TEXT NOT NULL, ended_at TEXT, outcome TEXT NOT NULL DEFAULT '')")
    if 'queen' not in [r[1] for r in conn.execute('PRAGMA table_info(embedded_bees)')]:
        conn.execute("ALTER TABLE embedded_bees ADD COLUMN queen TEXT NOT NULL DEFAULT ''")   # the Queen whose native subagent the Bee is
    conn.execute('CREATE TABLE IF NOT EXISTS bee_step_counts (project TEXT NOT NULL, plan_path TEXT NOT NULL, package TEXT NOT NULL, steps INTEGER NOT NULL, PRIMARY KEY(project,plan_path,package))')


# Remember how many numbered steps the Bee of a package got.
def record_step_count(conn, project, plan, package, steps):
    """Store the number of steps of the newest Agent of the package: the window shows 0/steps from its start, until it reports a step."""
    ensure_attempts(conn)
    conn.execute('INSERT INTO bee_step_counts(project,plan_path,package,steps) VALUES (?,?,?,?) ON CONFLICT(project,plan_path,package) DO UPDATE SET steps=excluded.steps', (project, plan, package, int(steps)))


# Remember a Bee that runs as an embedded subagent of the Queen.
def record_embedded_start(conn, project, plan, package, name, cls, provider, model='', effort='', queen=''):
    """An embedded Agent has no agent row of its own: store it so the window can show it under its Queen `queen`.

    An older open row of the package is never closed here: a new start does not prove that the older subagent ended, so
    start_process refuses a new Agent while one is open."""
    ensure_attempts(conn)
    conn.execute('INSERT INTO embedded_bees(project,plan_path,package,name,class,provider,model,effort,started_at,queen) VALUES (?,?,?,?,?,?,?,?,?,?)',
                 (project, plan, package, name, cls, provider, model, effort, db.now(), queen or ''))


# Names that open embedded Bees hold.
def open_embedded_names(conn, skip=None) -> set:
    """The names of the embedded Agents that have not ended; `skip` is a (project, plan, package) whose Agent does not count because it is being replaced."""
    if not conn.execute("SELECT 1 FROM sqlite_master WHERE type='table' AND name='embedded_bees'").fetchone():
        return set()
    rows = conn.execute('SELECT name,project,plan_path,package FROM embedded_bees WHERE ended_at IS NULL').fetchall()
    return {r[0] for r in rows if (r[1], r[2], r[3]) != tuple(skip or ())}


# Mark embedded Bees as ended.
def close_embedded(conn, project, plan, package=None, outcome='done', keep_stopping=False):
    """Close the open embedded Agents of a package, or of the whole plan when no package is given, with the outcome that ended them.

    An Agent whose stop is pending (outcome STOPPING) is closed only by a confirmation of its end (its result reported by its Queen), then with the outcome
    stopped. `keep_stopping` leaves such an Agent open: a replacement or a Queen restart does not prove that the subagent ended.
    Returns the number of rows closed whose stop was pending."""
    ensure_attempts(conn)
    where, args = ' WHERE project=? AND plan_path=? AND ended_at IS NULL', [project, plan]
    if package:
        where += ' AND package=?'
        args.append(package)
    confirmed = 0
    cur = conn.execute('SELECT rowid AS rid,* FROM embedded_bees' + where, args)
    names = [c[0] for c in cur.description]
    for values in cur.fetchall():
        row = dict(zip(names, values))
        stopping = row['outcome'] == STOPPING
        if stopping and keep_stopping:
            continue
        confirmed += int(stopping)
        end = 'stopped' if stopping else outcome
        if enforce_agent_end(conn, project, row):
            end = 'failed'
        conn.execute('UPDATE embedded_bees SET ended_at=?, outcome=? WHERE rowid=?', (db.now(), end, row['rid']))
    return confirmed


# An embedded Bee the owner stopped keeps its row and its Bee place until its end is confirmed.
STOPPING = 'stopping'


def unconfirmed_embedded(conn, project, plan, package=None):
    """The open embedded Agent rows (dicts) of the plan or one of its packages, oldest first: none of them has a confirmed end."""
    ensure_attempts(conn)
    sql, args = 'SELECT * FROM embedded_bees WHERE project=? AND plan_path=? AND ended_at IS NULL', [project, plan]
    if package:
        sql += ' AND package=?'
        args.append(package)
    cur = conn.execute(sql + ' ORDER BY started_at', args)
    names = [c[0] for c in cur.description]
    return [dict(zip(names, r)) for r in cur.fetchall()]


# State of one recorded process of a Queen.
def _process_state(pid, identity, bound_row, alive):
    """'absent', 'present' or 'unknown' for one recorded process of a Queen; 'none' when nothing is recorded for it.

    With a recorded identity the process is hers when its identity matches; without one, when its pid matches and the start-time check of agent_process
    does not mark it as reused. An identity that cannot be read (for example access denied) leaves the answer unknown."""
    from . import agent_process, platform as osplat
    try:
        pid = int(pid or 0)
    except (TypeError, ValueError):
        pid = 0
    if pid <= 0:
        return 'unknown' if identity else 'none'   # an identity without a pid cannot be looked up
    if not alive(pid):
        return 'absent'
    if identity:
        current = osplat.process_identity(pid)
        if current is None:
            return 'unknown'
        return 'present' if current == identity else 'absent'
    if osplat.process_inaccessible(pid):
        return 'unknown'
    return 'absent' if agent_process.pid_reused(bound_row) else 'present'


def queen_of_embedded(conn, row):
    """The agent row (dict) of the Queen of an embedded Agent row: the row with the recorded Queen name in the same Project and plan whose start is the latest
    before the Agent's start; None when the name is empty or no such row exists."""
    name = row.get('queen') or ''
    if not name:
        return None
    best = None
    for r in conn.execute("SELECT * FROM agents WHERE project=? AND name=? AND plan_path=? AND phase='queen' ORDER BY id", (row['project'], name, row['plan_path'])):
        agent = runtime._agent_out(r)
        started = str(agent.get('started_at') or '')
        if started and started <= str(row['started_at'] or '') and (best is None or started >= str(best.get('started_at') or '')):
            best = agent
    return best


def queen_has_ended(conn, row, alive=None):
    """True only when it is certain that the Queen of the embedded Agent row has ended: her agent row is no longer active and neither her runner process
    nor the CLI process it started runs. Everything uncertain (E-3) answers False."""
    from . import platform as osplat
    alive = alive or osplat.process_alive
    queen = queen_of_embedded(conn, row)
    if not queen or queen.get('status') in runtime.ACTIVE_STATUSES:
        return False
    runner = _process_state(queen.get('pid'), queen.get('processIdentity'), queen, alive)
    cli = _process_state(queen.get('cliPid'), queen.get('cliIdentity'), {**queen, 'pid': queen.get('cliPid')}, alive)
    if runner == 'none':
        return False   # neither pid nor identity recorded
    return runner == 'absent' and cli in ('absent', 'none')


def close_ended_embedded(conn, project=None, alive=None) -> int:
    """E-1: close every open embedded Agent row whose Queen has certainly ended, with the outcome interrupted (stopped for a pending stop). No attempt is
    counted and no package state changes: the next Queen reads the result file, if any, and reports the package. Returns the number of rows closed."""
    ensure_attempts(conn)
    sql, args = 'SELECT rowid AS rid, * FROM embedded_bees WHERE ended_at IS NULL', []
    if project:
        sql += ' AND project=?'
        args.append(project)
    cur = conn.execute(sql + ' ORDER BY started_at', args)
    names = [c[0] for c in cur.description]
    closed, plans = [], set()
    for values in cur.fetchall():
        row = dict(zip(names, values))
        if not queen_has_ended(conn, row, alive):
            continue
        outcome = 'stopped' if row.get('outcome') == STOPPING else 'interrupted'
        if enforce_agent_end(conn, row['project'], row):
            outcome = 'failed'
        conn.execute('UPDATE embedded_bees SET ended_at=?, outcome=? WHERE rowid=? AND ended_at IS NULL', (db.now(), outcome, row['rid']))
        closed.append(row)
        plans.add((row['project'], row['plan_path']))
    for row in closed:
        runtime.append_event(conn, row['project'], 'The subagent Agent %s (%s) ended with its Queen %s; its Agent place is free' % (row['name'], row['package'], row['queen']), kind='note', mirror='none')
    for proj, plan in plans:
        write_stop_notice(conn, proj, plan)
    return len(closed)


def own_embedded(row, queen) -> bool:
    """True when the open embedded Agent row is a native subagent of the running Queen `queen` (agent dict): only she sees its end directly.
    A row without a recorded Queen (written before the Queen was recorded) counts as hers when it started after her."""
    if not queen:
        return False
    started = str(queen.get('started_at') or '')
    if row.get('queen'):
        # A Queen name can be used by two agent rows: a Bee that started before this Queen belongs to an earlier one.
        return row['queen'] == queen.get('name') and (not started or str(row.get('started_at') or '') >= started)
    return bool(queen.get('started_at')) and str(row.get('started_at') or '') >= str(queen['started_at'])


def release_refusal(conn, project, plan, package, queen, evidence='', outcome=''):
    """What stops `bzz agent result` from releasing the open embedded Agents of the package ('' when nothing does).

    An Agent of the running Queen herself has returned its result to her: succeeded or failed needs the result file the Agent wrote after its start
    (--ended-evidence), interrupted (her confirmation of an Agent she stopped) needs none. An Agent of an earlier Queen is closed by HaiveQueen once that
    Queen has ended (close_ended_embedded); while its row is still open, a result needs a file written after its start and an ended Agent agent row or
    a known absent Agent process."""
    from pathlib import Path
    from . import platform
    foreign = unconfirmed_embedded(conn, project, plan, package)
    if not foreign:
        return ''
    own = [r for r in foreign if own_embedded(r, queen)]
    earlier = [r for r in foreign if r not in own]
    if own and not earlier and outcome == 'interrupted':
        return ''
    row = foreign[-1]
    who = 'the subagent Agent %s (%s) of Queen %s' % (row['name'], package, row.get('queen') or 'unknown')
    if not evidence:
        return ('%s has no confirmed end; record its result only with --ended-evidence <the result file the Agent wrote at its end, such as its final report>. '
                'Without such a file leave it open: it keeps its Agent place' % who)
    path = Path(evidence)
    if not path.is_file():
        return 'the evidence %s for %s does not exist or is no file' % (evidence, who)
    started = max(db.parse_time(r['started_at']) for r in foreign)
    if path.stat().st_mtime < started.timestamp():
        return 'the evidence %s was written before %s started, so it proves no end of it' % (evidence, who)
    for bee in earlier:
        agent = runtime.get_agent(conn, project, bee['name'])
        if not (agent and agent.get('phase') == 'bee' and agent.get('plan_path') == plan and agent.get('package') == package
                and str(agent.get('started_at') or '') >= bee['started_at']):
            return 'Agent %s has no matching agent row proving its end; its row and Agent place stay open until its Queen has ended' % bee['name']
        ended = str(agent.get('ended_at') or '') >= bee['started_at'] and agent.get('status') not in runtime.ACTIVE_STATUSES
        try:
            pid = int(agent.get('pid') or 0)
        except (TypeError, ValueError):
            pid = 0
        absent = pid > 0 and not platform.process_alive(pid)
        if not (ended or absent):
            return 'Agent %s has no confirmed end; a newer result file is not proof of its end' % bee['name']
    return ''


def stopping_embedded(conn, project, plan=None, package=None):
    """(plan, package, name) of the open embedded Agents whose stop is pending, oldest first."""
    if not conn.execute("SELECT 1 FROM sqlite_master WHERE type='table' AND name='embedded_bees'").fetchone():
        return []
    sql, args = 'SELECT plan_path, package, name FROM embedded_bees WHERE project=? AND ended_at IS NULL AND outcome=?', [project, STOPPING]
    if plan:
        sql += ' AND plan_path=?'
        args.append(plan)
    if package:
        sql += ' AND package=?'
        args.append(package)
    return [tuple(r) for r in conn.execute(sql + ' ORDER BY started_at', args)]


# Count a failed attempt, ignoring technical interruptions.
def record_attempt(conn, project, plan, package, cls, outcome, attempt=None, open_requirements=None, error='', verify='code', force_failure=False):
    """Record an attempt once. A failed attempt counts only when the same error came again or, for a package closed by code, the same requirement stayed open. A package in state implemented never counts; for a package closed by build or owner open requirements alone never count."""
    ensure_attempts(conn)
    if outcome == 'succeeded':
        conn.execute('INSERT OR IGNORE INTO bee_completed VALUES(?,?,?)', (project, plan, package))
        set_package_state(conn, project, plan, package, 'done')
    if outcome == 'interrupted':
        return
    with db.transaction(conn):
        if attempt and conn.execute('INSERT OR IGNORE INTO bee_attempt_reports(project,plan_path,package,attempt) VALUES(?,?,?,?)', (project, plan, package, attempt)).rowcount == 0:
            if force_failure:
                prior = conn.execute('SELECT counted FROM bee_attempt_signals WHERE project=? AND plan_path=? AND package=? AND attempt=?', (project, plan, package, attempt)).fetchone()
                if prior and not prior[0]:
                    conn.execute('UPDATE bee_attempt_signals SET counted=1,error=? WHERE project=? AND plan_path=? AND package=? AND attempt=?', (error, project, plan, package, attempt))
                    conn.execute('INSERT INTO bee_attempts VALUES(?,?,?,?,1) ON CONFLICT(project,plan_path,package,class) DO UPDATE SET failures=failures+1', (project, plan, package, cls))
                    conn.execute('DELETE FROM bee_completed WHERE project=? AND plan_path=? AND package=?', (project, plan, package))
                    set_package_state(conn, project, plan, package, 'working', attempt)
            return
        if attempt:
            epoch = retry_epoch(conn, project, plan, package)
            previous = conn.execute('SELECT open_requirements,error FROM bee_attempt_signals WHERE project=? AND plan_path=? AND package=? AND class=? AND rowid>? ORDER BY rowid DESC LIMIT 1', (project, plan, package, cls, epoch or 0)).fetchone()
            state = conn.execute('SELECT state FROM package_progress WHERE project=? AND plan_path=? AND package=?', (project, plan, package)).fetchone()
            same_requirement = verify == 'code' and previous and set(open_requirements or []) & set(json.loads(previous[0]))
            same_error = previous and error and error == previous[1]
            counted = bool(outcome == 'failed' and (force_failure or (not (state and state[0] == 'implemented') and (same_requirement or same_error))))
            conn.execute('INSERT INTO bee_attempt_signals VALUES(?,?,?,?,?,?,?,?)', (project, plan, package, cls, attempt, json.dumps(open_requirements or []), error, int(counted)))
        if attempt and counted:
            conn.execute('INSERT INTO bee_attempts(project,plan_path,package,class,failures) VALUES(?,?,?,?,1) ON CONFLICT(project,plan_path,package,class) DO UPDATE SET failures=failures+1', (project, plan, package, cls))


# Remember how far a package of a Hive is.
def set_package_state(conn, project, plan, package, state, agent='', reason=''):
    """Store the progress state of one package (working, waiting, implemented, done or rejected; no row means open). `reason` says what a waiting package waits for (question:<id>, build, owner, package or provider). The Plan progress shown in the window counts the done ones, a rejected package is not done."""
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


FAILURE_QUESTION = 'The package remains unsolved at the highest available class. How should it continue?'


def retry_epoch(conn, project, plan, package):
    """Last signal before the most recent owner-directed retry; None before any answer."""
    # Signal boundaries advance when answers arrive, regardless of question creation order.
    row = conn.execute('SELECT MAX(signal_rowid) FROM bee_retry_answers WHERE project=? AND plan_path=? AND package=?', (project, plan, package)).fetchone()
    return row[0] if row else None


def answer_failure_question(conn, question, by):
    """Start a fresh counting cycle once for an owner/coordinator failure answer, retaining history."""
    if by not in ('owner', 'coordinator') or question['question'] not in (FAILURE_QUESTION, tr(FAILURE_QUESTION, language='German')):
        return
    ensure_attempts(conn)
    key = (question['project'], question['plan_path'], question['item'])
    counts = dict(conn.execute('SELECT class,failures FROM bee_attempts WHERE project=? AND plan_path=? AND package=?', key).fetchall())
    last = conn.execute('SELECT COALESCE(MAX(rowid),0) FROM bee_attempt_signals').fetchone()[0]
    if conn.execute('INSERT OR IGNORE INTO bee_retry_answers VALUES (?,?,?,?,?,?)', (question['id'], *key, last, json.dumps(counts))).rowcount:
        conn.execute('UPDATE bee_attempts SET failures=0 WHERE project=? AND plan_path=? AND package=?', key)


# Determine the current or next escalation class.
def next_attempt(conn, project, plan, package, cls, providers, excluded, threshold):
    """Return the current class, its next offered class, or None at the top."""
    if failure_count(conn, project, plan, package, cls) < threshold:
        return cls
    return next_class(providers, cls, excluded)


# Write only the package task and relevant paths to a Bee; the complete project rules are loaded by the runner, a `rules` argument never replaces them.
def bee_prompt(name, package, task, steps, paths, rules, build_rules=''):
    """Build a slim Agent task with numbered progress reports."""
    return roles.role_text('bee', {'package': package, 'task': task, 'steps': '\n'.join('%d. %s' % (i + 1, s) for i, s in enumerate(steps)), 'paths': ', '.join(paths), 'rules': '\n'.join(rules), 'name': name, 'step_count': len(steps), 'build_rules': build_rules}).rstrip()


PACKAGE_STATES = ('working', 'waiting', 'implemented', 'done', 'rejected')   # progress states of a package; a package without a row is open; waiting = needs an answer, a build or the owner (the reason says which); rejected = the owner did not accept it


# Describe the plan's Queen and Bee workflow with configured limits.
def queen_instructions(queen_class, bee_limit, threshold, context_limit, language=None):
    """Return Queen task instructions for the configured classes and limits."""
    return roles.role_text('queen', {'class': queen_class, 'bee_limit': bee_limit, 'escalate_after': threshold, 'context_limit': context_limit, 'language': language or roles.language({})})


# Packages the owner stopped with "Stop Bee": they stay stopped until the owner resumes them.
STOP_KEY = 'package-stopped:'


STOP_NOTICE_FILE = 'STOP-PACKAGES'


def stop_notice_text(conn, project, plan):
    """The text of STOP-PACKAGES for the plan, empty when no package is stopped and no stop is pending."""
    ensure_attempts(conn)
    names = [p for (_, pl, p) in stopped_packages(conn, project) if pl == plan]
    pending = stopping_embedded(conn, project, plan)
    names += [p for (_, p, _) in pending if p not in names]
    if not names:
        return ''
    text = 'The owner stopped these packages: %s. End their Agents now (a subagent Agent too), start none of them again and leave them open; the owner resumes them.\n' % ', '.join(names)
    if pending:
        text += ('Stop pending until you confirm the end of these subagent Agents: %s. They keep their Agent place until then. As soon as a subagent Agent '
                 'has ended, confirm it with bzz agent result --outcome interrupted for its package.\n') % ', '.join('%s (%s)' % (n, p) for (_, p, n) in pending)
    return text


OPEN_BEES_FILE = 'OPEN-AGENTS'


def open_bees_text(conn, project, plan, queen):
    """The text of OPEN-AGENTS for the Queen `queen`: the subagent Agents of earlier Queens of the plan whose end is not confirmed; empty when none."""
    inherited = [r for r in unconfirmed_embedded(conn, project, plan) if not own_embedded(r, queen)]
    if not inherited:
        return ''
    return ('These subagent Agents of an earlier Queen of your plan have no confirmed end: %s. Each keeps its Agent place and its package gets no new Agent. '
            'Do not start them again. A subagent Agent counts as ended when its Queen has ended: HaiveQueen itself closes its row in its next round as interrupted '
            '(no attempt, no failure, the package state stays); an Agent still listed here is not closed yet, so leave it open. '
            'Once it is closed, look for the result file of such an Agent; when its work is on the branch report the package with bzz agent result --outcome succeeded, '
            'otherwise start a new Agent for the package.\n') % ', '.join(
        '%s (%s, Queen %s)' % (r['name'], r['package'], r.get('queen') or 'unknown') for r in inherited)


def write_stop_notice(conn, project, plan):
    """Tell every running Queen of the plan (file STOP-PACKAGES in her run folder) which packages the owner stopped: she ends their subagent Agents,
    confirms each ended subagent Agent and starts none of them again. An embedded Agent has no process of its own, so only its Queen can end it.
    Without a stopped package or a pending stop the file is removed; an unchanged file is not written again."""
    from . import platform as osplat
    text = stop_notice_text(conn, project, plan)
    for queen in runtime.list_agents(conn, project, 'active'):
        if queen.get('plan_path') != plan or queen.get('phase') != 'queen' or queen.get('status') not in runtime.ACTIVE_STATUSES:
            continue
        for name, body in ((STOP_NOTICE_FILE, text), (OPEN_BEES_FILE, open_bees_text(conn, project, plan, queen))):
            target = osplat.runs_dir() / project / queen['name'] / name
            if body:
                if not target.is_file() or target.read_text(encoding='utf-8') != body:
                    target.parent.mkdir(parents=True, exist_ok=True)
                    target.write_text(body, encoding='utf-8')
            elif target.exists():
                target.unlink()


def after_queen_start(conn, project, plan):
    """A new Queen of the plan has started. The embedded Agents of her predecessor stay open with their Agent places: a Queen ending proves no end of her
    native Agents. The new Queen is told of them (OPEN-AGENTS) and of stopped packages (STOP-PACKAGES)."""
    inherited = unconfirmed_embedded(conn, project, plan)
    if inherited:
        runtime.append_event(conn, project, 'New Queen of %s: subagent Agents %s keep their Agent places until their end is confirmed' % (
            plan, ', '.join('%s (%s)' % (r['name'], r['package']) for r in inherited)), kind='note', mirror='none')
    write_stop_notice(conn, project, plan)


def stop_package(conn, project, plan, package):
    """Mark the package as stopped by the owner; no Agent of it starts again until resume_package.

    An open embedded (subagent) Agent of the package has no process HaiveQueen could end: its stop is pending (outcome STOPPING), its row stays open and
    keeps its Agent place, and the running Queen is told to end it and confirm its end; only that confirmation closes the row."""
    ensure_attempts(conn)
    runtime.kv_set(conn, project, STOP_KEY + '%s|%s' % (plan, package), db.now())
    pending = conn.execute('UPDATE embedded_bees SET outcome=? WHERE project=? AND plan_path=? AND package=? AND ended_at IS NULL',
                           (STOPPING, project, plan, package)).rowcount
    text = 'Package %s stopped by the owner; it stays stopped until the owner resumes it' % package
    if pending:
        text += '. Its subagent Agent keeps its Agent place until its Queen confirms that it ended'
    runtime.append_event(conn, project, text, kind='note', mirror='none')
    write_stop_notice(conn, project, plan)


def package_stopped(conn, project, plan, package):
    """True when the owner stopped the package and has not resumed it."""
    return bool(runtime.kv_get(conn, project, STOP_KEY + '%s|%s' % (plan, package)))


def stopped_packages(conn, project=None):
    """(project, plan, package) of every package the owner stopped and has not resumed, sorted."""
    sql, args = "SELECT project, key FROM kv WHERE substr(key,1,?)=? AND value!=''", [len(STOP_KEY), STOP_KEY]
    if project:
        sql += ' AND project=?'
        args.append(project)
    found = []
    for row in conn.execute(sql, args):
        plan, _, package = row[1][len(STOP_KEY):].rpartition('|')
        if plan and package:
            found.append((row[0], plan, package))
    return sorted(found)


def resume_package(conn, project, plan, package):
    """Release a stopped package again (GUI action "Resume package"); its Queen may start an Agent for it again."""
    if not package_stopped(conn, project, plan, package):
        raise ValueError('the package %s is not stopped' % package)
    runtime.kv_delete(conn, project, STOP_KEY + '%s|%s' % (plan, package))
    runtime.append_event(conn, project, 'Package %s resumed by the owner' % package, kind='note', mirror='none')
    write_stop_notice(conn, project, plan)


# Packages a running Queen could start now but has not started: their dependencies are done and Bee places are free.
NOTICE_FILE = 'START-PACKAGES'
STARTABLE_KEY = 'hive-startable:'


def startable_not_started(env, plan, alive=None):
    """Ids of the packages of the plan that are not finished, waiting, stopped or worked by a live or embedded Agent and whose dependencies are done.

    a package whose Agent only waits for a provider (waiting, reason provider) counts as startable: its Agent starts again as soon as an
    eligible provider has a free slot, so the round reserves that slot for it and tells the Queen to start it."""
    from . import agent_process
    ensure_attempts(env.conn)
    states = {r[0]: ('' if r[1] == 'waiting' and r[2] == 'provider' else r[1])
              for r in env.conn.execute('SELECT package,state,reason FROM package_progress WHERE project=? AND plan_path=?', (env.name, plan))}
    worked = {a.get('package') for a in runtime.list_agents(env.conn, env.name, 'active')
              if a.get('plan_path') == plan and a.get('phase') == 'bee' and agent_process.counts(a, alive)}
    worked |= {r[0] for r in env.conn.execute('SELECT package FROM embedded_bees WHERE project=? AND plan_path=? AND ended_at IS NULL', (env.name, plan))}
    locked = {p['id'] for p in runtime.list_packages(env.conn, env.name, plan) if p['locked'] or p['phase'] == 'done'}
    packages = env.packages(plan)
    ids = {p['id'] for p in packages}
    found = []
    for p in packages:
        if p['id'] in worked or p['id'] in locked or states.get(p['id']) in ('done', 'implemented', 'waiting'):
            continue
        if package_done(env.conn, env.name, plan, p['id']) or package_stopped(env.conn, env.name, plan, p['id']):
            continue
        if all(dep in ids and (package_done(env.conn, env.name, plan, dep) or states.get(dep) in ('done', 'implemented')) for dep in p['depends']):
            found.append(p['id'])
    return found


def reservation_route(env, plan, package, hive_provider, free, excluded=(), limited=()):
    """The provider whose slot an Agent of `package` would take now, by the rule of bzz agent start (class, capabilities, the Queen's own or a
    local provider directly, else the normal provider choice); None when no eligible provider has a free slot. Failure escalation is not
    foreseen: the plan class, or the stored class of an escalated package, decides."""
    from . import routine
    row = next((p for p in env.packages(plan) if p['id'] == package), None)
    if not row:
        return None
    need = routine.package_needs_for(env, plan, package)
    providers = [p for p in env.providers if p['name'] not in excluded]
    allowed = capable_providers(providers, need) if need else [p for p in providers if (p.get('endpoint') or p.get('server')) or p['name'] == hive_provider]
    cls = row['cls']
    stored = env.conn.execute('SELECT class FROM bee_assignments WHERE project=? AND plan_path=? AND package=?', (env.name, plan, package)).fetchone()
    if stored and scheduler.class_index(stored[0]) > scheduler.class_index(cls):
        cls = stored[0]
    has_class = lambda group: next((c for c in scheduler.CLASS_NAMES[scheduler.class_index(cls):] if any((p.get('classes', {}).get(c) or {}).get('model') for p in group)), None)
    base = has_class(allowed)
    other = False
    if base is None and not need:
        base, other = has_class(providers), True
    if base is None:
        return None
    route = select_route(env.providers, free, hive_provider, base, need, excluded, other or scheduler.class_index(base) != scheduler.class_index(row['cls']), limited, conn=env.conn)
    return route[0] if route else None


def free_bee_places(env, plan, free_slots, alive=None):
    """Agent places the Queen of the plan has free now: beesPerQueen minus her live and embedded Agents, at most the free provider slots."""
    from . import agent_process
    ensure_attempts(env.conn)
    live = sum(1 for a in runtime.list_agents(env.conn, env.name, 'active')
               if a.get('plan_path') == plan and a.get('phase') == 'bee' and agent_process.counts(a, alive))
    live += env.conn.execute('SELECT COUNT(*) FROM embedded_bees WHERE project=? AND plan_path=? AND ended_at IS NULL', (env.name, plan)).fetchone()[0]
    return max(0, min(int(env.settings.get('beesPerQueen', 3)) - live, int(free_slots)))


def notify_startable(env, plan, queen, free_slots, alive=None):
    """Tell the running Queen in this round which packages to start now (file START-PACKAGES in her run folder) and store them for the dashboard.

    Returns the package ids named; none when no package waits, no Agent place is free or the Queen has her HANDOFF file (the file and the stored list are then cleared)."""
    target = env.run_dir(queen['name']) / NOTICE_FILE
    handoff = (target.parent / 'HANDOFF').exists()   # a Queen at her context limit starts no new Bees
    places = 0 if handoff else free_bee_places(env, plan, free_slots, alive)
    names = startable_not_started(env, plan, alive)[:places] if places else []
    runtime.kv_set(env.conn, env.name, STARTABLE_KEY + plan, json.dumps(names))
    if names:
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text('Start these packages now, each with its own Agent (%d Agent place(s) free): %s\n' % (places, ', '.join(names)), encoding='utf-8')
        env.log('Queen %s told to start the startable packages %s' % (queen['name'], ', '.join(names)))
    elif target.exists():
        target.unlink()
    return names


def startable_map(conn):
    """{(project, plan): [package ids]} of the startable packages a running Queen has not started yet (as stored by the last round)."""
    found = {}
    for row in conn.execute("SELECT project, key, value FROM kv WHERE substr(key,1,?)=?", (len(STARTABLE_KEY), STARTABLE_KEY)):
        try:
            names = json.loads(row[2] or '[]')
        except ValueError:
            continue
        if names:
            found[(row[0], row[1][len(STARTABLE_KEY):])] = list(names)
    return found


# Why a provider is no wait target.
def never_available(provider):
    """'' when the provider can become free again; else why not: disabled, or 0 configured slots (an offline provider keeps its configured slots)."""
    if not provider.get('enabled', True) or provider.get('unavailable') == 'disabled':
        return 'it is disabled'
    if int(provider.get('configuredSlots', provider.get('slots', 0)) or 0) < 1:
        return 'it has 0 configured slots'
    return ''


def wait_refusal(env, plan, provider) -> str:
    """The refusal text of `bzz agent wait` for a disabled provider or one with 0 configured slots, else ''. When an open package needs a capability that
    only such providers offer, the text names the capability and those providers and tells the Queen to file one owner question instead of waiting."""
    from . import routine
    why = never_available(provider)
    if not why:
        return ''
    text = 'provider %s cannot be waited for: %s' % (provider['name'], why)
    excluded = env.settings.get('excludedProviders', [])
    ensure_attempts(env.conn)
    states = {r[0]: r[1] for r in env.conn.execute('SELECT package,state FROM package_progress WHERE project=? AND plan_path=?', (env.name, plan))}
    for p in env.packages(plan):
        if states.get(p['id']) in ('done', 'implemented') or package_done(env.conn, env.name, plan, p['id']):
            continue
        need = routine.package_needs_for(env, plan, p['id'])
        capable = [c for c in capable_providers(env.providers, need) if c['name'] not in excluded] if need else []
        if capable and all(never_available(c) for c in capable):
            return ('%s; package %s needs %s, which only %s offer(s) and no provider that can become free has it: do not wait, file one owner question '
                    'with bzz ask naming the capability and these providers, write a handoff and end with WAITING_FOR_OWNER; the plan waits for the answer'
                    % (text, p['id'], ', '.join(need), ', '.join(c['name'] for c in capable)))
    return text


# Packages of a plan that can still start although one provider is missing.
def runnable_packages(env, plan, provider) -> list:
    """Ids of the packages that are not finished or waiting, whose dependencies are done and that do not need a capability only the missing provider has (a capability another provider offers too does not count): the Queen must work them before the Plan waits."""
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
    """Persist the provider a Plan is waiting for."""
    ensure_attempts(conn)
    conn.execute('CREATE TABLE IF NOT EXISTS bee_provider_wait (project TEXT NOT NULL, plan_path TEXT NOT NULL, provider TEXT NOT NULL, PRIMARY KEY(project,plan_path))')
    with db.transaction(conn):
        conn.execute('INSERT INTO bee_provider_wait VALUES (?,?,?) ON CONFLICT(project,plan_path) DO UPDATE SET provider=excluded.provider', (project, plan, provider))
        conn.execute('UPDATE hives SET state=?,reset_at=? WHERE project=? AND plan_path=?', ('codex_wait' if (provider == 'codex' if is_codex is None else is_codex) else 'provider_wait', reset_at, project, plan))
        conn.execute('DELETE FROM hive_integration WHERE project=? AND plan_path=?', (project, plan))


# A provider other than the awaited one that can take a waiting package now.
def other_provider_for_waiting(env, plan, waited, free, excluded=()):
    """Name of a provider other than `waited` that is usable now (enabled, online, not excluded, no limit used up, a free slot) and offers the
    capabilities of at least one unfinished package whose dependencies are done; None when only the awaited provider can help.
    The next Queen needs one slot to start; her Agent takes a slot only when it starts."""
    from . import routine
    ensure_attempts(env.conn)
    usable = [p for p in env.providers if p['name'] not in excluded and not p.get('unavailable') and p.get('slots', 1) > 0
              and free.get(p['name'], 0) > 0 and not scheduler.reserve_zero(p, env.conn)]   # a used-up window (reserve 0) blocks the provider
    others = [p for p in usable if p['name'] != waited]
    if not others:
        return None
    states = {r[0]: r[1] for r in env.conn.execute('SELECT package,state FROM package_progress WHERE project=? AND plan_path=?', (env.name, plan))}
    packages = env.packages(plan)
    ids = {p['id'] for p in packages}
    for p in packages:
        if states.get(p['id']) in ('done', 'implemented') or package_done(env.conn, env.name, plan, p['id']):
            continue
        if not all(dep in ids and package_done(env.conn, env.name, plan, dep) for dep in p['depends']):
            continue
        capable = capable_providers(others, routine.package_needs_for(env, plan, p['id']) or [])
        if capable:
            return by_reserve(capable, env.providers, env.conn)[0]['name']
    return None


def by_reserve(candidates, providers, conn=None) -> list:
    """`candidates` in the order of the normal provider choice: providers without a limit first (list order among them), then the best quota reserve from both usage windows, equal reserves go to a local provider first, then the order of the settings."""
    from . import scheduler
    order = [p['name'] for p in providers]
    now = time.time()   # one clock reading for all candidates, so equal usage gives exactly equal reserves

    def key(p):
        resv = scheduler.provider_reserve(p, conn, now=now)   # the quota reserve from both usage windows; None without limit (+99 %)
        unlimited = resv is None
        # the raw reserve decides (never a rounded one); equal reserves, also two providers without a limit (+99 %), go to a local provider first, then the settings order
        return (not unlimited, 0 if unlimited else -resv, not servers.is_local(p), order.index(p['name']) if p['name'] in order else len(order))
    return sorted(candidates, key=key)


# Wake a waiting Hive only when its provider has a slot and no limit, or when another provider can take a waiting package.
def provider_available(conn, project, plan, provider, free, limited, other=None):
    """Wake the Plan when its waiting provider is usable or, with `other`, when another provider can take a waiting package."""
    if (free <= 0 or limited) and not other:
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
def report_attempt(env, plan, package, cls, outcome, attempt=None, open_requirements=None, error='', blocked='', force_failure=False):
    """Record an outcome and ask once after the highest class fails repeatedly. `blocked` (question:<id>, build, owner, package or provider) means the attempt only waits for that: nothing is counted and the package is set to waiting."""
    attempt, foreign = package_branch_violation(env, plan, package, attempt)
    if foreign:
        outcome, error, blocked, force_failure = 'failed', 'foreign branch commit: ' + foreign, '', True
    had_open = bool(unconfirmed_embedded(env.conn, env.name, plan, package))
    if close_embedded(env.conn, env.name, plan, package, 'blocked' if blocked else outcome):   # the embedded Bee of this package has reported
        runtime.append_event(env.conn, env.name, 'The stopped subagent Agent of package %s has ended; its Agent place is free' % package, kind='note', mirror='none')
    if had_open:   # the notices of the running Queens no longer name it
        write_stop_notice(env.conn, env.name, plan)
    if blocked:
        set_package_state(env.conn, env.name, plan, package, 'waiting', 'queen', blocked)
        return
    row = next((p for p in env.packages(plan) if p['id'] == package), None)
    record_attempt(env.conn, env.name, plan, package, cls, outcome, attempt, open_requirements, error, row['verify'] if row else 'code', force_failure)
    if outcome == 'succeeded':
        env.conn.execute('DELETE FROM bee_assignments WHERE project=? AND plan_path=? AND package=?', (env.name, plan, package))
    threshold = int(env.settings.get('escalateAfter', 3))
    excluded = env.settings.get('excludedProviders', [])
    if outcome == 'failed' and next_attempt(env.conn, env.name, plan, package, cls, escalation_providers(env, plan, package), excluded, threshold) is None:
        language = roles.language(env.settings)
        text = tr(FAILURE_QUESTION, language=language)
        if not any(q.get('question') == text for q in runtime.list_questions(env.conn, env.name, status='open', plan=plan, item=package)):
            db.add_question(env.conn, env.name, plan, package, text, tr('Review the failed attempts and choose the next approach.', language=language))


def prepare_agent_worktree(env, plan, package):
    """Reuse a package checkout or create its branch from the current plan tip."""
    import subprocess
    from pathlib import Path
    from . import routine
    ensure_attempts(env.conn)
    entries = worktree_entries(env)
    resolved = plan_worktree(env, plan)
    if entries is None or not resolved:
        env.log('Agent worktree cannot be prepared: missing plan header or Git worktree list')
        return None
    saved = env.conn.execute('SELECT cwd FROM bee_worktrees WHERE project=? AND plan_path=? AND package=?', (env.name, plan, package)).fetchone()
    if saved:
        found = next(((d, b) for d, b in entries if same_folder(d, saved[0])), None)
        if found and found[1]:
            known = env.conn.execute('SELECT branch FROM bee_worktree_branches WHERE project=? AND plan_path=? AND package=?', (env.name, plan, package)).fetchone()
            if known and known[0] != found[1]:
                env.log('folder %s holds branch %s; package %s expects branch %s' % (saved[0], found[1], package, known[0]))
                return None
            env.conn.execute('INSERT OR IGNORE INTO bee_worktree_branches VALUES (?,?,?,?)', (env.name, plan, package, found[1]))
            return str(Path(found[0])), found[1]
        env.log('Agent worktree %s is unavailable or has no branch' % saved[0])
        return None
    for previous in reversed(runtime.list_agents(env.conn, env.name, 'all')):
        if (previous.get('plan_path') == plan and previous.get('package') == package and previous.get('phase') == 'bee'
                and previous.get('cwd') and valid_bee_worktree(env, previous['cwd'], None)):
            found = next(((d, b) for d, b in entries if previous.get('cwd') and same_folder(d, previous['cwd']) and b), None)
            if found:
                env.conn.execute('INSERT OR IGNORE INTO bee_worktrees VALUES (?,?,?,?)', (env.name, plan, package, found[0]))
                env.conn.execute('INSERT OR IGNORE INTO bee_worktree_branches VALUES (?,?,?,?)', (env.name, plan, package, found[1]))
                return str(Path(found[0])), found[1]
    branch = resolved[0] + '-' + package.lower()
    folder = Path(routine.new_worktree(env, routine.plan_tag(env, plan).lower() + '-' + package.lower()))
    here = next(((d, b) for d, b in entries if same_folder(d, folder)), None)
    if here and here[1] != branch:
        env.log('folder %s holds branch %s; package %s expects branch %s' % (folder, here[1] or '(none)', package, branch))
        return None
    elsewhere = next(((d, b) for d, b in entries if b == branch), None)
    if elsewhere:
        folder = Path(elsewhere[0])
    elif not here:
        if folder.exists():
            env.log('folder %s holds branch (none); package %s expects branch %s' % (folder, package, branch))
            return None
        git = ['git', '-C', str(env.code_repo)]
        exists = subprocess.run(git + ['show-ref', '--verify', '--quiet', 'refs/heads/' + branch], capture_output=True).returncode == 0
        args = ['worktree', 'add', str(folder), branch] if exists else ['worktree', 'add', '-b', branch, str(folder), resolved[0]]
        result = subprocess.run(git + args, capture_output=True, text=True, encoding='utf-8', errors='replace')
        if result.returncode:
            env.log('Agent worktree cannot be prepared: ' + result.stderr.strip())
            return None
    env.conn.execute('INSERT OR IGNORE INTO bee_worktrees VALUES (?,?,?,?)', (env.name, plan, package, str(folder)))
    env.conn.execute('INSERT OR IGNORE INTO bee_worktree_branches VALUES (?,?,?,?)', (env.name, plan, package, branch))
    return str(folder), branch


def record_agent_branch_start(env, name, cwd, branch):
    from . import branchguard
    import uuid
    folder = env.run_dir(name) / ('git-guard-' + uuid.uuid4().hex)
    overrides = branchguard.prepare(folder, cwd, branch)
    runtime.kv_delete(env.conn, env.name, 'agent-violation:' + name)
    runtime.kv_delete(env.conn, env.name, 'agent-violation-reported:' + name)
    runtime.kv_set(env.conn, env.name, 'agent-branch:' + name, json.dumps({'cwd': cwd, 'branch': branch, 'guard': str(folder), 'environment': overrides}))
    return overrides


def agent_branch_violation(env, name):
    return branch_violation(env.conn, env.name, name)


def package_branch_violation(env, plan, package, attempt):
    """An embedded Agent's evidence follows its identity even if the Queen uses a result alias."""
    for row in unconfirmed_embedded(env.conn, env.name, plan, package):
        foreign = branch_violation(env.conn, env.name, row['name'])
        if foreign:
            return row['name'], foreign
    return attempt, branch_violation(env.conn, env.name, attempt) if attempt else ''


def branch_violation(conn, project, name):
    from . import branchguard
    saved = runtime.kv_get(conn, project, 'agent-violation:' + name)
    if saved:
        return saved
    raw = runtime.kv_get(conn, project, 'agent-branch:' + name)
    if not raw:
        return ''
    data = json.loads(raw)
    if data.get('guard'):
        return branchguard.violation(data['guard'])
    return ''


def enforce_agent_end(conn, project, row):
    """Check every terminal path, including missing runners and embedded Queen exits."""
    name = row['name']
    foreign = branch_violation(conn, project, name)
    if not foreign:
        return ''
    runtime.kv_set(conn, project, 'agent-violation:' + name, foreign)
    text = 'Foreign branch commit: %s; this package attempt failed.' % foreign
    raw = runtime.kv_get(conn, project, 'agent-branch:' + name)
    data = json.loads(raw) if raw else {}
    if data.get('guard'):
        from pathlib import Path
        (Path(data['guard']).parent / 'branch-violation.txt').write_text(text + '\n', encoding='utf-8')
    record_attempt(conn, project, row['plan_path'], row['package'], row.get('class') or 'medium',
                   'failed', name, error=text, force_failure=True)
    if not runtime.kv_get(conn, project, 'agent-violation-reported:' + name):
        runtime.append_event(conn, project, text, kind='note', agent=name, mirror='none', data={'foreign_branch': foreign})
        runtime.kv_set(conn, project, 'agent-violation-reported:' + name, foreign)
    return foreign


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
    """Log and store the reason an Agent start was refused and return False, so a caller can write `return refuse(...)`."""
    global LAST_REFUSAL
    LAST_REFUSAL = reason
    env.log('Agent start refused for package %s: %s' % (package, reason))
    return False


# The reason no Bee may start while an update waits for running agents.
def drain_refusal(draining=None):
    """While the update worker waits for running agents no Agent starts, an embedded one included; the reason, else None."""
    from . import updates
    if (draining or updates.draining)():
        return 'an update of HaiveQueen waits for the running agents to end; no Agent starts until it has restarted'
    return None


# Start a separate Bee process after validating context, memory, slots and routing.
def start_process(env, plan, package, name, cls, task, steps, paths, rules, cwd=None, no_subagents=False):
    """Start an eligible Agent through the agent runner. Every package goes to an Agent, the Queen never works one herself; the provider setting beeMode decides whether
    the Agent of the Queen's own provider is an embedded subagent or a separate process.
    A refusal returns False and leaves its reason in LAST_REFUSAL."""
    global LAST_REFUSAL
    LAST_REFUSAL = ''
    from pathlib import Path
    from . import hives, loop, routine
    with loop.hive_schedule_lock():
        check_dependencies(env, plan, package)
        draining = drain_refusal()
        if draining:
            return refuse(env, package, draining)
        hive = hives.get(env.conn, env.name, plan)
        if not hive or not hives.can_start_bee(env.conn, env.name, plan, False, env.settings.get('contextLimitTokens', 300000)):
            return refuse(env, package, 'the Plan may not start an Agent now (Plan state %s, context limit or HANDOFF reached)' % (hive['state'] if hive else 'missing'))
        active = runtime.list_agents(env.conn, None, 'active')
        queen = next((a for a in active if a.get('project') == env.name and a.get('plan_path') == plan and a.get('phase') == 'queen' and a.get('status') in ('running', 'starting')), None)
        if not queen:
            return refuse(env, package, 'no running Queen of this plan')
        held = {str(a.get('person') or a.get('name')) for a in active if a.get('status') in ('running', 'starting')} | open_embedded_names(env.conn, (env.name, plan, package))
        if re.sub(r'-\d+$', '', str(name)) in held:   # a name is held by one Queen or Bee at a time
            return refuse(env, package, 'the name %s is already held by a running Queen or Agent; use the free name %s' % (name, loop.agent_identity(env.conn)['Person']))
        from . import platform as osplat
        from . import agent_process
        running = [{**a, 'status': 'running'} for a in active if agent_process.counts(a)]
        ensure_attempts(env.conn)
        embedded_count = env.conn.execute('SELECT COUNT(*) FROM embedded_bees WHERE project=? AND plan_path=? AND ended_at IS NULL', (env.name, plan)).fetchone()[0]
        if embedded_count + sum(1 for a in running if a.get('project') == env.name and a.get('plan_path') == plan and a.get('phase') == 'bee') >= int(env.settings.get('beesPerQueen', 3)):
            return refuse(env, package, 'the Queen already has %s Agents running' % env.settings.get('beesPerQueen', 3))
        plan_row = next((p for p in runtime.list_plans(env.conn, env.name) if p['path'] == plan), None)
        if plan_row and plan_row['status'] in plan_mod.CUT_STATUSES:
            return refuse(env, package, 'the plan is not cut yet: write its packages into the header and run bzz plan check first')
        package_row = next((p for p in env.packages(plan) if p['id'] == package), None)
        if not package_row:
            return refuse(env, package, 'the package is not in the plan')
        if package_stopped(env.conn, env.name, plan, package):
            return refuse(env, package, 'the owner stopped this package; it starts again only after the owner resumes it')
        pending = stopping_embedded(env.conn, env.name, plan, package)
        if pending:
            return refuse(env, package, 'the stopped subagent Agent %s of this package has not confirmed its end; once it has ended, report it with bzz agent result --outcome interrupted' % pending[0][2])
        unconfirmed = unconfirmed_embedded(env.conn, env.name, plan, package)
        if unconfirmed:   # no replacement while the earlier subagent Bee may still run
            if own_embedded(unconfirmed[0], queen):
                return refuse(env, package, 'the subagent Agent %s of this package has not confirmed its end; record its result with bzz agent result first' % unconfirmed[0]['name'])
            return refuse(env, package, 'the subagent Agent %s of the earlier Queen %s has not confirmed its end; record its result with bzz agent result --ended-evidence first' % (
                unconfirmed[0]['name'], unconfirmed[0].get('queen') or 'unknown'))
        requested_class = package_row['cls']
        threshold = int(env.settings.get('escalateAfter', 3))
        excluded = env.settings.get('excludedProviders', [])
        need = routine.package_needs_for(env, plan, package)
        allowed = [p for p in env.providers if p['name'] not in excluded and
                   (p in capable_providers(env.providers, need) if need else
                    ((p.get('endpoint') or p.get('server')) or p['name'] == hive['provider']))]
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
        if retry_epoch(env.conn, env.name, plan, package) is not None:
            offered = escalation_providers(env, plan, package)
            while next_class(offered, current, excluded):
                current = next_class(offered, current, excluded)
            cls = current   # the owner's answer directs the retry to the highest offered class
        queen_class = env.settings.get('queenClass', 'medium')
        if (scheduler.class_index(requested_class) < scheduler.class_index(queen_class) and
                failure_count(env.conn, env.name, plan, package, queen_class) >= threshold):
            current = next_class(escalation_providers(env, plan, package), queen_class, excluded)
            if current is None:
                return refuse(env, package, 'the package failed %d times at the Queen class %s and no higher class is offered' % (threshold, queen_class))
        while failure_count(env.conn, env.name, plan, package, current) >= threshold:
            previous = current
            current = next_class(escalation_providers(env, plan, package), current, excluded)
            if current is None:
                return refuse(env, package, 'the package failed %d times and no higher class is offered' % threshold)
            if scheduler.class_index(previous) < scheduler.class_index(queen_class) <= scheduler.class_index(current):
                env.log('Package %s raised to Queen class %s after failures' % (package, current))
        ensure_attempts(env.conn)
        stored = env.conn.execute('SELECT class FROM bee_assignments WHERE project=? AND plan_path=? AND package=?', (env.name, plan, package)).fetchone()
        if stored and stored[0] == cls and scheduler.class_index(cls) > scheduler.class_index(current):
            current = cls   # the package was already escalated to this higher class: keep it even when the failure counters were reset
        if scheduler.class_index(cls) != scheduler.class_index(current) and not (current == base_class and scheduler.class_index(cls) == scheduler.class_index(requested_class)):
            return refuse(env, package, 'class %s was requested but the system computes class %s (plan class %s, no stored escalation)' % (cls, current, requested_class))
        queen_provider = scheduler.get_provider(env.providers, hive['provider'])
        embedded = bool(queen_provider and queen_provider.get('beeMode') == 'subagent' and not (queen_provider.get('endpoint') or queen_provider.get('server')) and not no_subagents)   # the Queen's provider runs her Bees as embedded subagents
        free = scheduler.provider_free(env.providers, running, env.conn)   # A subagent Bee needs a free slot of the Queen's provider like a Bee process
        route_free = dict(free)
        limited = [p['name'] for p in env.providers if scheduler.reserve_zero(p, env.conn)]   # a used-up window (reserve 0) blocks the provider
        ensure_attempts(env.conn)
        if embedded:   # a subagent Bee takes a free local provider first, else stays on the Queen's provider, else another one as for other Bees; an escalated package takes the best reserve among them
            route = select_route(env.providers, route_free, hive['provider'], current, need, excluded, current != base_class or use_other_provider, limited, conn=env.conn)
        else:   # a process Bee is placed by the normal provider choice like a Queen; the stored assignment keeps only the class (above), the provider is chosen again here
            route = select_route(env.providers, route_free, hive['provider'], current, need, excluded, True, limited, conn=env.conn)
        if not route:
            return refuse(env, package, 'no provider with a free slot offers class %s' % current)
        provider, selected_class = route
        env.conn.execute('INSERT INTO bee_assignments(project,plan_path,package,class,provider) VALUES(?,?,?,?,?) ON CONFLICT(project,plan_path,package) DO UPDATE SET class=excluded.class,provider=excluded.provider', (env.name, plan, package, selected_class, provider))
        pick = scheduler.get_provider(env.providers, provider)
        pair = prepare_agent_worktree(env, plan, package)
        if not pair:
            return refuse(env, package, 'the package worktree could not be prepared; see the round log')
        cwd, branch = pair
        if not valid_bee_worktree(env, cwd, queen.get('cwd')):
            return refuse(env, package, 'the package worktree is not a separate checkout of this repository')
        # The runner otherwise adds a suffix after the guard was stored. Choose
        # the final attempt identity first, for process and embedded Agents alike.
        existing_names = {a['name'] for a in runtime.list_agents(env.conn, env.name, 'all')}
        existing_names.update(r[0] for r in env.conn.execute('SELECT name FROM embedded_bees WHERE project=?', (env.name,)))
        requested, suffix = name, 1
        while name in existing_names:
            suffix += 1
            name = '%s-%d' % (requested, suffix)
        git_environment = record_agent_branch_start(env, name, cwd, branch)
        if provider == hive['provider'] and embedded:
            if cwd:
                if Path(cwd).exists() and not valid_bee_worktree(env, cwd, queen.get('cwd')):
                    return refuse(env, package, 'the worktree %s is not a valid separate worktree with a branch of this repository' % cwd)
                env.conn.execute('INSERT OR IGNORE INTO bee_worktrees VALUES (?,?,?,?)', (env.name, plan, package, str(Path(cwd).absolute())))
                cwd = env.conn.execute('SELECT cwd FROM bee_worktrees WHERE project=? AND plan_path=? AND package=?', (env.name, plan, package)).fetchone()[0]
            entry = (pick.get('classes') or {}).get(selected_class) or {}
            record_embedded_start(env.conn, env.name, plan, package, name, selected_class, provider, str(entry.get('model') or ''), str(entry.get('effort') or ''), queen['name'])
            record_step_count(env.conn, env.name, plan, package, len(steps))
            # The subagent gets the same Bee rules as a Bee process: the Queen hands this prompt on as the task of the subagent.
            prompt = roles.role_text('bee-start', {'language': roles.language(env.settings)}) + '\n\n' + bee_prompt(name, package, task, steps, paths, rules, buildjobs.rule_text(env.settings.get('buildJobs')))
            prompt += '\n\nAssigned worktree: %s; assigned branch: %s. Use the returned environment for every Git command in this Agent, including commands in other worktrees. Environment: %s' % (cwd, branch, json.dumps(git_environment))
            return {'kind': 'subagent', 'name': name, 'provider': provider, 'class': selected_class, 'cwd': cwd, 'branch': branch, 'environment': git_environment, 'prompt': prompt}
        if not hives.can_start_bee(env.conn, env.name, plan, True, env.settings.get('contextLimitTokens', 300000)) or loop.ram_gb() < loop._min_ram(env) or free.get(provider, 0) <= 0:
            return refuse(env, package, 'an Agent process may not start now (starts paused or stopped, too little free RAM, or no free slot of provider %s)' % provider)
        path = Path(cwd or '')
        if not cwd or not valid_bee_worktree(env, path, queen.get('cwd')):
            return refuse(env, package, 'no valid separate worktree for the Agent was given')
        existing = [a['name'] for a in runtime.list_agents(env.conn, env.name, 'all')]
        state = {'names': existing, 'busy': [], 'plan_status': {scheduler.norm_plan(plan): 'RUNNING'}, 'by_path': {scheduler.norm_plan(plan): {'path': plan}}}
        ctx = {'env': env, 'state': state, 'pool': loop.Pool(free, 0), 'report': [], 'dry': False, 'settle': 0}
        action = {'name': name, 'type': provider, 'phase': 'bee', 'plan': plan, 'package': package, 'cwd': str(path), 'prompt': bee_prompt(name, package, task, steps, paths, rules, buildjobs.rule_text(env.settings.get('buildJobs'))), 'class': selected_class}
        action['prompt'] += '\n\nAssigned worktree: %s; assigned branch: %s.' % (cwd, branch)
        if not loop._do_start(ctx, action, forced_pick=pick):
            return refuse(env, package, 'the agent runner could not start the Agent process')
        env.conn.execute('INSERT OR IGNORE INTO bee_worktrees VALUES (?,?,?,?)', (env.name, plan, package, str(path.absolute())))
        record_step_count(env.conn, env.name, plan, package, len(steps))
        return {'kind': 'process', 'name': name, 'provider': provider, 'class': selected_class, 'cwd': str(path.absolute()), 'branch': branch}


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


# The branch HaiveQueen proposes to the cut Queen when the plan text names none.
def derived_branch(plan):
    """The plan file name in lower case without -PLAN and .md (docs/plans/Feature-X-PLAN.md gives feature-x)."""
    from pathlib import Path
    return re.sub(r'-plan$', '', Path(plan).stem, flags=re.I).lower()


def same_folder(a, b):
    """True when both paths name the same folder (case, separators and links ignored)."""
    import os
    return os.path.normcase(os.path.realpath(str(a))) == os.path.normcase(os.path.realpath(str(b)))


def cut_default(env, plan):
    """The default rule of the cut: (branch, worktree folder). The owner's choice of a new branch wins; else the derived branch in the project's worktree location."""
    from pathlib import Path
    from . import routine
    conn = getattr(env, 'conn', None)
    branch = (runtime.kv_get(conn, env.name, NEW_BRANCH_KEY + plan) if conn is not None else None) or derived_branch(plan)
    return branch, Path(routine.new_worktree(env, scheduler.to_slug(branch)))


def plan_worktree(env, plan):
    """The plan branch and worktree folder from the two fields `branch` and `worktree` of the plan header, or None when the header lacks one.

    The plan text, the state file and the file name are never searched."""
    from pathlib import Path
    parsed = plan_mod.load_plan(str(env.workspace / plan))
    if not parsed.branch or not parsed.worktree:
        return None
    return parsed.branch, Path(parsed.worktree).expanduser()


def target_worktree(env, plan):
    """The header's (branch, folder); for a plan still to be cut and without both fields the default rule of the cut."""
    resolved = plan_worktree(env, plan)
    if resolved or plan_mod.load_plan(str(env.workspace / plan)).status not in plan_mod.CUT_STATUSES:
        return resolved
    return cut_default(env, plan)


# The search of earlier versions, used one last time to write the header of a plan that is past the cut.
def legacy_search(env, plan):
    """Read the branch and worktree from the owner's choice, the plan text, the state file or an owner answer, else the derived branch (migration of B-6 only)."""
    import re
    from pathlib import Path
    conn = getattr(env, 'conn', None)
    chosen = runtime.kv_get(conn, env.name, NEW_BRANCH_KEY + plan) if conn is not None else None
    if chosen:   # the owner chose a new branch instead of an existing one
        return chosen, Path(__import__('haivequeen.routine', fromlist=['new_worktree']).new_worktree(env, scheduler.to_slug(chosen)))
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
        if answers:
            lines = (answers[-1].get('answer') or '').strip().splitlines()
            if len(lines) != 2:
                return None
            branch, worktree = (line.strip() for line in lines)
            if not branch or not worktree:
                return None
        else:
            branch = derived_branch(plan)
            if not branch:
                return None
            worktree = None
    return branch, Path(worktree) if worktree else Path(__import__('haivequeen.routine', fromlist=['new_worktree']).new_worktree(env, scheduler.to_slug(branch)))


# Recognize a plan branch already owned by work outside the Beekeeper.
def foreign_plan_branch(env, plan):
    """Leave a queued or existing branch with no run history untouched."""
    import subprocess
    resolved = target_worktree(env, plan)
    if not resolved:
        return False
    branch, _ = resolved
    if runtime.kv_get(env.conn, env.name, TAKEOVER_KEY + plan) == branch:   # the owner said: take over the existing branch
        return False
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


# The plan's branch exists but the plan has no Hive history: the owner decides whether the Hive takes it over or creates a new one.
TAKEOVER_KEY, NEW_BRANCH_KEY, BRANCH_ITEM = 'branch-takeover:', 'plan-branch:', 'branch-takeover'
TAKE_OVER_WORDS = ('take over', 'takeover', 'existing', 'use it', 'übernehmen', 'uebernehmen', 'bestehend')
NEW_WORDS = ('new', 'create', 'neu', 'neuen', 'anlegen')


def branch_choice(answer):
    """'take over', 'new' or '' for an owner answer to the branch question (English protocol words, German still recognised)."""
    text = str(answer or '').strip().lower()
    if any(word in text for word in TAKE_OVER_WORDS):
        return 'take over'
    if any(re.search(r'(?<![a-zäöü])%s(?![a-zäöü])' % re.escape(word), text) for word in NEW_WORDS):
        return 'new'
    return ''


def free_branch_name(env, branch):
    """The first of <branch>-2, <branch>-3, ... that is neither a local nor a remote branch."""
    import subprocess
    remote_name = str(env.settings.get('remoteName') or 'origin')
    n = 2
    while True:
        name = '%s-%d' % (branch, n)
        refs = ('refs/heads/' + name, 'refs/remotes/%s/%s' % (remote_name, name))
        if all(subprocess.run(['git', '-C', str(env.code_repo), 'show-ref', '--verify', '--quiet', ref], capture_output=True).returncode for ref in refs):
            return name
        n += 1


def resolve_existing_branch(env, plan):
    """For a plan whose branch exists without run history: apply the owner's answer, else ask once.

    Returns 'take over' or 'new' when the owner decided (the Plan may start), else '' (the plan waits for the answer)."""
    from .i18n import tr
    branch = target_worktree(env, plan)[0]
    for q in reversed(runtime.list_questions(env.conn, env.name, status='answered', plan=plan, item=BRANCH_ITEM)):
        choice = branch_choice(q.get('answer'))
        if choice == 'take over':
            runtime.kv_set(env.conn, env.name, TAKEOVER_KEY + plan, branch)
            env.log('plan %s: the owner takes over the existing branch %s' % (plan, branch))
            return choice
        if choice == 'new':
            name = free_branch_name(env, branch)
            runtime.kv_set(env.conn, env.name, NEW_BRANCH_KEY + plan, name)
            if plan_worktree(env, plan):   # the header is the only source: the owner's choice goes into its two fields
                from . import routine
                runtime.set_plan_header(env.conn, env.name, plan, name, routine.new_worktree(env, scheduler.to_slug(name)), workspace=env.workspace)
            env.log('plan %s: the owner wants a new branch; %s is created instead of %s' % (plan, name, branch))
            return choice
    if not runtime.list_questions(env.conn, env.name, status='open', plan=plan, item=BRANCH_ITEM):
        language = roles.language(env.settings)
        db.add_question(env.conn, env.name, plan, BRANCH_ITEM,
                        tr('The branch %s of this plan already exists, but the plan has no run history. Take over the existing branch or create a new one?', branch, language=language),
                        tr('Answer "take over" to continue on %s, or "new" to create a new branch from the integration branch.', branch, language=language))
    return ''


def worktree_entries(env):
    """(folder, branch) of every worktree of the code repository ('' for a detached one); None when Git cannot list them."""
    import subprocess
    listing = subprocess.run(['git', '-C', str(env.code_repo), 'worktree', 'list', '--porcelain'], capture_output=True, text=True, encoding='utf-8', errors='replace')
    if listing.returncode:
        return None
    found = []
    for entry in listing.stdout.replace('\r\n', '\n').split('\n\n'):
        fields = entry.splitlines()
        directory = next((line[9:] for line in fields if line.startswith('worktree ')), '')
        branch = next((line[18:] for line in fields if line.startswith('branch refs/heads/')), '')
        if directory:
            found.append((directory, branch))
    return found


# One line when the folder or the branch of the header is taken by something else.
def worktree_conflict(env, plan):
    """'' when the header's worktree folder and branch fit together; else the one line for the dashboard and the round log: the folder, the branch found there and the branch the header expects. Nothing is changed on disk."""
    from pathlib import Path
    resolved = target_worktree(env, plan)
    entries = worktree_entries(env)
    if not resolved or entries is None:
        return ''
    branch, folder = resolved
    here = next((b for d, b in entries if same_folder(d, folder)), None)
    if here is None and Path(folder).exists() and not same_folder(folder, env.code_repo):
        here = ''
    if here is not None and here != branch:
        return 'folder %s holds branch %s; the plan header expects branch %s in it' % (folder, here or '(none)', branch)
    for directory, found in entries:
        if found == branch and not same_folder(directory, folder):
            return 'folder %s holds branch %s; the plan header expects branch %s in folder %s' % (Path(directory), found, branch, folder)
    return ''


# Reuse or create the branch named by the plan header in its worktree folder.
def prepare_queen_worktree(env, plan):
    """Prepare the plan branch in the header's worktree folder without changing the main checkout; None when the header is incomplete or the line of worktree_conflict applies."""
    import subprocess
    resolved = target_worktree(env, plan)
    if resolved is None:
        return None
    branch, target = resolved
    conflict = worktree_conflict(env, plan)
    if conflict:
        env.log('cannot prepare plan worktree: ' + conflict)
        return None
    git = ['git', '-C', str(env.code_repo)]
    if same_folder(target, env.code_repo):
        return None
    if target.exists():
        return str(target) if any(same_folder(d, target) and b == branch for d, b in worktree_entries(env) or []) else None
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


# After the cut: the default worktree and branch the cut Queen started in are removed when the header names others and they are unused.
def cleanup_cut_default(env, plan):
    """Remove the cut's default worktree and branch when the header names another pair and they hold no commit of their own and no change; otherwise keep them and name them in the round log. Done once per plan."""
    import subprocess
    resolved = plan_worktree(env, plan)
    marker = 'cut-cleanup:' + plan
    conn = getattr(env, 'conn', None)
    if not resolved or (conn is not None and runtime.kv_get(conn, env.name, marker)):
        return
    branch, folder = cut_default(env, plan)
    if branch == resolved[0] or same_folder(folder, resolved[1]):
        return

    def run(*args, cwd=None):
        return subprocess.run(['git', '-C', str(cwd or env.code_repo), *args], capture_output=True, text=True, encoding='utf-8', errors='replace')

    registered = any(same_folder(d, folder) for d, _ in worktree_entries(env) or [])
    branch_exists = run('show-ref', '--verify', '--quiet', 'refs/heads/' + branch).returncode == 0
    if not registered and not branch_exists:
        return
    remote_name = str(env.settings.get('remoteName') or 'origin')
    integration = str(env.settings.get('integrationBranch') or 'main')
    base = remote_name + '/' + integration if run('show-ref', '--verify', '--quiet', 'refs/remotes/%s/%s' % (remote_name, integration)).returncode == 0 else integration
    unused = not branch_exists or run('merge-base', '--is-ancestor', 'refs/heads/' + branch, base).returncode == 0
    if unused and registered:
        changes = run('status', '--porcelain', cwd=str(folder))
        unused = changes.returncode == 0 and not changes.stdout.strip()
    if unused and registered and run('worktree', 'remove', str(folder)).returncode:
        unused = False
    if unused and branch_exists and run('branch', '-D', branch).returncode:
        unused = False
    if unused:
        env.log('plan %s: the default worktree %s and branch %s of the cut were unused and are removed' % (plan, folder, branch))
    else:
        env.log('plan %s: the default worktree %s and branch %s of the cut hold their own commits or changes and stay; remove them by hand if unused' % (plan, folder, branch))
    if conn is not None:
        runtime.kv_set(conn, env.name, marker, '1')


# B-6: plans past the cut get the two header fields once.
MIGRATE_STATUSES = ('QUEUED', 'RUNNING', 'WAITING', 'REVIEW', 'PAUSED')


def migrate_headers(env):
    """Write `branch` and `worktree` into the header of every plan past the cut that lacks one: the branch and folder of the plan's last Queen while that folder exists, else the search of earlier versions one last time. One log line per written header; returns the plans written."""
    import subprocess
    from pathlib import Path
    written = []
    for row in runtime.list_plans(env.conn, env.name):
        plan = row['path']
        try:
            parsed = plan_mod.load_plan(str(env.workspace / plan))
            if parsed.status not in MIGRATE_STATUSES or (parsed.branch and parsed.worktree):
                continue
            pair = None
            for queen in reversed([a for a in runtime.list_agents(env.conn, env.name, 'all') if a.get('plan_path') == plan and a.get('phase') == 'queen' and a.get('cwd')]):
                cwd = Path(queen['cwd'])
                if cwd.is_dir() and not same_folder(cwd, env.code_repo):
                    shown = subprocess.run(['git', '-C', str(cwd), 'branch', '--show-current'], capture_output=True, text=True, encoding='utf-8', errors='replace')
                    if shown.returncode == 0 and shown.stdout.strip():
                        pair = (shown.stdout.strip(), cwd)
                        break
            pair = pair or legacy_search(env, plan)
            if not pair:
                continue
            runtime.set_plan_header(env.conn, env.name, plan, pair[0], str(pair[1]), workspace=env.workspace)
            env.log('plan %s: branch and worktree written to the header: %s, %s' % (plan, pair[0], pair[1]))
            written.append(plan)
        except (OSError, runtime.CmdError) as exc:
            env.log('plan %s: header branch and worktree not written: %s' % (plan, exc))
    return written

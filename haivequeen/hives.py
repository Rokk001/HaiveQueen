"""Persistent Hive state and project integration order."""
import json
import os
import re
import subprocess
from . import buildjobs, db, roles, runtime, agent_process, interrupt
from . import integration as integration_steps
from . import plans as plan_mod
from . import platform as osplat
from .i18n import tr


# Return the persisted Hive for one project and plan, or None.
def get(conn, project, plan):
    """Return the persisted Hive for one project and plan, or None."""
    row = conn.execute("SELECT * FROM hives WHERE project=? AND plan_path=?", (project, plan)).fetchone()
    return dict(row) if row else None


# Create a Hive once and return its persisted state.
def create(conn, project, plan, provider, colour):
    """Create a Hive once and return its persisted state."""
    with db.transaction(conn):
        conn.execute("INSERT OR IGNORE INTO hives(project,plan_path,provider,colour) VALUES (?,?,?,?)", (project, plan, provider, colour))
    return get(conn, project, plan)


# Queue a Hive and grant its project's integration place in arrival order.
def take_integration(conn, project, plan, owner_pid=None, build_jobs=None):
    """Queue a Hive and grant its project's integration place in arrival order."""
    with db.transaction(conn):
        conn.execute("INSERT OR IGNORE INTO hive_integration(project,plan_path,owner_pid,build_jobs) VALUES (?,?,?,?)", (project, plan, owner_pid, build_jobs))
        if owner_pid:
            conn.execute("UPDATE hive_integration SET owner_pid=? WHERE project=? AND plan_path=?", (owner_pid, project, plan))
        if build_jobs is not None:
            conn.execute("UPDATE hive_integration SET build_jobs=? WHERE project=? AND plan_path=?", (build_jobs, project, plan))
        head = conn.execute("SELECT plan_path FROM hive_integration WHERE project=? ORDER BY id LIMIT 1", (project,)).fetchone()
        granted = bool(head and head[0] == plan)
        if granted:
            conn.execute("UPDATE hive_integration SET acquired=1 WHERE project=? AND plan_path=?", (project, plan))
    return granted



# Remove integration reservations whose recorded owner process vanished.
def reap_integrations(conn, alive=None):
    """Remove integration reservations whose recorded owner process vanished."""
    alive = alive or osplat.process_alive
    for row in db.rows(conn, "SELECT q.id,q.owner_pid FROM hive_integration q WHERE q.owner_pid IS NOT NULL AND NOT EXISTS (SELECT 1 FROM hives h WHERE h.project=q.project AND h.plan_path=q.plan_path AND h.state='integration_wait' AND q.acquired=0)"):
        if not alive(row['owner_pid']):
            conn.execute("DELETE FROM hive_integration WHERE id=? AND owner_pid=?", (row['id'], row['owner_pid']))

# Release a Hive's integration place or waiting entry.
def release_integration(conn, project, plan):
    """Release a Hive's integration place or waiting entry."""
    with db.transaction(conn):
        conn.execute("DELETE FROM hive_integration WHERE project=? AND plan_path=?", (project, plan))


# Rest a Hive until its provider resets and release its integration place.
def rest_until(conn, project, plan, reset_at):
    """Rest a Hive until its provider resets and release its integration place."""
    with db.transaction(conn):
        conn.execute("UPDATE hives SET state='resting',reset_at=? WHERE project=? AND plan_path=?", (reset_at, project, plan))
        conn.execute("DELETE FROM hive_integration WHERE project=? AND plan_path=?", (project, plan))


# Wake resting Hives whose provider reset has arrived.
def wake_due(conn, now):
    """Wake resting Hives whose provider reset has arrived."""
    with db.transaction(conn):
        conn.execute("UPDATE hives SET state='waiting',reset_at=NULL WHERE state='resting' AND reset_at IS NOT NULL AND reset_at<=?", (now,))


# Put a Hive to rest until one of its owner questions is answered.
def wait_for_answers(conn, project, plan, questions):
    """Put a Hive to rest until one of its owner questions is answered."""
    with db.transaction(conn):
        conn.execute("UPDATE hives SET state='answer',questions_json=? WHERE project=? AND plan_path=?", (json.dumps(questions), project, plan))
        conn.execute("DELETE FROM hive_integration WHERE project=? AND plan_path=?", (project, plan))


# Mark a Hive as handing off without starting any more Bees.
def context_limit(conn, project, plan):
    """Mark a Hive as handing off without starting any more Bees."""
    with db.transaction(conn):
        conn.execute("UPDATE hives SET state='handoff' WHERE project=? AND plan_path=?", (project, plan))


# Return whether the Hive may start a Bee, respecting pause for process Bees.
def can_start_bee(conn, project, plan, own_process=False, context_limit=300000):
    """Return whether the Hive may start a Bee, respecting context and pause."""
    from . import loop, runstate
    row = get(conn, project, plan)
    if own_process and (loop.stop_flag().exists() or not runstate.starts_allowed(conn)):
        return False
    for agent in runtime.list_agents(conn, project, 'active'):
        if agent.get('plan_path') == plan and agent.get('phase') == 'queen' and agent.get('status') in ('running', 'starting'):
            flag = osplat.runs_dir() / project / agent['name'] / 'HANDOFF'
            if int(agent.get('ctxTokens') or 0) >= int(context_limit) or flag.exists():
                return False
    return bool(row and row['state'] not in ('handoff', 'queenless', 'resting', 'answer', 'owner_check', 'integration_wait', 'codex_wait', 'provider_wait', 'done'))


# Answers of the owner that mean the owner check is fine.
OWNER_CHECK_OK = {'ok', 'okay', 'yes', 'good', 'all good', 'looks good', 'passt', 'gut', 'alles ok', 'alles gut', 'passt so', 'in ordnung', 'ja', 'ist ok'}


# Normalize only the affirmative phrases recognized for an owner check.
def owner_check_ok(answer):
    """Return whether the owner's exact normalized answer accepts the check."""
    normalized = ' '.join(re.sub(r'[^\w ]', '', str(answer or '').lower()).split())
    return normalized in OWNER_CHECK_OK


# The owner check questions of a Hive, whatever suffix the Queen gave the item (owner-check, owner-check-build17, ...).
def owner_check_questions(conn, project, plan):
    """Questions of the plan whose item is owner-check or owner-check-<suffix>, oldest first; owner-check-answered marks one that was already handled."""
    return [q for q in runtime.list_questions(conn, project, plan=plan)
            if q['item'] == 'owner-check' or (str(q['item']).startswith('owner-check-') and q['item'] != 'owner-check-answered')]


# Return the owner check step of an integrated Hive: ('wait', ids) while the owner has not answered, ('fix',) when his answer says something is not right, None when there is no owner check or he said ok.
def owner_check_step(conn, project, plan):
    """Return the owner check step of an integrated Hive: ('wait', ids) while the owner has not answered, ('fix',) when his answer says something is not right, None when there is no owner check or he said ok."""
    import re
    asked = owner_check_questions(conn, project, plan)
    if not asked:
        return None
    last = asked[-1]
    if last['status'] in ('open', 'triage'):
        return ('wait', [q['id'] for q in asked if q['status'] in ('open', 'triage')])
    if last['status'] == 'answered':
        return None if owner_check_ok(last['answer']) else ('fix',)
    return None


# Find exact integration evidence without treating an older branch tip as completion.
def integrated_ready(conn, env, hive):
    """Recover a missing ready link only from the named branch's integrated tip."""
    plan = hive['plan_path']
    row = conn.execute("SELECT * FROM queue WHERE project=? AND plan_path=? ORDER BY id DESC LIMIT 1", (env.name, plan)).fetchone()
    if not row or row['status'] != 'integrated':
        return None
    if str(row['id']) == runtime.kv_get(conn, env.name, 'hive-rejected-ready:' + plan):
        return None
    rejected = runtime.list_questions(conn, env.name, status='answered', plan=plan, item='owner-check-answered')
    if rejected and not owner_check_ok(rejected[-1].get('answer')) and str(rejected[-1].get('answered_at') or '') >= str(row['updated_at'] or ''):
        return None
    if hive.get('ready_id'):
        return dict(row) if row['id'] == hive['ready_id'] else None
    from . import bees
    named = bees.plan_worktree(env, plan)
    if not named or row['branch'] != named[0] or not re.fullmatch(r'[0-9a-fA-F]{7,64}', row['tip'] or ''):
        return None
    try:
        result = subprocess.run(['git', '-C', str(env.code_repo), 'rev-parse', '--verify', 'refs/heads/' + named[0]],
                                capture_output=True, text=True, timeout=10)
    except (OSError, subprocess.TimeoutExpired):
        return None
    tip = result.stdout.strip()
    return dict(row) if result.returncode == 0 and tip and tip.startswith(row['tip']) else None


# Close persisted Hive scheduling state after authoritative plan completion.
def finish_hive(conn, project, plan):
    """Retire the Hive and only its obsolete automatic end questions."""
    for question in runtime.list_questions(conn, project, status='open', plan=plan, item='queen-ended'):
        db.answer_question(conn, question['id'], 'lifted: plan completion confirmed', 'auto')
    with db.transaction(conn):
        conn.execute("DELETE FROM hive_integration WHERE project=? AND plan_path=?", (project, plan))
        conn.execute("UPDATE hives SET state='done',questions_json=NULL,claimed_at=NULL,reset_at=NULL WHERE project=? AND plan_path=?", (project, plan))
    runtime.kv_set(conn, project, 'hive-blocker:' + plan, '')


# Reconcile authoritative completion before start gates without touching active agents.
def reconcile_completed(conn, env, alive=None, plan_files=None):
    """Retire completed idle Hives and preserve every genuine owner blocker."""
    from . import scheduler as sch
    runtime.sync_plans(conn, env.name, str(env.workspace))
    agents = runtime.list_agents(conn, env.name, 'active')
    for plan in runtime.list_plans(conn, env.name):
        if plan_files and not any(sch.norm_plan(plan['path']).endswith(sch.norm_plan(f)) for f in plan_files):
            continue
        hive = get(conn, env.name, plan['path'])
        if not hive or hive['state'] == 'done':
            continue
        if any(a.get('plan_path') == plan['path'] and a.get('phase') in ('queen', 'bee') and agent_process.counts(a, alive) for a in agents):
            continue
        if plan['status'] == 'DONE':
            finish_hive(conn, env.name, plan['path'])
            continue
        if plan['status'] not in plan_mod.STARTABLE_STATUSES:
            continue
        integrated = integrated_ready(conn, env, hive)
        if not integrated:
            continue
        integration_steps.sync(env, plan['path'], hive['provider'])   # a Deploy step no provider can do becomes an owner question here
        step = owner_check_step(conn, env.name, plan['path'])
        if step and step[0] == 'wait':
            wait_for_owner_check(conn, env.name, plan['path'], step[1])
            _move_status(conn, env, plan['path'], 'REVIEW')
        elif step and step[0] == 'fix':
            runtime.kv_set(conn, env.name, 'hive-rejected-ready:' + plan['path'], str(integrated['id']))
            reopen_after_owner_check(conn, env.name, plan['path'])
            _move_status(conn, env, plan['path'], 'RUNNING')
        else:
            questions = [q['id'] for q in runtime.list_questions(conn, env.name, plan=plan['path'])
                         if q['status'] in ('open', 'triage') and q['item'] != 'queen-ended' and not str(q['item']).upper().startswith('STALL-')]
            if questions:
                wait_for_answers(conn, env.name, plan['path'], questions)
                continue
            if integration_steps.open_steps(env, plan['path']):
                continue   # integrated, but a step package is open: the next Queen starts it, the plan stays open
            if (not owner_check_questions(conn, env.name, plan['path']) and not owner_accepted(conn, env.name, plan['path'], integrated.get('updated_at'))
                    and owner_check_wanted(env, plan['path'])):
                file_owner_check(conn, env, plan['path'])   # the system asks for the acceptance itself; the Queen is not relied on to do it
                _move_status(conn, env, plan['path'], 'REVIEW')
                continue
            runtime.set_plan_status(conn, env.name, plan['path'], 'DONE', str(env.workspace))
            finish_hive(conn, env.name, plan['path'])


# Change the status of a plan in the course of its life cycle; a plan file that cannot be written keeps its status.
def _move_status(conn, env, plan, status):
    """Set the plan status unless it already has it; a failure is logged, never raised."""
    row = conn.execute("SELECT status FROM plans WHERE project=? AND path=?", (env.name, plan)).fetchone()
    if row is None or row[0] == status:
        return
    try:
        runtime.set_plan_status(conn, env.name, plan, status, str(env.workspace))
    except runtime.CmdError as exc:
        env.log('plan %s: status change to %s failed: %s' % (plan, status, exc))


# Hive states in which every open package of the plan waits (for an answer, a provider, the owner check, ...).
WAIT_STATES = ('answer', 'resting', 'codex_wait', 'provider_wait', 'integration_wait', 'queenless', 'owner_check')


# RUNNING <-> WAITING: a plan without a live Queen or Bee whose Hive waits is WAITING; a live Queen or Bee makes it RUNNING again.
def update_waiting(conn, env, alive=None):
    """Move RUNNING plans whose Hive waits and that have no live agent to WAITING, and QUEUED or WAITING plans with a live agent to RUNNING."""
    agents = runtime.list_agents(conn, env.name, 'active')
    for plan in runtime.list_plans(conn, env.name):
        if plan['status'] not in ('QUEUED', 'RUNNING', 'WAITING'):
            continue
        hive = get(conn, env.name, plan['path'])
        if not hive or hive['state'] == 'done':
            continue
        live = any(a.get('plan_path') == plan['path'] and a.get('phase') in ('queen', 'bee') and agent_process.counts(a, alive) for a in agents)
        blocker = runtime.kv_get(conn, env.name, 'hive-blocker:' + plan['path']) or ''
        waits = not live and (hive['state'] in WAIT_STATES or bool(blocker))
        if plan['status'] == 'RUNNING' and waits:
            _move_status(conn, env, plan['path'], 'WAITING')
        elif plan['status'] in ('QUEUED', 'WAITING') and live:   # the Queen that cut the plan works on, or a waiting plan got its agent again
            _move_status(conn, env, plan['path'], 'RUNNING')


# What a WAITING plan waits for, as shown in the Hive status column.
def waiting_for(conn, project, plan):
    """The reason a plan waits: the start blocker of the last round, else the Hive state; '' when nothing is known."""
    blocker = runtime.kv_get(conn, project, 'hive-blocker:' + plan) or ''
    if blocker:
        return blocker
    hive = get(conn, project, plan)
    return 'Hive state "%s"' % hive['state'] if hive and hive['state'] in WAIT_STATES else ''


# Check whether a provider can take a waiting Hive again.
def provider_wait_check(conn, env, hive, provider, free, plan):
    """Return (available, detail) for a Hive that waits for `provider`: whether a Queen can start now and, if not, what is missing.

    A Queen needs a slot of her own, and a Bee that runs as a separate process of the same provider needs one more: the slot the waiting Queen has just
    given up is no reason to wake her. When the provider can never offer that many slots, the owner is asked once (item provider-slots)."""
    from . import usage
    full = [w for w in usage.limit_windows(conn, provider) if w['used_pct'] >= 100]
    if full:
        if any(w.get('unknown') for w in full):
            return False, 'usage unknown, no recent reading (%s)' % (usage.claude_refresh_error(conn, provider=provider) or 'check failed')
        return False, 'quota used up'
    needed = 1 + (1 if provider['name'] == hive['provider'] and provider.get('beeMode') == 'process' and not (provider.get('endpoint') or provider.get('server')) else 0)
    if provider['slots'] < needed:
        if not runtime.list_questions(conn, env.name, status='open', plan=plan, item='provider-slots'):
            db.add_question(conn, env.name, plan, 'provider-slots',
                            tr('Provider %s has %d slot(s), but its Queen plus a Bee that runs as a separate process needs %d, so no Bee of this Hive can ever start. '
                               'Raise the slots of %s, or set beeMode = "subagent" for it so its Bees need no slot of their own.', provider['name'], provider['slots'], needed, provider['name'], language=roles.language(env.settings)),
                            tr('Raise slots to %d, or set beeMode to subagent if the provider can run subagents.', needed, language=roles.language(env.settings)))
        return False, 'needs %d slots (Queen and Bee process) but has %d' % (needed, provider['slots'])
    if free < needed:
        return False, 'no free slot (needs %d, %d free)' % (needed, free)
    return True, ''


# Whether the owner already accepted the integrated plan.
def owner_accepted(conn, project, plan, since):
    """True when an owner check of the plan that was already handled (item owner-check-answered) got an ok answer at or after `since` (the time of the integration)."""
    return any(owner_check_ok(q.get('answer')) and str(q.get('answered_at') or '') >= str(since or '')
               for q in runtime.list_questions(conn, project, status='answered', plan=plan, item='owner-check-answered'))


# Whether the owner has to accept the integrated plan.
def owner_check_wanted(env, plan):
    """True unless the plan's header says owner_check = "none"; an unreadable plan file counts as wanted."""
    from . import plans as planlib
    try:
        return planlib.load_plan(str(env.workspace / plan)).owner_check != 'none'
    except (OSError, ValueError):
        return True


# Ask the owner to accept an integrated plan.
def file_owner_check(conn, env, plan):
    """File the owner check question of an integrated plan (the configured text or its default, with the final report of its last Queen) and park the Hive until the owner answers; returns the question id."""
    final = ''
    last = next((a for a in reversed(runtime.list_agents(conn, env.name, 'all')) if a.get('plan_path') == plan and a.get('phase') == 'queen'), None)
    if last:
        try:
            final = (env.run_dir(last['name']) / 'final.txt').read_text(encoding='utf-8', errors='replace').strip()[:800]
        except OSError:
            final = ''
    language = roles.language(env.settings)
    text = str(env.settings.get('ownerCheckText') or tr('Check the integrated result', language=language))
    if final:
        text += tr('\n\nReport of the Queen:\n', language=language) + final
    qid = db.add_question(conn, env.name, plan, 'owner-check', text, '')
    wait_for_owner_check(conn, env.name, plan, [qid])
    return qid


# Park an integrated Hive until the owner has answered the owner check question; no Queen runs and no integration place is held.
def wait_for_owner_check(conn, project, plan, questions):
    """Park an integrated Hive until the owner has answered the owner check question; no Queen runs and no integration place is held."""
    with db.transaction(conn):
        conn.execute("UPDATE hives SET state='owner_check',questions_json=? WHERE project=? AND plan_path=?", (json.dumps(questions), project, plan))
        conn.execute("DELETE FROM hive_integration WHERE project=? AND plan_path=?", (project, plan))


# Reopen a Hive after a owner check answer that says something is not right: the old ready row no longer closes the plan and the next Queen fixes exactly that.
def reopen_after_owner_check(conn, project, plan):
    """Reopen a Hive after a owner check answer that says something is not right: the old ready row no longer closes the plan and the next Queen fixes exactly that."""
    integration_steps.reset(conn, project, plan)
    with db.transaction(conn):
        conn.execute("UPDATE questions SET item='owner-check-answered' WHERE project=? AND plan_path=? AND (item='owner-check' OR (item LIKE 'owner-check-%' AND item!='owner-check-answered')) AND status='answered'", (project, plan))
        conn.execute("UPDATE hives SET state='waiting',ready_id=NULL,questions_json=NULL WHERE project=? AND plan_path=?", (project, plan))
        conn.execute("DELETE FROM hive_integration WHERE project=? AND plan_path=?", (project, plan))


# Repair historical technical ends and lift their automatic Queen questions.
def repair_technical_ends(conn, project):
    """Mark vanished agents and release questions caused solely by their technical end."""
    from . import interrupt
    agents = runtime.list_agents(conn, project, 'all')
    technical = {}
    for agent in agents:
        if agent.get('status') not in ('done', 'failed') or not interrupt.VANISHED_RE.match(str(agent.get('last_line') or '')):
            continue
        technical[(agent.get('plan_path'), agent.get('name'))] = agent
        if not agent.get('interrupted'):
            conn.execute("UPDATE agents SET extra_json=json_set(COALESCE(extra_json, '{}'), '$.interrupted', ?) WHERE id=?", (interrupt.VANISHED, agent['id']))
    # Older reports need the Bee row for the class; newer reports carry it themselves.
    if conn.execute("SELECT 1 FROM sqlite_master WHERE type='table' AND name='bee_attempt_reports'").fetchone():
        conn.execute('CREATE TABLE IF NOT EXISTS bee_technical_repairs (project TEXT, plan_path TEXT, package TEXT, attempt TEXT, PRIMARY KEY(project,plan_path,package,attempt))')
        has_signals = bool(conn.execute("SELECT 1 FROM sqlite_master WHERE type='table' AND name='bee_attempt_signals'").fetchone())
        for (plan, name), agent in technical.items():
            if agent.get('phase') != 'bee' or not agent.get('class') or not agent.get('package'):
                continue
            key = (project, plan, agent['package'], name)
            with db.transaction(conn):
                report = conn.execute('SELECT 1 FROM bee_attempt_reports WHERE project=? AND plan_path=? AND package=? AND attempt=?', key).fetchone()
                if not report or not conn.execute('INSERT OR IGNORE INTO bee_technical_repairs VALUES(?,?,?,?)', key).rowcount:
                    continue
                signal = conn.execute('SELECT class,counted FROM bee_attempt_signals WHERE project=? AND plan_path=? AND package=? AND attempt=?', key).fetchone() if has_signals else None
                cls, counted = (signal[0], signal[1]) if signal else (agent['class'], 1)
                if signal:
                    conn.execute('DELETE FROM bee_attempt_signals WHERE project=? AND plan_path=? AND package=? AND attempt=?', key)
                if counted:
                    conn.execute('UPDATE bee_attempts SET failures=MAX(0,failures-1) WHERE project=? AND plan_path=? AND package=? AND class=?',
                                 (project, plan, agent['package'], cls))
    for hive in db.rows(conn, "SELECT * FROM hives WHERE project=? AND state='answer'", (project,)):
        ids = json.loads(hive['questions_json'] or '[]')
        for question in runtime.list_questions(conn, project, status='open', plan=hive['plan_path']):
            if question['id'] not in ids or question['item'] != 'queen-ended':
                continue
            match = re.match(r'^Queen (.+?) of this Hive ended without new work \(no commit, no changed file, no question\)\.', question['question'])
            agent = technical.get((hive['plan_path'], match.group(1))) if match else None
            if agent and agent.get('phase') == 'queen':
                db.answer_question(conn, question['id'], 'lifted: only a technical end (window closed, killed, crashed)', 'auto')
        remaining = [q for q in runtime.list_questions(conn, project, status='open', plan=hive['plan_path']) if q['id'] in ids]
        if ids and not remaining:
            conn.execute("UPDATE hives SET state='waiting',questions_json=NULL WHERE project=? AND plan_path=?", (project, hive['plan_path']))


# Report repeated vanished process starts while leaving retries enabled.
def technical_start_faults(conn, project):
    """File one no-start fault per Hive or Bee package after three consecutive technical ends."""
    groups = {}
    for agent in runtime.list_agents(conn, project, 'all'):
        if agent.get('phase') not in ('queen', 'bee'):
            continue
        key = (agent.get('plan_path'), agent.get('phase'), agent.get('package') if agent.get('phase') == 'bee' else '')
        groups.setdefault(key, []).append(agent)
    for (plan, phase, package), history in groups.items():
        count = 0
        for agent in reversed(history):
            line = str(agent.get('last_line') or '')
            if agent.get('status') not in ('failed', 'done') or not interrupt.VANISHED_RE.match(line):
                break
            if phase == 'queen' and not line.startswith('pane never started'):
                break
            count += 1
        if count < 3:
            continue
        marker = '%s | %s | %s | %s' % (project, plan, phase, package or '')
        existing = runtime.list_questions(conn, 'HaiveQueen', plan='HQ-FAULTS', item='no_start')
        if not any(marker in q['question'] for q in existing):
            db.add_question(conn, 'HaiveQueen', 'HQ-FAULTS', 'no_start', '[no_start] Three consecutive technical ends: ' + marker, '')


# Fingerprint one worktree entry: a link target, the SHA-256 of a file, or None when the path no longer exists.
def _entry_fingerprint(path, name):
    """Return 'link:<target>', the SHA-256 hex digest of a regular file, or None for a missing path; raises OSError for anything unreadable."""
    import hashlib
    if path.is_symlink():
        return 'link:' + os.readlink(path)
    if path.is_file():
        return hashlib.sha256(path.read_bytes()).hexdigest()
    if path.exists():
        raise OSError('cannot read worktree entry: ' + name)
    return None


# Capture the contents of every Git-visible file (the thorough, slow way used by snapshots made before worktree_state became fast).
def full_worktree_state(cwd, excluded=None):
    """Return hashes of all tracked and non-ignored untracked files, excluding the plan state; reads every file, so it is slow in a large worktree."""
    import subprocess
    from pathlib import Path
    root = Path(cwd)
    result = subprocess.run(['git', '-C', str(root), 'ls-files', '-co', '--exclude-standard', '-z'], capture_output=True, check=True, timeout=30)
    state = {}
    exclusions = excluded if isinstance(excluded, list) else [excluded] if excluded else []
    exclusions = {Path(p).resolve() for p in exclusions}
    for raw in result.stdout.split(b'\0'):
        if not raw:
            continue
        name = os.fsdecode(raw)
        path = root / name
        if path.resolve() in exclusions:
            continue
        state[name] = _entry_fingerprint(path, name)
    return state


# Capture the files that differ from the last commit, for comparison at the Queen's end.
def worktree_state(cwd, excluded=None):
    """Return fingerprints of the files that differ from the last commit (modified, deleted and non-ignored untracked), excluding the plan state.

    Git names those files without reading the clean ones, so this takes a fraction of a second also in a worktree with thousands of files.
    A change shows as a new fingerprint and a revert to the committed content disappears, so two states are equal exactly when no file was
    changed in between (a new commit is detected separately by ended_without_work)."""
    import subprocess
    from pathlib import Path
    git = ['git', '--no-optional-locks', '-C', str(cwd)]   # --no-optional-locks: do not refresh the index while the Queen may be running Git too
    top = Path(subprocess.run(git + ['rev-parse', '--show-toplevel'], capture_output=True, text=True, encoding='utf-8', check=True, timeout=30).stdout.strip())
    result = subprocess.run(git + ['status', '--porcelain=v1', '-z', '--untracked-files=all', '--no-renames'], capture_output=True, check=True, timeout=60)
    state = {}
    exclusions = excluded if isinstance(excluded, list) else [excluded] if excluded else []
    exclusions = {Path(p).resolve() for p in exclusions}
    for raw in result.stdout.split(b'\0'):
        if len(raw) < 4:
            continue
        name = os.fsdecode(raw[3:])   # "XY <path>": two status letters, a space, the path relative to the top of the repository
        path = top / name
        if path.resolve() in exclusions:
            continue
        state[name] = _entry_fingerprint(path, name)
    return state


# Persist the Queen's starting worktree state in her run folder.
def save_worktree_state(cwd, run_dir, excluded=None):
    """Save a snapshot when the worktree is readable; otherwise retain the commit fallback."""
    from pathlib import Path
    try:
        state = worktree_state(cwd, excluded)
        exclusions = excluded if isinstance(excluded, list) else [excluded] if excluded else []
        (Path(run_dir) / 'worktree-start.json').write_text(json.dumps({'cwd': str(Path(cwd).resolve()), 'excluded': [str(p) for p in exclusions], 'files': state, 'mode': 'git-status'}), encoding='utf-8')
    except (OSError, ValueError, subprocess.SubprocessError):
        return


# Select the newest report written during the run, falling back to the final message.
def queen_final_text(run_dir, started, message='', ended=float('inf')):
    """Return the Queen's report or her last non-empty message."""
    from pathlib import Path
    excluded = {'final.txt', 'stderr.txt', 'runner-error.txt', 'milestones.txt', 'build-samples.txt', 'prompt.md', 'rules.md'}
    try:
        reports = [p for p in Path(run_dir).iterdir() if p.is_file() and p.suffix.lower() in ('.md', '.txt') and p.name.lower() not in excluded and started <= p.stat().st_mtime < ended]
        if reports:
            return max(reports, key=lambda p: (p.stat().st_mtime_ns, p.name)).read_text(encoding='utf-8-sig', errors='replace')
    except OSError:
        pass
    return message


# Return whether the Queen ended without a handoff, new commit or changed worktree (unknown counts as work).
def ended_without_work(env, agent):
    """Return whether the Queen ended without a handoff, new commit or changed worktree."""
    import subprocess
    from pathlib import Path
    if (env.run_dir(agent['name']) / 'HANDOFF').exists():
        return False
    cwd = agent.get('cwd')
    since = str(agent.get('started_at') or '')[:19]
    if not cwd or not since or not Path(cwd).is_dir():
        return False
    try:
        result = subprocess.run(['git', '-C', str(cwd), 'log', '--all', '-1', '--since=' + since, '--format=%H'], capture_output=True, text=True, encoding='utf-8', errors='replace', timeout=30)
    except (OSError, subprocess.SubprocessError):
        return False
    if result.returncode != 0 or result.stdout.strip():
        return False
    snapshot = env.run_dir(agent['name']) / 'worktree-start.json'
    if not snapshot.is_file():
        return True
    try:
        state = json.loads(snapshot.read_text(encoding='utf-8'))
        if Path(cwd).resolve() != Path(state['cwd']).resolve():
            return False
        compare = worktree_state if state.get('mode') == 'git-status' else full_worktree_state   # snapshots without a mode were made by the thorough method
        return compare(cwd, state.get('excluded')) == state['files']
    except (OSError, ValueError, KeyError, subprocess.SubprocessError):
        return False


# File the automatic owner question for a Queen that ended without work and return its id.
def ask_about_end(conn, env, plan, agent, final_report):
    """File the automatic owner question for a Queen that ended without work and return its id; the question holds her last text."""
    language = roles.language(env.settings)
    text = (final_report or '').strip() or tr('no final text (status %s)', agent.get('status'), language=language)
    question = tr('Queen %s of this Hive ended without new work (no commit, no changed file, no question). Her last text: %s How should the Hive continue?', agent['name'], text[:1500], language=language)
    return db.add_question(conn, env.name, plan, 'queen-ended', question, tr('Answer what blocks her, or say how she should count or decide it; then the next Queen starts.', language=language))


# Return whether all independent Bee processes of the Hive have finished.
def can_handoff(conn, project, plan):
    """Return whether all independent Bee processes of the Hive have finished."""
    return not any(a.get('plan_path') == plan and a.get('phase') == 'bee' and agent_process.counts(a) for a in runtime.list_agents(conn, project, 'active'))


# End this Hive's Queen and own-process Bees, leaving it queenless.
def dequeen(conn, project, plan, stop=None):
    """End this Hive's Queen and own-process Bees, leaving it queenless."""
    if get(conn, project, plan) is None:
        raise ValueError('unknown Hive')
    stop = stop or osplat.kill_process_tree
    rows = [a for a in runtime.list_agents(conn, project, 'active') if a.get('plan_path') == plan and a.get('phase') in ('queen', 'bee') and agent_process.counts(a)]
    for row in rows:
        if row.get('pid') and stop(row['pid']) is False:
            raise ValueError('could not end Hive process')
        runtime.upsert_agent(conn, project, row['name'], {'status': 'done', 'pid': None, 'ended_at': db.now(), 'last_line': 'dequeened'})
    with db.transaction(conn):
        conn.execute("UPDATE hives SET state='queenless',reset_at=NULL,requested_provider=NULL WHERE project=? AND plan_path=?", (project, plan))
        conn.execute("DELETE FROM hive_integration WHERE project=? AND plan_path=?", (project, plan))


# The status a plan had before it was paused is kept in the kv table (survives a restart).
def _plan_status(conn, project, plan):
    """The status of a plan in the table, or raise ValueError for an unknown plan."""
    row = conn.execute("SELECT status FROM plans WHERE project=? AND path=?", (project, plan)).fetchone()
    if row is None:
        raise ValueError('unknown plan')
    return row[0]


# Pause a plan: no new Queen or Bee starts, a running Queen keeps working.
def pause_plan(conn, project, plan):
    """Set the plan to PAUSED and keep its earlier status; a plan that is already PAUSED or CANCELLED stays as it is."""
    status = _plan_status(conn, project, plan)
    if status in ('PAUSED', 'CANCELLED'):
        return
    runtime.kv_set(conn, project, 'plan-prepause:' + plan, status)
    runtime.set_plan_status(conn, project, plan, 'PAUSED')


# Stop a Hive: end its Queen and Bees and pause the plan; handoff, package states, branch and worktrees stay.
def stop_hive(conn, project, plan, stop=None):
    """End the Queen and the own-process Bees of the Hive (as dequeen does) and set the plan to PAUSED."""
    pause_plan(conn, project, plan)
    if get(conn, project, plan) is not None:
        dequeen(conn, project, plan, stop=stop)


# Is the Hive of a plan stopped?
def is_stopped(conn, project, plan):
    """True when the plan is PAUSED and no live Queen or Bee of it runs."""
    if _plan_status(conn, project, plan) != 'PAUSED':
        return False
    return not any(a.get('plan_path') == plan and a.get('phase') in ('queen', 'bee') and agent_process.counts(a) for a in runtime.list_agents(conn, project, 'active'))


# Cancel a stopped plan or a draft; nothing is deleted.
def cancel_plan(conn, project, plan):
    """Set a stopped plan (PAUSED without a live Queen) or a DRAFT plan to CANCELLED."""
    if _plan_status(conn, project, plan) != 'DRAFT' and not is_stopped(conn, project, plan):
        raise ValueError('only a stopped Hive or a draft can be cancelled')
    runtime.set_plan_status(conn, project, plan, 'CANCELLED')
    runtime.reset_cut_assignments(conn, project, plan)   # a cancelled plan's cut no longer applies
    runtime.kv_delete(conn, project, 'plan-prepause:' + plan)


reject_plan = cancel_plan   # the earlier name of the action


# Put a stopped or cancelled plan back to draft.
def back_to_draft(conn, project, plan):
    """Set a stopped (PAUSED without a live Queen) or CANCELLED plan to DRAFT."""
    if _plan_status(conn, project, plan) != 'CANCELLED' and not is_stopped(conn, project, plan):
        raise ValueError('only a stopped or cancelled plan can go back to draft')
    runtime.set_plan_status(conn, project, plan, 'DRAFT')
    runtime.reset_cut_assignments(conn, project, plan)   # the owner redrafts the plan: a new generation is cut again
    runtime.kv_delete(conn, project, 'plan-prepause:' + plan)


# Start a plan from the Hives list.
def start_plan(conn, project, plan):
    """A DRAFT plan becomes SENT and is prepared like any sent plan (its Queen cuts it, bzz plan check records the cut); a PAUSED or CANCELLED plan is started again (start_again)."""
    if _plan_status(conn, project, plan) == 'DRAFT':
        runtime.reset_cut_assignments(conn, project, plan)   # a new generation: its cut is recorded when the Queen's cut passes the plan check
        runtime.set_plan_status(conn, project, plan, 'SENT')
    else:
        start_again(conn, project, plan)


# Start a paused or rejected plan again.
def start_again(conn, project, plan):
    """A PAUSED plan gets the status it had before the pause (QUEUED when unknown), a CANCELLED plan becomes DRAFT."""
    status = _plan_status(conn, project, plan)
    if status == 'CANCELLED':
        runtime.set_plan_status(conn, project, plan, 'DRAFT')
        runtime.reset_cut_assignments(conn, project, plan)
    elif status == 'PAUSED':
        before = runtime.kv_get(conn, project, 'plan-prepause:' + plan)
        before = plan_mod.normalize_status(before) if plan_mod.status_known(before) else ''
        runtime.set_plan_status(conn, project, plan, before if before and before not in ('PAUSED', 'CANCELLED') else 'QUEUED')
        runtime.kv_delete(conn, project, 'plan-prepause:' + plan)
    else:
        raise ValueError('the plan is neither paused nor cancelled')


# Request a replacement Queen, preserving the provider unless explicitly changed.
def requeen(conn, project, plan, provider=None, stop=None, providers=None, queen_class="medium", excluded=()):
    """Request a replacement Queen, preserving the provider unless explicitly changed."""
    if get(conn, project, plan) is None:
        raise ValueError('unknown Hive')
    if provider and (provider in excluded or providers is not None and not any(p['name'] == provider and 'Queen' in p.get('capabilities', []) and p.get('classes', {}).get(queen_class, {}).get('model') for p in providers)):
        raise ValueError('provider does not offer the Queen class')
    if any(a.get('plan_path') == plan and a.get('phase') == 'queen' and agent_process.counts(a) for a in runtime.list_agents(conn, project, 'active')):
        dequeen(conn, project, plan, stop=stop)
    with db.transaction(conn):
        conn.execute("UPDATE hives SET state='requeen',requested_provider=?,reset_at=NULL WHERE project=? AND plan_path=?", (provider, project, plan))


STEP_WORD = re.compile(r"(?i)\b(?:step|schritt)\s*(\d+)\s*(?:/|of|von)\s*(\d+)")   # "step 2/5", "Step 2 of 5", "Schritt 2/5"


# Find the latest step report of each package of a Hive.
def package_steps(conn, project, plan, packages, active, notes):
    """{package: {'step': 'n/m', 'task': text}} from the newest note per package that names a package of the plan and a step number "n/m".

    The wording around them is free ("Mia P1: step 2/4 text", "P1 step 2 of 4"). Only notes of the Hive count: a note written by one of its agents (Queen or Bee, also an ended one)
    or by a Bee or subagent that belonged to it, named at the start of the text. The newest report of a package stays when its Bee is replaced, until the new Bee reports a step."""
    own = [a for a in runtime.list_agents(conn, project, 'all') if a.get('plan_path') == plan] + list(active)
    mine = {str(a['name']) for a in own}
    people = {str(a['person']) for a in own if a.get('person')}
    if conn.execute("SELECT 1 FROM sqlite_master WHERE type='table' AND name='embedded_bees'").fetchone():
        people |= {r[0] for r in conn.execute("SELECT name FROM embedded_bees WHERE project=? AND plan_path=?", (project, plan))}
    found = {}
    for note in notes:
        if note['project'] != project:
            continue
        text = note['text'] or ''
        step = STEP_WORD.search(text)
        n, m = int(step.group(1)), int(step.group(2))
        if m <= 0 or n > m:
            continue
        first = re.split(r"[\s:,;()\[\]-]+", text.strip(), 1)[0]
        if note['agent'] not in mine and first not in people:
            continue
        before = text[:step.start()] or text
        hits = [(h.start(), p) for p in packages for h in re.finditer(r"(?<![\w-])%s(?![\w-])" % re.escape(p), before, re.I)]
        if not hits:
            hits = [(h.start(), p) for p in packages for h in re.finditer(r"(?<![\w-])%s(?![\w-])" % re.escape(p), text, re.I)]
        if hits:
            package = max(hits)[1]   # the package named last before the step number
            found[package] = {'step': '%d/%d' % (n, m), 'task': text[step.end():].strip()}
    return found


# Collect Hives, their Queen and the latest reported step of each Bee (shown in the Hive table of the graphical dashboard).
def hive_data(conn, project, agents, plan=None):
    """Collect Hives, active Queens and Bee progress from their reports; `project` and `plan` narrow the Hives (None = all)."""
    where, args = ["state!='done'"], []
    if project:
        where.append("project=?")
        args.append(project)
    if plan:
        where.append("plan_path=?")
        args.append(plan)
    rows = db.rows(conn, "SELECT * FROM hives WHERE " + " AND ".join(where) + " ORDER BY project,plan_path", args)
    notes = db.rows(conn, "SELECT project,agent,text FROM events WHERE kind='note' ORDER BY id") if rows else []
    step_notes = [n for n in notes if STEP_WORD.search(n['text'] or '')]
    for hive in rows:
        active = [a for a in agents if a.get('project') == hive['project'] and a.get('plan_path') == hive['plan_path']]
        queens_of = [a for a in active if a.get('phase') == 'queen']
        hive['queen'] = next((a for a in queens_of if a.get('status') in ('running', 'starting')), queens_of[-1] if queens_of else None)   # the running Queen, else the newest one
        bees = {str(a.get('person') or a.get('name')): {'name': str(a.get('person') or a.get('name')), 'task': a.get('task') or a.get('package') or '', 'step': ''} for a in active if a.get('phase') == 'bee'}
        queens = {a['name'] for a in active if a.get('phase') == 'queen'}
        packages = {r['id'] for r in db.rows(conn, "SELECT id FROM packages WHERE project=? AND plan_path=?", (hive['project'], hive['plan_path']))}
        for note in notes:
            if note['project'] != hive['project']:
                continue
            match = re.match(r"^([^ ]+) ([^:]+): step (\d+/\d+)\s*(.*)$", note['text'] or '')
            if not match:
                continue
            name, package, step, task = match.groups()
            if name in bees or (note['agent'] in queens and package in packages):
                n, m = map(int, step.split('/'))
                if n >= m:
                    bees.pop(name, None)
                else:
                    bees[name] = {'name': name, 'task': task or package, 'step': step}
        hive['bees'] = list(bees.values())
        hive['package_steps'] = package_steps(conn, hive['project'], hive['plan_path'], packages, active, step_notes)
        hive['blocker'] = runtime.kv_get(conn, hive['project'], 'hive-blocker:' + hive['plan_path'])
        hive['pending_questions'] = [q['id'] for q in runtime.list_questions(conn, hive['project'], plan=hive['plan_path']) if q['status'] in ('open', 'triage') and not str(q['item']).upper().startswith('STALL-')]
    return rows


# Run one machine-wide scheduling round for Hives without a language model.
def schedule_round(conn, machine, envs, dry_run=False, free_ram=None, alive=None, settle=3.0, plan_files=None):
    """Run one machine-wide scheduling round for Hives without a language model."""
    from datetime import datetime, timedelta
    from pathlib import Path
    from . import loop, routine, runstate, scheduler as sch, usage
    from . import faults, inbox, views
    free_ram = free_ram or loop.ram_gb
    result = {'skipped': None, 'projects': {}}
    if not dry_run:
        for env in envs:
            reconcile_completed(conn, env, alive=alive, plan_files=plan_files)
        try:
            codex = [p for p in sch.get_providers(machine) if p.get("limit") == "codex"]
            for provider in codex:
                usage.refresh_codex_usage(conn, min_interval=provider.get("usageInterval", 60), provider=provider)
        except Exception:
            pass
        for env in envs:
            inbox.process_inbox(env)
    if not dry_run:
        if loop.stop_flag().exists():
            result['skipped'] = 'STOP'
        elif not runstate.starts_allowed(conn):
            result['skipped'] = runstate.get_state(conn)['state']
        if result['skipped']:
            for env in envs:
                agents = runtime.list_agents(conn, env.name, 'active')
                for hive in db.rows(conn, "SELECT plan_path FROM hives WHERE project=? AND state!='done'", (env.name,)):
                    if not any(a.get('plan_path') == hive['plan_path'] and a.get('phase') in ('queen', 'bee')
                               and agent_process.counts(a, alive) for a in agents):
                        runtime.kv_set(conn, env.name, 'hive-blocker:' + hive['plan_path'], result['skipped'])
                env.log('round skipped (%s)' % result['skipped'])
                faults.check_round(env, 0, False)
                loop.run_hook(env, loop._state_summary(env))
            return result
    if not dry_run:
        for env in envs:   # the global gates are open: a start blocker written while HaiveQueen was paused or stopped is stale
            conn.execute("UPDATE kv SET value='' WHERE project=? AND key LIKE 'hive-blocker:%' AND value IN ('PAUSED','STOP')", (env.name,))
    if not dry_run:
        reap_integrations(conn, alive=alive)
        wake_due(conn, db.now())
    for env in envs:
        runtime.sync_plans(conn, env.name, str(env.workspace))
        if not dry_run:
            routine.repair_stale_agents(env, alive=alive)
            repair_technical_ends(conn, env.name)
            try:   # E-1: a subagent Bee counts as ended once its Queen has certainly ended
                __import__('haivequeen.bees', fromlist=['x']).close_ended_embedded(conn, env.name, alive=alive)
            except Exception as exc:
                env.log('ended subagent Bees not closed: %s' % exc)
            technical_start_faults(conn, env.name)
            runtime.archive_agents(conn, env.name, 12)
    all_active = runtime.list_agents(conn, None, 'active')
    is_alive = alive or osplat.process_alive

    # Count an agent until its runner has actually exited.
    # Count an agent until its runner has actually exited.
    def process_running(row):
        """Count an agent until its runner has actually exited."""
        return agent_process.counts(row, is_alive)

    processes = [{**a, 'status': 'running'} for a in all_active if process_running(a)]
    free = sch.provider_free(envs[0].providers if envs else [], processes)
    pool = loop.Pool(free, 0)
    limit = int(machine.get('hives', 2))
    queen_count = sum(1 for a in processes if a.get('phase') == 'queen')
    excluded = set(machine.get('excludedProviders') or [])
    projects = sch.order_projects(envs)
    result['projects'] = {env.name: {'report': [], 'started': 0, 'actions': 0} for env in projects}
    skipped = {env.name: [] for env in projects}

    # Remember why a plan got no Queen this round; the reasons are logged with the round line.
    def not_started(env, path, reason):
        """Remember why a plan got no Queen this round."""
        skipped[env.name].append('%s not started: %s' % (Path(path).stem, reason))
        if not dry_run:
            runtime.kv_set(conn, env.name, 'hive-blocker:' + path, reason)

    for existing_pass in (True, False):
        for env in projects:
            report = result["projects"][env.name]["report"]
            started = 0
            rows = runtime.list_plans(conn, env.name)
            ordered = []
            for plan in rows:
                if plan_files and not any(sch.norm_plan(plan['path']).endswith(sch.norm_plan(f)) for f in plan_files):
                    continue
                hive = get(conn, env.name, plan['path'])
                if bool(hive) != existing_pass:
                    continue
                if plan['status'] not in plan_mod.QUEEN_STATUSES or (hive and hive['state'] == 'done'):
                    continue
                if dry_run and hive and integrated_ready(conn, env, hive):
                    continue
                if hive and hive['state'] == 'starting':
                    claimed = db.parse_time(hive.get('claimed_at'))
                    if claimed and (datetime.now() - claimed).total_seconds() < 120:
                        continue
                    conn.execute("UPDATE hives SET state='waiting',claimed_at=NULL WHERE project=? AND plan_path=? AND state='starting'", (env.name, plan['path']))
                    hive = get(conn, env.name, plan['path'])
                if hive and hive['state'] == 'integration_wait':
                    if any(a.get('plan_path') == plan['path'] and a.get('phase') == 'queen' and process_running(a) for a in runtime.list_agents(conn, env.name, 'active')):
                        continue
                if hive and hive['state'] == 'answer':
                    ids = json.loads(hive['questions_json'] or '[]')
                    if ids and any(q['status'] == 'answered' and q['id'] in ids for q in runtime.list_questions(conn, env.name)):
                        conn.execute("UPDATE hives SET state='waiting',questions_json=NULL WHERE project=? AND plan_path=?", (env.name, plan['path']))
                        hive = get(conn, env.name, plan['path'])
                history = [a for a in runtime.list_agents(conn, env.name, 'all') if a.get('plan_path') == plan['path'] and a.get('phase') == 'queen']
                running = [a for a in history if not a.get('archived') and process_running(a)]
                if running:
                    if not dry_run:
                        runtime.kv_set(conn, env.name, 'hive-blocker:' + plan['path'], '')
                    flagged = routine.set_handoff_flags(env, [routine.view(a) for a in running]) if not dry_run else []
                    if flagged and hive:
                        context_limit(conn, env.name, plan['path'])
                    if hive and not dry_run and plan['status'] not in plan_mod.CUT_STATUSES:
                        from . import bees
                        provider = sch.get_provider(env.providers, hive['provider'])
                        slots = sum(pool.free.values()) if provider and provider.get('beeMode') == 'process' else int(env.settings.get('beesPerQueen', 3))
                        try:   # H-47: in this round, tell the Queen which startable packages to start while Bee places are free
                            bees.notify_startable(env, plan['path'], running[-1], slots, is_alive)
                        except Exception as exc:
                            env.log('startable packages not reported: %s' % exc)
                        try:   # H-31: a Queen that started later learns of stopped packages and pending stops too
                            bees.write_stop_notice(conn, env.name, plan['path'])
                        except Exception as exc:
                            env.log('stopped packages not reported: %s' % exc)
                    continue
                if hive and hive['state'] == 'handoff':
                    if not can_handoff(conn, env.name, plan['path']):
                        not_started(env, plan['path'], 'handing off, Bee processes are still running')
                        continue
                    release_integration(conn, env.name, plan['path'])
                    conn.execute("UPDATE hives SET state='waiting' WHERE project=? AND plan_path=?", (env.name, plan['path']))
                    hive = get(conn, env.name, plan['path'])
                if hive and hive['state'] == 'running':
                    release_integration(conn, env.name, plan['path'])
                    last = history[-1] if history else None
                    if last and last.get('interrupted') in ('api', 'connection'):
                        history_views = [routine.view(a) for a in history]
                        if not routine.retry_due(env, history_views, routine.view(last)):
                            not_started(env, plan['path'], 'retry after an interrupted Queen is not due yet')
                            continue
                    if last and last.get('interrupted') == 'rate_limit':
                        provider = sch.get_provider(env.providers, hive['provider'])
                        windows = usage.limit_windows(conn, provider) if provider else []
                        resets = [w['reset_epoch'] for w in windows if w['used_pct'] >= 100 and w['reset_epoch']]
                        reset = datetime.fromtimestamp(max(resets)) if resets else datetime.now() + timedelta(minutes=float(env.settings.get('rateLimitWaitMinutes', 15)))
                        rest_until(conn, env.name, plan['path'], reset.isoformat(timespec='seconds'))
                    else:
                        final = env.run_dir(last['name']) / 'final.txt' if last else None
                        final_report = final.read_text(encoding='utf-8-sig') if final and final.is_file() else ''
                        if last:
                            try:
                                report_started = datetime.fromisoformat(last['started_at']).timestamp()
                            except (ValueError, TypeError, KeyError):
                                report_started = float('inf')
                            try:
                                ended = datetime.fromisoformat(last['ended_at']).timestamp() + 1
                            except (ValueError, TypeError, KeyError):
                                ended = float('inf')
                            final_report = queen_final_text(env.run_dir(last['name']), report_started, final_report, ended)
                        questions = [q for q in runtime.list_questions(conn, env.name, status='open', plan=plan['path']) if not str(q['item']).upper().startswith('STALL-')]   # stop-rule questions never block a Hive
                        if last and interrupt.kind_of(last) == interrupt.VANISHED:
                            conn.execute("UPDATE hives SET state='waiting' WHERE project=? AND plan_path=?", (env.name, plan['path']))
                        elif last and last.get('status') == 'done' and 'WAITING_FOR_OWNER' in final_report and questions:
                            wait_for_answers(conn, env.name, plan['path'], [q['id'] for q in questions])
                        elif last and not interrupt.is_interrupted(last) and ended_without_work(env, last):   # open owner questions are the reason; her final text does not matter, only without any a question is filed
                            wait_for_answers(conn, env.name, plan['path'], [q['id'] for q in questions] or [ask_about_end(conn, env, plan['path'], last, final_report)])
                        else:
                            conn.execute("UPDATE hives SET state='waiting' WHERE project=? AND plan_path=?", (env.name, plan['path']))
                    hive = get(conn, env.name, plan['path'])
                if hive and hive['state'] in ('codex_wait', 'provider_wait') and history and not dry_run:   # the Queen asked for a Bee and ended: with owner questions open and no new commit the Hive waits for an answer, it does not start the next Queen
                    open_questions = [q['id'] for q in runtime.list_questions(conn, env.name, status='open', plan=plan['path']) if not str(q['item']).upper().startswith('STALL-')]
                    if open_questions and not interrupt.is_interrupted(history[-1]) and ended_without_work(env, history[-1]):
                        wait_for_answers(conn, env.name, plan['path'], open_questions)
                        not_started(env, plan['path'], 'Hive state "answer" (questions %s)' % ', '.join(str(i) for i in open_questions))
                        continue
                if hive and hive['state'] in ('codex_wait', 'provider_wait'):
                    from . import bees
                    wait = conn.execute('SELECT provider FROM bee_provider_wait WHERE project=? AND plan_path=?', (env.name, plan['path'])).fetchone()
                    provider = sch.get_provider(env.providers, wait[0]) if wait else None
                    name = provider['name'] if provider else wait[0] if wait else '?'
                    can, detail = provider_wait_check(conn, env, hive, provider, pool.free.get(name, 0), plan['path']) if provider else (False, 'provider unknown')
                    if not (can and bees.provider_available(conn, env.name, plan['path'], name, pool.free.get(name, 0), False)):
                        not_started(env, plan['path'], 'waiting for provider %s: %s' % (name, detail or 'not available'))
                        continue
                    hive = get(conn, env.name, plan['path'])
                if hive and hive['state'] in ('queenless', 'resting', 'answer', 'owner_check', 'handoff'):
                    not_started(env, plan['path'], 'Hive state "%s"%s' % (hive['state'], ' (questions %s)' % ', '.join(str(i) for i in json.loads(hive['questions_json'] or '[]')) if hive['state'] == 'answer' else ''))
                    continue
                qs = runtime.list_packages(conn, env.name, plan['path'])
                waiting = min((sch.waiting_since(env, plan['path'], p['id']) for p in qs), default='')
                ordered.append((0 if hive else 1, waiting, plan['path'], plan, hive))
            ordered.sort(key=lambda p: p[:3])
            for _, _, path, plan, hive in ordered:
                force = bool(hive and hive['state'] == 'requeen')
                if queen_count >= limit and not force:
                    not_started(env, path, 'Hive limit %d reached' % limit)
                    continue
                if free_ram() < loop._min_ram(env):
                    not_started(env, path, 'free RAM %.2f GB is below the minimum %.2f GB' % (free_ram(), loop._min_ram(env)))
                    continue
                providers = [{**p, 'use': 'preferred'} for p in env.providers if p['name'] not in excluded and 'Queen' in p.get('capabilities', [])
                             and not any(w['used_pct'] >= 100 for w in usage.limit_windows(conn, p))]
                cls = str(env.settings.get('queenClass', 'medium'))
                if hive:
                    history = [a for a in runtime.list_agents(conn, env.name, 'all') if a.get('plan_path') == path and a.get('phase') == 'queen']
                    if history and interrupt.kind_of(history[-1]) == interrupt.VANISHED and history[-1].get('class'):
                        cls = history[-1]['class']
                    requested = hive.get('requested_provider') or hive['provider']
                    providers = [p for p in providers if p['name'] == requested]
                    pick = sch.select_provider(providers, cls, ['Queen'], pool.free, prefer=requested, conn=conn)
                else:
                    pick = sch.select_provider(providers, cls, ['Queen'], pool.free, conn=conn)
                if not pick:
                    not_started(env, path, 'no provider with a free slot and a free limit offers class %s' % cls)
                    continue
                if hive and hive['state'] == 'integration_wait':
                    on = integration_steps.switches(env.settings)
                    low = buildjobs.waiting_for_ram(buildjobs.settings_from(env.settings.get('buildJobs')), free_ram() * 1024.0) if on['build'] or on['deploy'] else None
                    if low:   # below the minimum free RAM before a build: the place is not taken, the Queen waits and is asked again next round (also after a restart)
                        not_started(env, path, 'waiting for the build: ' + low)
                        continue
                    if not take_integration(conn, env.name, path):
                        not_started(env, path, 'waiting for the integration place')
                        continue
                    conn.execute("UPDATE hives SET state='waiting' WHERE project=? AND plan_path=?", (env.name, path))
                    hive = get(conn, env.name, path)
                if dry_run:
                    report.append('START Queen ' + path)
                    continue
                if not hive:
                    from . import bees
                    if bees.foreign_plan_branch(env, path) and not bees.resolve_existing_branch(env, path):
                        not_started(env, path, 'its branch %s exists without Hive history: waiting for the owner to take it over or create a new one' % bees.plan_worktree(env, path)[0])
                        continue
                    used = {r['colour'] for r in db.rows(conn, "SELECT colour FROM hives WHERE state != 'done'")}
                    colour = next((c for c in loop.AGENT_COLOURS if c not in used), None)
                    if colour is None:
                        colour = next(format(i, '06X') for i in range(1, 0x1000000) if format(i, '06X') not in used)
                    hive = create(conn, env.name, path, pick['name'], colour)
                claimed = conn.execute("UPDATE hives SET state='starting',claimed_at=? WHERE project=? AND plan_path=? AND state IN ('waiting','requeen')", (db.now(), env.name, path))
                if claimed.rowcount != 1:
                    not_started(env, path, 'the Hive is already claimed')
                    continue
                existing_names = [a['name'] for a in runtime.list_agents(conn, env.name, 'all')]
                predecessors = [a for a in runtime.list_agents(conn, env.name, 'all') if a.get('plan_path') == path and a.get('phase') == 'queen' and a.get('person')]
                name = loop.agent_identity(conn, [predecessors[-1]['person']] if predecessors else ())['Person']
                row_key, k = name, 1
                while row_key in existing_names:   # an earlier agent of this project carried the name: the row key gets a number, the Queen keeps her plain name
                    k += 1
                    row_key = '%s-%d' % (name, k)
                name = row_key
                questions = [q for q in runtime.list_questions(conn, env.name, plan=path) if q['status'] in ('open', 'triage') and not str(q['item']).upper().startswith('STALL-')]
                prompt = 'Work the entire plan %s. The plan, its state file, the handoff of your predecessor, the project rules, the decisions and owner answers of your plan and those released for the whole Apiary are loaded below. Check for new answers while you work. Open questions: %s. Continue in the plan worktree and branches; if none exists, create the plan worktree and branch according to the project rules before changing files. Record the worktree as the Queen cwd with bzz agents update-fields --project %s --name %s --cwd \'<absolute worktree path>\' (single quotes, no JSON). Work independent packages, answer the completeness question and integrate only when no requirements remain. ' % (env.workspace / path, json.dumps(questions, ensure_ascii=False), env.name, name)
                prompt += integration_steps.start_text(env, path, pick['name'])
                if plan['status'] not in plan_mod.CUT_STATUSES:   # a plan still to be cut gets its step packages when it is integrated
                    integration_steps.sync(env, path, pick['name'])
                from . import bees
                prompt += '\n' + bees.queen_instructions(cls, env.settings.get('beesPerQueen', 3), env.settings.get('escalateAfter', 3), sch.context_limit(env.settings, env.providers, pick['name'], cls), roles.language(env.settings))
                topics = integration_steps.owner_topics_text(env)
                if topics:
                    prompt += '\n' + topics
                prompt += '\nConfigured Bee providers: ' + json.dumps([{'name': p['name'], 'protocol': p['protocol'], 'classes': p.get('classes', {}), 'capabilities': p.get('capabilities', []), 'local': bool(p.get('endpoint') or p.get('server')), 'beeMode': p.get('beeMode')} for p in env.providers if p['name'] not in machine.get('excludedProviders', [])], ensure_ascii=False)
                prompt += ' Project integration configuration: ' + json.dumps(integration_steps.configuration(env, path), ensure_ascii=False)
                if plan['status'] in plan_mod.CUT_STATUSES:   # a plan the owner sent as free text: the Queen cuts it into packages before any Bee starts
                    prompt += '\n' + roles.role_text('queen-cut', {'project': env.name, 'plan': path})
                prior = [routine.view(a) for a in runtime.list_agents(conn, env.name, 'all') if a.get('plan_path') == path and a.get('cwd') and Path(a['cwd']).is_dir()]
                cwd = routine.pick_cwd(prior, str(env.code_repo), workspace=routine.other_workspace(env)) if prior else str(env.code_repo)
                keep = bool(prior) and bees.valid_queen_worktree(env, cwd) and bees.branch_is_derived(env, path)   # a Hive with a worktree keeps it; the derived branch is only for a plan without one
                if (bees.plan_worktree(env, path) and not keep) or not bees.valid_queen_worktree(env, cwd):
                    try:
                        cwd = bees.prepare_queen_worktree(env, path)
                    except (OSError, ValueError) as exc:
                        env.log('cannot prepare Queen worktree: ' + str(exc))
                        cwd = None
                    if not cwd:
                        conn.execute("UPDATE hives SET state='waiting',claimed_at=NULL WHERE project=? AND plan_path=?", (env.name, path))
                        named = bees.plan_worktree(env, path)
                        not_started(env, path, 'worktree for branch %s could not be prepared (%s)' % named if named else 'no branch or worktree named in the plan or its state file')
                        continue
                action = {'name': name, 'type': pick['name'], 'phase': 'queen', 'plan': path, 'package': '', 'cwd': cwd, 'prompt': prompt, 'class': cls}
                st = {'names': existing_names, 'busy': [], 'plan_status': {sch.norm_plan(path): plan['status']}, 'by_path': {sch.norm_plan(path): plan}}
                ctx = {'env': env, 'state': st, 'pool': pool, 'report': report, 'dry': False, 'settle': settle}
                try:
                    count = loop._do_start(ctx, action, forced_pick=pick)
                except Exception:
                    conn.execute("UPDATE hives SET state='waiting',claimed_at=NULL WHERE project=? AND plan_path=? AND state='starting'", (env.name, path))
                    raise
                if count:
                    # the embedded Bees of the old Queen keep their rows and places until their end is confirmed
                    bees.after_queen_start(conn, env.name, path)
                    runtime.kv_set(conn, env.name, 'hive-blocker:' + path, '')
                    for package in runtime.list_packages(conn, env.name, path):
                        runtime.kv_delete(conn, env.name, "wait:%s|%s" % (path, package["id"]))
                    env.update_agent(name, {'color': hive['colour']})
                    queen = runtime.get_agent(conn, env.name, name)
                    conn.execute("UPDATE hive_integration SET owner_pid=? WHERE project=? AND plan_path=? AND acquired=1", (queen.get('pid'), env.name, path))
                    conn.execute("UPDATE hives SET state='running',provider=?,requested_provider=NULL,reset_at=NULL,claimed_at=NULL WHERE project=? AND plan_path=?", (pick['name'], env.name, path))
                    queen_count += 1
                    started += 1
            result['projects'][env.name]['started'] += started
            result['projects'][env.name]['actions'] += started
    if not dry_run:
        for env in projects:
            providers = [{**p, 'use': 'preferred'} for p in env.providers if p['name'] not in excluded and 'Queen' in p.get('capabilities', [])
                         and not any(w['used_pct'] >= 100 for w in usage.limit_windows(conn, p))]
            eligible = any(p['status'] in plan_mod.QUEEN_STATUSES and ((get(conn, env.name, p['path']) or {}).get('state') in (None, 'waiting', 'requeen')) for p in runtime.list_plans(conn, env.name))
            can_start = eligible and queen_count < limit and bool(sch.select_provider(providers, str(env.settings.get('queenClass', 'medium')), ['Queen'], pool.free, conn=conn))
            env.log('round done: %d Queen(s) started%s' % (result['projects'][env.name]['started'], ''.join('; ' + text for text in skipped[env.name])))
            try:
                update_waiting(conn, env, alive)
            except Exception as exc:
                env.log('plan status not updated: %s' % exc)
            faults.check_round(env, result['projects'][env.name]['started'], can_start)
            try:
                views.write_views(conn, env.name)
            except Exception as exc:
                env.log('views not written: %s' % exc)
            loop.run_hook(env, loop._state_summary(env))
    if not dry_run:
        loop.write_state(conn, 'idle', datetime.now(), None)
    return result


# Summarize only changes that can make a waiting Hive runnable.
def wake_marker(conn, envs):
    """Return a compact marker for agent exits, answers, inbox and reset deadlines."""
    from . import inbox
    from . import scheduler as sch
    envs_by_name = {e.name: e for e in envs}
    agents = []
    for row in runtime.list_agents(conn, None, 'active'):
        status = row.get('status')
        if status not in ('running', 'starting') and not (row.get('phase') in ('queen', 'bee') and row.get('pid')):
            continue
        pid = row.get('pid')
        live = agent_process.is_live(row)
        owner = envs_by_name.get(row['project'])
        limit = sch.context_limit(owner.settings, owner.providers, sch.row_provider(row), row.get('class')) if owner else 300000
        context_reached = row.get('phase') == 'queen' and int(row.get('ctxTokens') or 0) >= limit
        agents.append((row['id'], status, pid, live, context_reached))
    questions = tuple((r['id'], r['status']) for r in db.rows(conn, 'SELECT id,status FROM questions ORDER BY id'))
    hive_rows = tuple((r['project'], r['plan_path'], r['state'], r['reset_at'], r['requested_provider']) for r in db.rows(conn, 'SELECT project,plan_path,state,reset_at,requested_provider FROM hives ORDER BY project,plan_path'))
    places = tuple((r['id'], r['acquired'], r['owner_pid'], bool(r['owner_pid'] and osplat.process_alive(r['owner_pid']))) for r in db.rows(conn, 'SELECT id,acquired,owner_pid FROM hive_integration ORDER BY id'))
    due = bool(conn.execute("SELECT 1 FROM hives WHERE state IN ('resting','codex_wait','provider_wait') AND reset_at IS NOT NULL AND reset_at<=? LIMIT 1", (db.now(),)).fetchone())
    files = []
    for env in envs:
        folder = inbox.inbox_dir(env.workspace)
        if folder.is_dir():
            for path in folder.glob('*.toml'):
                try:
                    stat = path.stat()
                    files.append((str(path), stat.st_size, stat.st_mtime_ns))
                except OSError:
                    continue
    state = runtime.kv_get(conn, '*', 'run-state')
    return (tuple(agents), questions, hive_rows, places, due, tuple(sorted(files)), state)

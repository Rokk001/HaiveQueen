"""Persistent Hive state and project integration order."""
import json
import os
import re
from . import db, runtime
from . import platform as osplat


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
    return bool(row and row['state'] not in ('handoff', 'queenless', 'resting', 'answer', 'integration_wait', 'codex_wait', 'provider_wait', 'done'))


# Return whether all independent Bee processes of the Hive have finished.
def can_handoff(conn, project, plan):
    """Return whether all independent Bee processes of the Hive have finished."""
    return not any(a.get('plan_path') == plan and a.get('phase') == 'bee' and (a.get('status') in ('running', 'starting') or (a.get('pid') and osplat.process_alive(a['pid']))) for a in runtime.list_agents(conn, project, 'active'))


# End this Hive's Queen and own-process Bees, leaving it queenless.
def dequeen(conn, project, plan, stop=None):
    """End this Hive's Queen and own-process Bees, leaving it queenless."""
    if get(conn, project, plan) is None:
        raise ValueError('unknown Hive')
    stop = stop or osplat.kill_process_tree
    rows = [a for a in runtime.list_agents(conn, project, 'active') if a.get('plan_path') == plan and a.get('phase') in ('queen', 'bee') and (a.get('status') in ('running', 'starting') or (a.get('pid') and osplat.process_alive(a['pid'])))]
    for row in rows:
        if row.get('pid') and stop(row['pid']) is False:
            raise ValueError('could not end Hive process')
        runtime.upsert_agent(conn, project, row['name'], {'status': 'done', 'pid': None, 'ended_at': db.now(), 'last_line': 'dequeened'})
    with db.transaction(conn):
        conn.execute("UPDATE hives SET state='queenless',reset_at=NULL,requested_provider=NULL WHERE project=? AND plan_path=?", (project, plan))
        conn.execute("DELETE FROM hive_integration WHERE project=? AND plan_path=?", (project, plan))


# Request a replacement Queen, preserving the provider unless explicitly changed.
def requeen(conn, project, plan, provider=None, stop=None, providers=None, queen_class="medium", excluded=()):
    """Request a replacement Queen, preserving the provider unless explicitly changed."""
    if get(conn, project, plan) is None:
        raise ValueError('unknown Hive')
    if provider and (provider in excluded or providers is not None and not any(p['name'] == provider and not p.get('endpoint') and p.get('classes', {}).get(queen_class, {}).get('model') for p in providers)):
        raise ValueError('provider does not offer the Queen class')
    if any(a.get('plan_path') == plan and a.get('phase') == 'queen' and (a.get('status') in ('running', 'starting') or (a.get('pid') and osplat.process_alive(a['pid']))) for a in runtime.list_agents(conn, project, 'active')):
        dequeen(conn, project, plan, stop=stop)
    with db.transaction(conn):
        conn.execute("UPDATE hives SET state='requeen',requested_provider=?,reset_at=NULL WHERE project=? AND plan_path=?", (provider, project, plan))


# Collect Hives, their Queen and the latest reported step of each Bee (shown in the frame of the Hive pane).
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
    for hive in rows:
        active = [a for a in agents if a.get('project') == hive['project'] and a.get('plan_path') == hive['plan_path']]
        hive['queen'] = next((a for a in active if a.get('phase') == 'queen'), None)
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
    return rows


# German words for the state of a Hive that has no running Queen.
def state_label(hive):
    """German words for the state of a Hive without a running Queen ('' for a running one)."""
    state = hive.get('state')
    if state == 'resting':
        return 'ruht bis ' + str(hive.get('reset_at') or '?').replace('T', ' ')[:16]
    return {'queenless': 'weisellos', 'integration_wait': 'wartet auf Integration', 'answer': 'wartet auf Antwort', 'codex_wait': 'wartet auf Codex',
            'provider_wait': 'wartet auf Codex', 'handoff': 'Übergabe', 'waiting': 'wartet auf Start', 'starting': 'wartet auf Start',
            'requeen': 'wartet auf Start'}.get(state, '')


# Resolve the four named migration plans from the existing plan source.
def migration_targets(env):
    """Return uniquely named migration plan paths, or None when the source is unclear."""
    import re
    from pathlib import Path
    rows = runtime.list_plans(env.conn, env.name)
    pattern = re.compile(r'(?i)\b(?:HaiveQueen|HQ)\s+plans\s+in\s+work\s*\(([^)]*)\)')
    lists = []
    for row in rows:
        try:
            lines = (env.workspace / row['path']).read_text(encoding='utf-8-sig').splitlines()
        except OSError:
            return None
        for line in lines:
            if re.match(r'^\s*-\s*D25\b', line):
                lists.extend(m.group(1) for m in pattern.finditer(line))
    if len(lists) != 1:
        return None
    names = [name.strip().casefold() for name in lists[0].split(',')]
    if len(names) != 4 or len(set(names)) != 4 or not all(names):
        return None
    targets = []
    for name in names:
        matches = [row['path'] for row in rows if re.sub(r'(?i)-plan$', '', Path(row['path']).stem).casefold() == name]
        if len(matches) != 1:
            return None
        targets.append(matches[0])
    return targets


# Pause only the named tool plans and clear obsolete locks after the first resume.
def migrate_once(conn, envs):
    """Migrate the specified plans once after resume, preserving all other plans."""
    if runtime.kv_get(conn, '*', 'hives-migration') != 'armed':
        return {}
    registered = {row['name'] for row in db.rows(conn, "SELECT name FROM projects")}
    loaded = {env.name for env in envs}
    missing = registered - loaded
    targets = {}
    deferred = {}
    pending = [env for env in envs if runtime.kv_get(conn, env.name, 'hives-migrated') != 'done']
    for env in pending:
        runtime.sync_plans(conn, env.name, str(env.workspace))
        if env.name.lower() in ('haivequeen', 'hq'):
            legacy = conn.execute("SELECT 1 FROM agents WHERE project=? AND phase IN ('work','verify','release','integrate') LIMIT 1", (env.name,)).fetchone()
            targets[env.name] = migration_targets(env)
            if targets[env.name] is None and legacy:
                env.log('migration target list is missing or ambiguous')
                deferred[env.name] = 'migration target list missing or ambiguous'
                continue
            if targets[env.name] is None:
                targets[env.name] = []
    for env in pending:
        if env.name in deferred:
            continue
        for plan in runtime.list_plans(conn, env.name):
            if plan['path'] in targets.get(env.name, ()) and plan['status'] == 'IN_PROGRESS':
                runtime.set_plan_status(conn, env.name, plan['path'], 'PAUSED', str(env.workspace))
        with db.transaction(conn):
            conn.execute("UPDATE packages SET locked=0,lock_reason=NULL WHERE project=?", (env.name,))
            conn.execute("DELETE FROM kv WHERE project=? AND (key LIKE 'stall:%' OR key LIKE 'planstall:%')", (env.name,))
            conn.execute("INSERT INTO kv(project,key,value,updated_at) VALUES(?, 'hives-migrated', 'done', ?) ON CONFLICT(project,key) DO UPDATE SET value='done',updated_at=excluded.updated_at", (env.name, db.now()))
    if not missing and not deferred:
        runtime.kv_set(conn, '*', 'hives-migration', 'done')
    return deferred


# Run one machine-wide scheduling round for Hives without a language model.
def schedule_round(conn, machine, envs, terminal=None, dry_run=False, free_ram=None, alive=None, settle=3.0, poll=2.0, tries=20, plan_files=None):
    """Run one machine-wide scheduling round for Hives without a language model."""
    from datetime import datetime, timedelta
    from pathlib import Path
    from . import loop, routine, runstate, scheduler as sch, usage
    from . import faults, inbox, views
    free_ram = free_ram or loop.ram_gb
    result = {'skipped': None, 'projects': {}}
    if not dry_run:
        try:
            usage.refresh_codex_usage(conn)
        except Exception:
            pass
        for env in envs:
            inbox.process_inbox(env)
    if not dry_run:
        if loop.stop_flag().exists():
            result['skipped'] = 'STOP'
        elif not runstate.starts_allowed(conn):
            result['skipped'] = runstate.get_state(conn)['state']
        else:
            low = next((env for env in envs if free_ram() < loop._min_ram(env)), None)
            if low:
                result['skipped'] = 'free RAM %.2f GB is below the minimum %.2f GB' % (free_ram(), loop._min_ram(low))
        if result['skipped']:
            for env in envs:
                env.log('round skipped (%s)' % result['skipped'])
                loop.run_hook(env, loop._state_summary(env))
            return result
    deferred = {}
    if not dry_run:
        deferred = migrate_once(conn, envs)
        reap_integrations(conn, alive=alive)
        wake_due(conn, db.now())
    envs = [env for env in envs if env.name not in deferred]
    for env in envs:
        runtime.sync_plans(conn, env.name, str(env.workspace))
        if not dry_run:
            routine.repair_stale_agents(env, alive=alive)
            runtime.archive_agents(conn, env.name, 12)
    all_active = runtime.list_agents(conn, None, 'active')
    is_alive = alive or osplat.process_alive

    # Count an agent until its runner has actually exited.
    # Count an agent until its runner has actually exited.
    def process_running(row):
        """Count an agent until its runner has actually exited."""
        return row.get('status') in ('running', 'starting') or bool(row.get('pid') and is_alive(row['pid']))

    processes = [{**a, 'status': 'running'} for a in all_active if process_running(a)]
    free = sch.provider_free(envs[0].providers if envs else [], processes)
    pool = loop.Pool(free, 0)
    limit = int(machine.get('hives', 2))
    queen_count = sum(1 for a in processes if a.get('phase') == 'queen')
    excluded = set(machine.get('excludedProviders') or [])
    preferred = str(machine.get('preferredProvider') or '')
    projects = sch.order_projects(envs)
    result['projects'] = {env.name: {'report': [], 'started': 0, 'actions': 0} for env in projects}
    result['projects'].update({name: {'report': [reason], 'started': 0, 'actions': 0} for name, reason in deferred.items()})
    skipped = {env.name: [] for env in projects}

    # Remember why a plan got no Queen this round; the reasons are logged with the round line.
    def not_started(env, path, reason):
        """Remember why a plan got no Queen this round."""
        skipped[env.name].append('%s not started: %s' % (Path(path).stem, reason))

    for existing_pass in (True, False):
        for env in projects:
            report = result["projects"][env.name]["report"]
            started = 0
            rows = runtime.list_plans(conn, env.name)
            ordered = []
            for plan in rows:
                if plan['status'] not in ('READY', 'IN_PROGRESS'):
                    continue
                if plan_files and not any(sch.norm_plan(plan['path']).endswith(sch.norm_plan(f)) for f in plan_files):
                    continue
                hive = get(conn, env.name, plan['path'])
                if bool(hive) != existing_pass:
                    continue
                if hive and hive.get("ready_id") and conn.execute("SELECT 1 FROM queue WHERE id=? AND project=? AND plan_path=? AND status='integrated'", (hive["ready_id"], env.name, plan['path'])).fetchone():
                    if not dry_run:
                        runtime.set_plan_status(conn, env.name, plan['path'], 'DONE', str(env.workspace))
                        release_integration(conn, env.name, plan['path'])
                        conn.execute("UPDATE hives SET state='done' WHERE project=? AND plan_path=?", (env.name, plan['path']))
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
                    flagged = routine.set_handoff_flags(env, [routine.view(a) for a in running]) if not dry_run else []
                    if flagged and hive:
                        context_limit(conn, env.name, plan['path'])
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
                        questions = [q for q in runtime.list_questions(conn, env.name, status='open', plan=plan['path']) if not str(q['item']).upper().startswith('STALL-')]   # stop-rule questions never block a Hive
                        if last and last.get('status') == 'done' and 'WAITING_FOR_OWNER' in final_report and questions:
                            wait_for_answers(conn, env.name, plan['path'], [q['id'] for q in questions])
                        else:
                            conn.execute("UPDATE hives SET state='waiting' WHERE project=? AND plan_path=?", (env.name, plan['path']))
                    hive = get(conn, env.name, plan['path'])
                if hive and hive['state'] in ('codex_wait', 'provider_wait'):
                    from . import bees
                    wait = conn.execute('SELECT provider FROM bee_provider_wait WHERE project=? AND plan_path=?', (env.name, plan['path'])).fetchone()
                    provider = sch.get_provider(env.providers, wait[0]) if wait else None
                    limited = not provider or any(w['used_pct'] >= 100 for w in usage.limit_windows(conn, provider))
                    if not provider or not bees.provider_available(conn, env.name, plan['path'], provider['name'], pool.free.get(provider['name'], 0), limited):
                        not_started(env, plan['path'], 'waiting for a free slot or limit of provider %s' % (provider['name'] if provider else wait[0] if wait else '?'))
                        continue
                    hive = get(conn, env.name, plan['path'])
                if hive and hive['state'] in ('queenless', 'resting', 'answer', 'handoff'):
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
                providers = [{**p, 'use': 'preferred'} for p in env.providers if p['name'] not in excluded and not p.get('endpoint')
                             and not any(w['used_pct'] >= 100 for w in usage.limit_windows(conn, p))]
                cls = str(env.settings.get('queenClass', 'medium'))
                if hive:
                    requested = hive.get('requested_provider') or hive['provider']
                    providers = [p for p in providers if p['name'] == requested]
                    pick = sch.select_provider(providers, cls, [], pool.free, prefer=requested, conn=conn)
                else:
                    pick = sch.select_provider(providers, cls, [], pool.free, prefer=preferred, conn=conn)
                if not pick:
                    not_started(env, path, 'no provider with a free slot and a free limit offers class %s' % cls)
                    continue
                if hive and hive['state'] == 'integration_wait':
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
                    if bees.foreign_plan_branch(env, path):
                        not_started(env, path, 'its branch %s is held by work outside the Beekeeper' % bees.plan_worktree(env, path)[0])
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
                if name in existing_names:   # an earlier agent of this project carried the name: take the next free one from the list
                    name = next((n for n in loop.AGENT_NAMES if n not in existing_names), name)
                questions = [q for q in runtime.list_questions(conn, env.name, plan=path) if q['status'] in ('open', 'triage') and not str(q['item']).upper().startswith('STALL-')]
                prompt = 'Work the entire plan %s. Read its state file, predecessor handoff, project rules, and all owner answers. Check for new answers while you work. Open questions: %s. Continue in the plan worktree and branches; if none exists, create the plan worktree and branch according to the project rules before changing files. Record the worktree as the Queen cwd with bzz agents update-fields --project %s --name %s --fields using a JSON object with the absolute cwd. Work independent packages, answer the completeness question and integrate only when no requirements remain. Register your own branch with bzz ready, then use bzz integration acquire --project %s --plan %s. Use the returned buildJobs EnvVar and Jobs for the integration build. If it reports waiting for integration, write a handoff and end until restarted. After acquiring the place, abort any unfinished merge in the project integration checkout before integrating again. Release the integration place with bzz integration release --project %s --plan %s after integration. If only open owner questions remain, write a handoff and end your final report with WAITING_FOR_OWNER.' % (env.workspace / path, json.dumps(questions, ensure_ascii=False), env.name, name, env.name, path, env.name, path)
                integration = {key: env.settings.get(key) for key in ('integrationBranch', 'compileCheck', 'integratorInstructions', 'buildFlowDoc') if env.settings.get(key)}
                configured_prompt = (env.settings.get('prompts') or {}).get('integrator')
                if configured_prompt:
                    integration['steps'] = sch.expand_template(str(configured_prompt), {**routine.base_values(env), 'plan': path})
                from . import bees
                prompt += '\n' + bees.queen_instructions(cls, env.settings.get('beesPerQueen', 3), env.settings.get('escalateAfter', 3), env.settings.get('contextLimitTokens', 300000))
                prompt += '\nConfigured Bee providers: ' + json.dumps([{'name': p['name'], 'protocol': p['protocol'], 'classes': p.get('classes', {}), 'capabilities': p.get('capabilities', []), 'local': bool(p.get('endpoint'))} for p in env.providers if p['name'] not in machine.get('excludedProviders', [])], ensure_ascii=False)
                prompt += ' Project integration configuration: ' + json.dumps(integration, ensure_ascii=False)
                prior = [routine.view(a) for a in runtime.list_agents(conn, env.name, 'all') if a.get('plan_path') == path and a.get('cwd') and Path(a['cwd']).is_dir()]
                cwd = routine.pick_cwd(prior, str(env.code_repo), workspace=routine.other_workspace(env)) if prior else str(env.code_repo)
                if bees.plan_worktree(env, path) or not bees.valid_queen_worktree(env, cwd):
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
                ctx = {'env': env, 'state': st, 'pool': pool, 'report': report, 'dry': False, 'terminal': terminal, 'settle': settle, 'poll': poll, 'tries': tries}
                try:
                    count = loop._do_start(ctx, action, forced_pick=pick)
                except Exception:
                    conn.execute("UPDATE hives SET state='waiting',claimed_at=NULL WHERE project=? AND plan_path=? AND state='starting'", (env.name, path))
                    raise
                if count:
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
    if not dry_run and not os.environ.get('BZZ_TEST_NOSTART'):
        ensure_hive_panes(conn, projects, terminal)
    if not dry_run:
        for env in projects:
            providers = [{**p, 'use': 'preferred'} for p in env.providers if p['name'] not in excluded and not p.get('endpoint')
                         and not any(w['used_pct'] >= 100 for w in usage.limit_windows(conn, p))]
            eligible = any(p['status'] in ('READY', 'IN_PROGRESS') and ((get(conn, env.name, p['path']) or {}).get('state') in (None, 'waiting', 'requeen')) for p in runtime.list_plans(conn, env.name))
            can_start = eligible and queen_count < limit and bool(sch.select_provider(providers, str(env.settings.get('queenClass', 'medium')), [], pool.free, prefer=preferred, conn=conn))
            env.log('round done: %d Queen(s) started%s' % (result['projects'][env.name]['started'], ''.join('; ' + text for text in skipped[env.name])))
            faults.check_round(env, result['projects'][env.name]['started'], can_start)
            try:
                views.write_views(conn, env.name)
            except Exception as exc:
                env.log('views not written: %s' % exc)
            loop.run_hook(env, loop._state_summary(env))
    if not dry_run:
        loop.write_state(conn, 'idle', datetime.now(), None)
    return result


# Make sure every living Hive has its sub-terminal, also when no Queen runs.
def ensure_hive_panes(conn, envs, terminal=None):
    """Open the sub-terminal of every Hive that is not done and has none; a terminal problem is logged and never stops the round."""
    from . import loop
    from .terminal import get_terminal
    for env in envs:
        for hive in db.rows(conn, "SELECT plan_path FROM hives WHERE project=? AND state!='done'", (env.name,)):
            try:
                term = terminal or get_terminal(env.settings.get('terminal', 'auto'), str(osplat.runs_dir() / '_panes'))
                loop.ensure_hive_pane(env, term, env.settings.get('window', 'HaiveQueen'), hive['plan_path'])
            except Exception as exc:
                env.log('Hive pane of %s not opened: %s' % (hive['plan_path'], exc))
    if envs:   # big Hives with a running Queen, small ones without; a backend that cannot resize is skipped
        from . import hivelayout
        try:
            hivelayout.apply(terminal or get_terminal(envs[0].settings.get('terminal', 'auto'), str(osplat.runs_dir() / '_panes')),
                             envs[0].settings.get('window', 'HaiveQueen'), conn, loop.is_test_runtime())
        except Exception:
            pass


# Summarize only changes that can make a waiting Hive runnable.
def wake_marker(conn, envs):
    """Return a compact marker for agent exits, answers, inbox and reset deadlines."""
    from . import inbox
    limits = {e.name: int(e.settings.get('contextLimitTokens', 300000)) for e in envs}
    agents = []
    for row in runtime.list_agents(conn, None, 'active'):
        status = row.get('status')
        if status not in ('running', 'starting') and not (row.get('phase') in ('queen', 'bee') and row.get('pid')):
            continue
        pid = row.get('pid')
        live = bool(pid and osplat.process_alive(pid))
        context_reached = row.get('phase') == 'queen' and int(row.get('ctxTokens') or 0) >= limits.get(row['project'], 300000)
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

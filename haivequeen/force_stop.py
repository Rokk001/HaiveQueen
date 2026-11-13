"""The owner-confirmed global stop, shared by web, CLI and tray controls."""
import json
import os
import signal
import time
from concurrent.futures import ThreadPoolExecutor

from . import agent_process, bees, db, integration, interrupt, loop, orchestrator, proctree, runtime
from . import platform as osplat

WARNING = 'Force stop ends all running Queens, Agents, Builds and Deploys immediately. Unfinished work will be done again after Start.'


def requested():
    try:
        return loop.stop_flag().read_text(encoding='utf-8').startswith('force stop requested ')
    except FileNotFoundError:
        return False


def terminate_loop(pid):
    """End the loop alone: its detached web service and tray must survive."""
    command = osplat.process_command_line(pid) or ''
    if not ('haivequeen' in command.lower() and 'start' in command and '--foreground' in command):
        raise ValueError('Cannot verify the Orchestrator process; refusing to end it')
    if osplat.is_windows():
        osplat._win_terminate(pid)
    else:
        os.kill(pid, signal.SIGKILL)
    deadline = time.monotonic() + 5
    while osplat.process_alive(pid) and time.monotonic() < deadline:
        time.sleep(0.01)
    return not osplat.process_alive(pid)


def stop(conn, kill=None, end_loop=None):
    """Stop all registered work without pausing plans or charging failed attempts."""
    kill = kill or osplat.kill_process_tree
    end_loop = end_loop or terminate_loop
    loop.stop_flag().parent.mkdir(parents=True, exist_ok=True)
    loop.stop_flag().write_text('force stop requested ' + db.now() + '\n', encoding='utf-8')
    errors = []
    with loop.hive_schedule_lock():
        rows = [a for a in runtime.list_agents(conn, scope='all') if not a.get('archived') and agent_process.counts(a)]
        states = [(r['project'], r['key'][len(orchestrator.KEY):], json.loads(r['value']))
                  for r in db.rows(conn, "SELECT project,key,value FROM kv WHERE key LIKE 'orch:%'")]
        victims = {int(a['pid']) for a in rows if a.get('pid') and not agent_process.pid_reused(a)}
        for _, _, state in states:
            if (state.get('job') or {}).get('pid'):
                victims.add(int(state['job']['pid']))
        # Detached checks can outlive their requesting Queen or the loop.
        checks = []
        for row in db.rows(conn, "SELECT project,key,value FROM kv WHERE key LIKE 'compile-request:%'"):
            token = row['key'][len(integration.COMPILE_REQUEST_KEY):]
            if runtime.kv_get(conn, row['project'], integration.COMPILE_RUN_KEY + token):
                continue
            request = json.loads(row['value'])
            supervisor = request.get('supervisor_pid')
            if supervisor and request.get('supervisor_identity') and osplat.process_identity(supervisor) == request['supervisor_identity']:
                victims.add(int(supervisor))
            pid = request.get('check_pid')
            if pid and request.get('check_identity') and osplat.process_identity(pid) == request['check_identity']:
                victims.add(int(pid))
            checks.append((row['project'], token, request))
        for holder in integration._read_holders(integration._compile_lock_path()):
            if holder.get('run_token') in {token for _, token, _ in checks} and str(holder.get('database')) == str(config_database(conn)):
                command = osplat.process_command_line(holder['pid']) or ''
                if 'compile_supervisor' in command and holder['run_token'] in command:
                    victims.add(holder['pid'])
        pid = loop.loop_running()
        if pid:
            try:
                if end_loop(pid) is False:
                    errors.append('Could not end the Orchestrator')
            except (OSError, ValueError) as exc:
                errors.append(str(exc))
        # Work is ended in parallel, so one slow process tree cannot hold up the rest.
        victims.discard(os.getpid())
        with ThreadPoolExecutor(max_workers=max(1, min(32, len(victims)))) as pool:
            futures = {pool.submit(kill, pid): pid for pid in victims}
            for future, victim in futures.items():
                try:
                    if future.result() is False:
                        errors.append('Could not end process %s' % victim)
                except OSError as exc:
                    errors.append(str(exc))
        if errors:
            return {'ok': False, 'error': '; '.join(errors), 'warning': WARNING}
        for row in rows:
            runtime.upsert_agent(conn, row['project'], row['name'], {'status': 'done', 'pid': None, 'ended_at': db.now(), 'last_line': runtime.STOPPED_LINE, 'interrupted': interrupt.VANISHED, 'retryAt': ''})
        bees.ensure_attempts(conn)
        conn.execute("UPDATE embedded_bees SET ended_at=?,outcome='interrupted' WHERE ended_at IS NULL", (db.now(),))
        # Clear only unfinished pipeline steps; completed steps and plan status stay.
        for project, plan, state in states:
            state.pop('job', None)
            state['steps'] = {k: v for k, v in state.get('steps', {}).items() if v != 'running'}
            state['placed'] = False
            if state.get('fix'):
                if state['fix'].get('agent'):
                    key = orchestrator.ATTEMPTS_KEY + plan
                    attempts = json.loads(runtime.kv_get(conn, project, key) or '{}')
                    if attempts.get('n'):
                        attempts['n'] = max(0, int(attempts['n']) - 1)
                        runtime.kv_set(conn, project, key, json.dumps(attempts))
                state['fix']['agent'] = None
            orchestrator.save(conn, project, plan, state)
        for project, token, request in checks:
            result = integration._aborted_result(request, 'Force stopped by the owner')
            runtime.kv_set(conn, project, integration.COMPILE_RUN_KEY + token, json.dumps(result))
            runtime.kv_set(conn, project, request['key'], json.dumps(result))
        conn.execute('DELETE FROM hive_integration')
        for victim in victims:
            proctree.exempt_clear(victim)
    return {'ok': True, 'running_agents': 0, 'warning': WARNING}


def config_database(conn):
    return conn.execute('PRAGMA database_list').fetchone()[2]

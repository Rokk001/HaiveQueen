"""The owner-confirmed global stop, shared by web, CLI and tray controls."""
import json
import os
import re
import shlex
import signal
import time
from concurrent.futures import ThreadPoolExecutor

from . import agent_process, bees, db, integration, interrupt, loop, orchestrator, proctree, runtime
from . import platform as osplat

WARNING = 'Force stop ends all running Queens, Agents, Builds and Deploys immediately. Unfinished work will be done again after Start.'


def has_work(conn):
    """Include orphaned agents, pipeline jobs and unfinished detached checks."""
    if any(agent_process.counts(row) for row in runtime.list_agents(conn)):
        return True
    for row in db.rows(conn, "SELECT value FROM kv WHERE key LIKE 'orch:%'"):
        if (json.loads(row['value']).get('job') or {}).get('pid'):
            return True
    for row in db.rows(conn, "SELECT project,key FROM kv WHERE key LIKE 'compile-request:%'"):
        token = row['key'][len(integration.COMPILE_REQUEST_KEY):]
        if not runtime.kv_get(conn, row['project'], integration.COMPILE_RUN_KEY + token):
            return True
    return False


def requested():
    try:
        return loop.stop_flag().read_text(encoding='utf-8').startswith('force stop requested ')
    except FileNotFoundError:
        return False


def _foreground_loop_command(command):
    """Recognize supported CLI entry points, never arbitrary argument substrings."""
    windows = osplat.is_windows()
    try:
        args = shlex.split(command, posix=not windows)
    except ValueError:
        return False
    if windows:
        args = [arg[1:-1] if arg.startswith('"') and arg.endswith('"') else arg for arg in args]

    def basename(value):
        return value.replace('\\', '/').rsplit('/', 1)[-1].lower()

    if not args:
        return False
    if re.fullmatch(r'pythonw?(?:\d+(?:\.\d+)*)?(?:\.exe)?', basename(args[0])):
        args = args[1:]
        while args and args[0] in ('-I', '-u', '-B', '-E', '-s', '-S', '-O', '-OO'):
            args = args[1:]
        if args[:2] in (['-m', 'haivequeen'], ['-m', 'haivequeen.cli']):
            args = args[2:]
        elif args and (basename(args[0]) in ('bzz', 'bzz.exe') or
                       args[0].replace('\\', '/').lower().endswith('/haivequeen/cli.py')):
            args = args[1:]
        else:
            return False
    elif basename(args[0]) in ('bzz', 'bzz.exe'):
        args = args[1:]
    else:
        return False
    return bool(args and args[0] == 'start' and '--foreground' in args[1:] and '--' not in args)


def terminate_loop(pid):
    """End the loop alone: its detached web service and tray must survive."""
    command = osplat.process_command_line(pid) or ''
    if not _foreground_loop_command(command):
        raise ValueError('Cannot verify the Orchestrator process; refusing to end it')
    if osplat.is_windows():
        osplat._win_terminate(pid)
    else:
        os.kill(pid, signal.SIGKILL)
    deadline = time.monotonic() + 5
    while osplat.process_alive(pid) and time.monotonic() < deadline:
        time.sleep(0.01)
    return not osplat.process_alive(pid)


def _snapshot(conn):
    """Read registered work without waiting for scheduling to finish."""
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
    return rows, states, victims, checks


def _end_work(victims, kill, errors):
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


def stop(conn, kill=None, end_loop=None):
    """Stop all registered work without pausing plans or charging failed attempts."""
    kill = kill or osplat.kill_process_tree
    end_loop = end_loop or terminate_loop
    loop.stop_flag().parent.mkdir(parents=True, exist_ok=True)
    loop.stop_flag().write_text('force stop requested ' + db.now() + '\n', encoding='utf-8')
    errors = []
    # A scheduling round may be stuck while holding the lock. End the verified
    # loop first; never let that lock delay termination of registered work.
    pid = loop.loop_running()
    if pid:
        try:
            if end_loop(pid) is False:
                errors.append('Could not end the Orchestrator')
        except (OSError, ValueError) as exc:
            errors.append(str(exc))
    _, _, victims, _ = _snapshot(conn)
    _end_work(victims, kill, errors)
    try:
        with loop.hive_schedule_lock(timeout=0):
            # Re-read under the lock to catch work registered during termination.
            rows, states, current, checks = _snapshot(conn)
            _end_work(current - victims, kill, errors)
            victims.update(current)
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
    except TimeoutError as exc:
        errors.append('Force-stop cleanup is pending: %s' % exc)
        return {'ok': False, 'error': '; '.join(errors), 'warning': WARNING}
    return {'ok': True, 'running_agents': 0, 'warning': WARNING}


def config_database(conn):
    return conn.execute('PRAGMA database_list').fetchone()[2]

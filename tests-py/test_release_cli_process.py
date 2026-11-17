import json
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
import threading
import os
from pathlib import Path
import shutil
import subprocess
import sys
import time
import zipfile
from unittest.mock import patch

from bzztest import RoundCase
from haivequeen import db, force_stop, hives, loop, orchestrator, platform, release_compat, releases, runtime, scheduler, selfupdate, updates, version_layout, web_service


def read_line(process):
    import queue
    q = queue.Queue()
    threading.Thread(target=lambda: q.put(process.stdout.readline().strip()), daemon=True).start()
    try:
        return q.get(timeout=140)
    except queue.Empty:
        raise AssertionError('browser milestone timed out')

class ReleaseCliProcessTest(RoundCase):
    def test_compatible_worker_switches_real_core_without_ending_jobs(self):
        self._compatible_switch(public=False)

    def test_public_install_starts_compatible_release_worker(self):
        self._compatible_switch(public=True)

    def test_public_install_preserves_launched_agents(self):
        self._compatible_switch(public=True, launched=True)

    def test_public_install_successor_schedules_startable_plan(self):
        self._compatible_switch(public=True, launched=True, scheduling=True)

    def test_forced_update_resumes_existing_plan(self):
        self._compatible_switch(public=True, incompatible=True, force=True, resume=True)

    def test_public_install_successor_harvests_detached_jobs(self):
        self._compatible_switch(public=True, adoption=True)

    def test_public_install_stages_downloaded_archive(self):
        self._compatible_switch(public=True, download=True, launched=True, adoption=True)

    def test_published_unknown_defers_without_stopping_jobs(self):
        self._compatible_switch(public=True, download=True, incompatible=True, reason=release_compat.UNKNOWN_REASON)

    def test_published_unknown_cannot_be_forced(self):
        self._compatible_switch(public=True, download=True, incompatible=True, force=True, reason=release_compat.UNKNOWN_REASON)

    def test_public_install_rolls_back_after_missing_successor_receipt(self):
        self._compatible_switch(public=True, rollback=True)

    def test_incompatible_public_install_waits_for_jobs_then_switches(self):
        self._compatible_switch(public=True, download=True, incompatible=True, database=True, adoption=True, resume=True,
                                reason="Database column providers_usage.actions becomes actions_json; older HQ usage writers require actions (affected versions: 1.0.0)")

    def test_web_api_install_and_force_reach_worker(self):
        self._compatible_switch(public=True, incompatible=True, force=True, web=True)

    def test_browser_install_and_confirmed_force_reach_worker(self):
        self._compatible_switch(public=True, incompatible=True, force=True, web='browser')

    def test_tray_confirmation_and_force_reach_worker(self):
        self._compatible_switch(public=True, incompatible=True, force=True, web='tray')

    def test_shared_settings_change_waits_for_jobs_then_switches(self):
        self._compatible_switch(public=True, incompatible=True, adoption=True, reason="shared settings format change")

    def test_incompatible_public_install_forces_jobs_then_switches(self):
        self._compatible_switch(public=True, incompatible=True, force=True)

    def test_shared_settings_change_forces_jobs_then_switches(self):
        self._compatible_switch(public=True, incompatible=True, force=True, reason="shared settings format change")

    def _browser_prepare(self, root):
        from haivequeen import web_auth
        candidates = [shutil.which(name) for name in ('msedge', 'chromium', 'google-chrome')]
        for var in ('PROGRAMFILES(X86)', 'PROGRAMFILES'):
            if os.environ.get(var):
                candidates.append(str(Path(os.environ[var]) / 'Microsoft/Edge/Application/msedge.exe'))
        browser = next((p for p in candidates if p and Path(p).is_file()), None)
        if not browser:
            self.skipTest('Chromium is required')
        import socket
        with socket.socket() as sock:
            sock.bind(('127.0.0.1', 0))
            port = sock.getsockname()[1]
        web_auth.setup(self.base / 'cfg' / 'config.toml', 'test', 'test', '127.0.0.1', port, '', '')
        web_auth.off(self.base / 'cfg' / 'config.toml')
        return browser

    def _browser_force(self, browser, jobs, detached, reason):
        status = web_service.control('status')
        self.assertTrue(status and status['running'], str(status))
        profile = self.base / 'browser-profile'
        driver = Path(__file__).with_name('browser_update_driver.cjs')
        if not shutil.which('node'):
            self.skipTest('Node is required')
        log = (self.base / 'browser-driver.log').open('w', encoding='utf-8')
        # Chromium keeps its certificate store and crash reports in the XDG folders, not in the profile.
        # HOME stays: a Snap browser does not start with a home below the temp folder.
        home = self.base / 'browser-home'
        browser_env = dict(os.environ, XDG_CONFIG_HOME=str(home / 'config'), XDG_DATA_HOME=str(home / 'data'))
        process = subprocess.Popen(['node', str(driver), browser, status['url']+'/login', str(profile), reason],
                                   stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=log, text=True, env=browser_env)
        try:
            milestone = read_line(process)
            self.assertEqual(milestone, 'CANCELLED', (self.base / 'browser-driver.log').read_text(encoding='utf-8')[-6000:])
            self.assertTrue(all(job.poll() is None for job in jobs))
            self.assertTrue(all(job.process.poll() is None for job, _, _, _ in detached))
            process.stdin.write('continue\n'); process.stdin.flush()
            self.assertEqual(read_line(process), 'CONFIRMED')
            out, err = process.communicate(timeout=30)
            self.assertEqual(process.returncode, 0, str(out))
        finally:
            if process.poll() is None:
                process.terminate(); process.wait(timeout=10)
            process.stdin.close()
            process.stdout.close()
            log.close()

    def _compatible_switch(self, public, download=False, rollback=False, incompatible=False, force=False,
                           reason="shared schema change", adoption=False, launched=False, scheduling=False, resume=False, web=False, database=False):
        settings_change = reason == 'shared settings format change'
        if incompatible and reason == 'shared schema change':
            database = True
            reason = ('Database column providers_usage.actions becomes actions_json; '
                      'older HQ usage writers require actions (affected versions: 1.0.0)')
        root = self.base / 'install'
        source = Path(__file__).resolve().parent.parent
        server = None
        requests = []
        from release_authority_fixture import authority, responses, package_files, database_break, settings_break
        from haivequeen import release_staging
        reply = {}
        class ReleaseHandler(BaseHTTPRequestHandler):
            def do_GET(self):
                requests.append((self.path, self.headers.get('User-Agent')))
                suffix = '/' + self.path.split('/', 4)[4]
                data = reply.get(suffix)
                self.send_response(200 if data is not None else 404)
                data = data or b'not found'
                self.send_header('Content-Length', str(len(data)))
                self.end_headers()
                self.wfile.write(data)
            def log_message(self, format, *args):
                pass
        server = ThreadingHTTPServer(('127.0.0.1', 0), ReleaseHandler)
        thread = threading.Thread(target=server.serve_forever, daemon=True)
        thread.start()
        files = package_files(source)
        api = 'http://127.0.0.1:%s/repos/' % server.server_port
        files['haivequeen/release_staging.py'] = files['haivequeen/release_staging.py'].replace(
            b'https://api.github.com/repos/', api.encode())
        # Only test pacing changes; both authenticated releases contain it.
        files['haivequeen/selfupdate.py'] = files['haivequeen/selfupdate.py'].replace(
            b'poll=5.0,\n                       timeout=ROUND_TIMEOUT',
            b'poll=0.1,\n                       timeout=ROUND_TIMEOUT')
        files['haivequeen/release_message.py'] = b'MESSAGE = "old application message"\n'
        if database:
            files['haivequeen/shared-contract.json'] = b'{"format":1,"database":"usage-actions-v1"}\n'
        if rollback:
            # Both authenticated versions contain the same test-only fault hook.
            # Only the target suppresses its first-round receipt.
            files['haivequeen/loop.py'] = files['haivequeen/loop.py'].replace(
                b'            record_completed_round(conn)',
                b"            if Path(__file__).resolve().parents[1].name != '2.0.0': record_completed_round(conn)")
            files['haivequeen/selfupdate.py'] = files['haivequeen/selfupdate.py'].replace(
                b'ROUND_TIMEOUT = 1800.0', b'ROUND_TIMEOUT = 30.0')
        old_authority = authority(files, '1.0.0')
        files = dict(files)
        files['haivequeen/release_message.py'] = b'MESSAGE = "new application message"\n'
        if database:
            files = database_break(files)
        if reason == 'shared settings format change':
            files = settings_break(files)
            reason = 'Shared machine settings move from config.toml to hq-machine.toml (affected versions: 1.0.0)'
        if reason == release_compat.UNKNOWN_REASON:
            files['haivequeen/release_message.py'] = b'import os\nMESSAGE = \"unknown shared behavior\"\n'
        target_authority = authority(files, '2.0.0')
        reply.update(responses({'1.0.0': old_authority, '2.0.0': target_authority}))
        def fetch(address):
            from urllib.request import Request, urlopen
            request = Request(address.replace('https://api.github.com/repos/', api),
                              headers={'User-Agent': 'HaiveQueen'})
            with urlopen(request, timeout=15) as response:
                return response.read()
        release_staging.stage_release(root, updates.repository(self.machine), '1.0.0', fetch)
        if not download:
            release_staging.stage_release(root, updates.repository(self.machine), '2.0.0', fetch)
        version_layout.install_launcher(root)
        version_layout.set_active(root, '1.0.0')
        (self.base / 'cfg' / 'config.toml').write_text(
            'roundMinutes = 0.01\n[web]\nport = 0\n', encoding='utf-8')
        real_names = ('TestQueen', 'TestAgent')
        if launched or resume:
            fake = self.base / 'provider.py'
            fake.write_text('import json,os,time\nfrom pathlib import Path\n'
                            'base=Path(os.environ["TEST_LIVE_BASE"])\n'
                            '(base / (os.environ["BZZ_AGENT"] + "-started")).write_text(os.environ["BZZ_AGENT_CLI"])\n'
                            'while not (base / "release-runners").exists(): time.sleep(.05)\n'
                            'print(json.dumps({"type":"result","is_error":False,"result":"Done."}))\n', encoding='utf-8')
            command = self.base / ('provider.cmd' if os.name == 'nt' else 'provider.sh')
            if os.name == 'nt':
                command.write_text('@"%s" "%s" %%*\r\n' % (sys.executable, fake), encoding='utf-8')
            else:
                command.write_text('#!/bin/sh\nexec "%s" "%s" "$@"\n' % (sys.executable, fake), encoding='utf-8')
                command.chmod(0o755)
            (self.base / 'cfg' / 'config.toml').write_text(
                "roundMinutes=0.01\nslots=4\nramReserveMb=0\n[web]\nport=0\n"
                "[[providers]]\nname='claude'\nprotocol='claude'\ncommand='%s'\nargs=[]\nslots=4\n"
                "capabilities=['Coding','Queen']\n[providers.classes.medium]\nmodel='test'\neffort='medium'\n[providers.classes.heavy]\nmodel='test'\neffort='high'\n"
                % command.as_posix(), encoding='utf-8')
            os.environ['TEST_LIVE_BASE'] = str(self.base)
            self.addCleanup(os.environ.pop, 'TEST_LIVE_BASE', None)
        browser = self._browser_prepare(root) if web == 'browser' else None
        env = dict(os.environ, BZZ_NO_DESKTOP='1')
        if scheduling or resume:
            env.pop('BZZ_TEST_NOSTART', None)
        jobs = []
        detached = []
        core = None
        status = None
        old_queen = None
        try:
            if launched:
                prompt = self.base / 'runner-prompt.md'
                prompt.write_text('TASK\nTest work\n', encoding='utf-8')
                for name, phase in zip(real_names, ('queen', 'bee')):
                    runtime.upsert_agent(self.conn, 'testproj', name,
                                         {'status': 'starting', 'phase': phase, 'provider': 'claude',
                                          'class': 'medium', 'person': name})
                    spec = {'name': name, 'provider': 'claude', 'protocol': 'claude', 'plan': '',
                            'package': '', 'phase': phase, 'cwd': str(self.repo), 'prompt_file': str(prompt)}
                    self.env().run_dir(name).mkdir(parents=True, exist_ok=True)
                    with patch.object(loop, 'TOOLS_DIR', root / 'versions' / '1.0.0'):
                        loop.launch_headless(self.env(), spec)
                deadline = time.monotonic() + 40
                while not all((self.base / (name + '-started')).exists() for name in real_names) and time.monotonic() < deadline:
                    time.sleep(.05)
                self.assertTrue(all((self.base / (name + '-started')).exists() for name in real_names))
                identities = {name: runtime.get_agent(self.conn, 'testproj', name)['pid'] for name in real_names}
            for phase in ('queen', 'bee'):
                code = ('import time,sys; from pathlib import Path\n'
                        'while not Path(sys.argv[1]).exists(): time.sleep(.05)')
                proc = subprocess.Popen([sys.executable, '-c', code, str(self.base / 'finish-agents')], env=env,
                                        start_new_session=(os.name != 'nt'))
                jobs.append(proc)
                db.add_agent(self.conn, 'testproj', name=phase, phase=phase, status='running', pid=proc.pid)
                runtime.upsert_agent(self.conn, 'testproj', phase, {'processIdentity': platform.process_identity(proc.pid)})
            if public:
                task = self.base / 'hold_work.py'
                task.write_text('from pathlib import Path\nimport sys,time\n'
                                'Path(sys.argv[1]).touch()\n'
                                'while not Path(sys.argv[2]).exists(): time.sleep(.05)\n'
                                'print("finished")\n', encoding='utf-8')
                for step in ('Build', 'Deploy'):
                    started = self.base / (step + '-started')
                    finish = self.base / (step + '-finish')
                    result = self.base / (step + '-result.json')
                    command = '"%s" "%s" "%s" "%s"' % (sys.executable, task, started, finish)
                    job = orchestrator.Job('release-' + step.lower())
                    with patch.object(orchestrator, '__file__', str(root / 'versions' / '1.0.0' / 'haivequeen' / 'orchestrator.py')):
                        orchestrator._start_detached_work(job, {}, step, command, self.wt, None, result)
                    detached.append((job, started, finish, result))
                if adoption:
                    for job, _, _, result in detached:
                        step = 'Build' if 'build' in job.token else 'Deploy'
                        plan = 'docs/plans/%s/%s-PLAN.md' % (step.lower(), step.upper())
                        self.plan('PAUSED', rel=plan, branch='feature', worktree=str(self.wt))
                        hives.create(self.conn, 'testproj', plan, 'claude', '123456')
                        self.conn.execute("UPDATE hives SET state='integrating' WHERE project=? AND plan_path=?", ('testproj', plan))
                        orchestrator.save(self.conn, 'testproj', plan,
                                          {'phase': 'pipeline', 'step': step, 'placed': True,
                                           'ran': [], 'steps': {step: 'running'},
                                           'job': {'step': step, 'token': job.token, 'pid': job.pid, 'identity': job.identity, 'detached': True,
                                                   'file': str(result), 'head': 'old-revision'}})
                deadline = time.monotonic() + 20
                while not all(started.exists() for _, started, _, _ in detached) and time.monotonic() < deadline:
                    time.sleep(.05)
                self.assertTrue(all(started.exists() for _, started, _, _ in detached))
            if resume:
                plan_path = 'docs/plans/next/NEXT-PLAN.md'
                self.plan('RUNNING', [{'id': 'P1', 'class': 'medium'}], rel=plan_path,
                          branch='feature', worktree=str(self.base / 'next-worktree'))
                def git(*args):
                    return subprocess.run(['git', '-C', str(self.repo), *args], check=True,
                                          capture_output=True, text=True)
                git('init', '-q', '-b', 'main')
                git('config', 'user.name', 'Test')
                git('config', 'user.email', 'test@example.invalid')
                git('add', '--', '.haivequeen/project.toml', plan_path)
                git('commit', '-qm', 'Create isolated plan')
                git('branch', 'feature')
            if resume:
                hives.create(self.conn, 'testproj', plan_path, 'claude', '123456')
                name = 'PriorQueen'
                runtime.upsert_agent(self.conn, 'testproj', name,
                                     {'status': 'starting', 'phase': 'queen', 'provider': 'claude',
                                      'plan_path': plan_path, 'class': 'medium', 'person': name})
                prompt = self.base / 'prior-prompt.md'
                prompt.write_text('TASK\nTest work\n', encoding='utf-8')
                spec = {'name': name, 'provider': 'claude', 'protocol': 'claude', 'plan': plan_path,
                        'package': '', 'phase': 'queen', 'cwd': str(self.repo), 'prompt_file': str(prompt)}
                self.env().run_dir(name).mkdir(parents=True, exist_ok=True)
                with patch.object(loop, 'TOOLS_DIR', root / 'versions' / '1.0.0'):
                    loop.launch_headless(self.env(), spec)
                deadline = time.monotonic() + 40
                while not (self.base / (name + '-started')).exists() and time.monotonic() < deadline:
                    time.sleep(.05)
                self.assertTrue((self.base / (name + '-started')).exists(), 'prior Queen did not start')
                old_queen = runtime.get_agent(self.conn, 'testproj', name)
                self.assertEqual(Path((self.base / (name + '-started')).read_text()), root / 'versions' / '1.0.0' / 'haivequeen' / 'cli.py')
            core = subprocess.Popen([sys.executable, '-I', str(root / 'launcher.py'), 'start',
                                     '--foreground'], env=env,
                                    stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
            deadline = time.monotonic() + 30
            while not loop.loop_running() and time.monotonic() < deadline:
                time.sleep(.05)
            old_pid = loop.loop_running()
            self.assertTrue(old_pid, 'old core did not start')
            if public:
                if force:
                    for job, _, _, result in detached:
                        step = 'Build' if 'build' in job.token else 'Deploy'
                        orchestrator.save(self.conn, 'testproj', 'force-' + step,
                                          {'phase': 'pipeline', 'steps': {step: 'running'},
                                           'job': {'step': step, 'pid': job.pid, 'identity': job.identity, 'detached': True, 'file': str(result)}})
                updates.save_state({'available': {'version': '2.0.0', 'url': 'https://example.invalid/release'}})
                if web == 'browser':
                    self._browser_force(browser, jobs, detached, reason)
                    request = None
                elif web is True:
                    # Exercise the real API against the installed version and
                    # the detached worker, with an isolated data directory.
                    code = ("import json,sys; sys.path.insert(0,sys.argv[1]); "
                            "from haivequeen import config,db,web_api; "
                            "c=db.connect(config.db_path()); "
                            "print(json.dumps(web_api.execute(c,'updates/install',"
                            "{'version':'2.0.0','confirmed':True})))")
                    command = [sys.executable, '-I', '-c', code, str(root / 'versions' / '1.0.0')]
                else:
                    command = [sys.executable, '-I', str(root / 'launcher.py'), 'update', '--install', '2.0.0']
                if web != 'browser':
                    # Detached descendants can retain inherited pipe
                    # writers. Await the request process with the same
                    # deadline and capture its exact output in owned files,
                    # without waiting for unrelated pipe EOF.
                    import tempfile
                    with tempfile.TemporaryFile(mode='w+', encoding='utf-8') as out, tempfile.TemporaryFile(mode='w+', encoding='utf-8') as err:
                        completed = subprocess.run(command, env=env, stdout=out, stderr=err,
                                                   text=True, timeout=30)
                        out.seek(0)
                        err.seek(0)
                        request = subprocess.CompletedProcess(command, completed.returncode,
                                                              out.read(), err.read())
                    self.assertEqual(request.returncode, 0, request.stdout + request.stderr)
                if web == 'browser':
                    pass
                elif web is True:
                    self.assertIn(json.loads(request.stdout)['how'], ('detached', 'schtasks'))
                else:
                    self.assertEqual(json.loads(request.stdout)['installing'], '2.0.0')
                if reason == release_compat.UNKNOWN_REASON:
                    deadline = time.monotonic() + 30
                    row = None
                    while time.monotonic() < deadline:
                        row = db.last_self_update(self.conn)
                        if row and row['ended']:
                            break
                        time.sleep(.05)
                    self.assertTrue(row and row['ended'], 'unknown release was not rejected')
                    self.assertEqual(row['result'], 'failed')
                    self.assertIn('update deferred', row['note'])
                    self.assertIn('unknown', row['note'].lower())
                    self.assertEqual((root / 'active-version').read_text().strip(), '1.0.0')
                    self.assertEqual(loop.loop_running(), old_pid)
                    self.assertFalse(loop.stop_flag().exists())
                    self.assertTrue(all(job.poll() is None for job in jobs))
                    self.assertTrue(all(job.process.poll() is None for job, _, _, _ in detached))
                    if force:
                        refused = subprocess.run([sys.executable, '-I', str(root / 'launcher.py'), 'update', '--force'],
                                                 env=env, capture_output=True, text=True, timeout=30)
                        self.assertNotEqual(refused.returncode, 0, refused.stdout + refused.stderr)
                        self.assertFalse(selfupdate.release_force_path().exists())
                        self.assertTrue(all(job.poll() is None for job in jobs))
                        self.assertTrue(all(job.process.poll() is None for job, _, _, _ in detached))
                    return
                if incompatible and web == 'browser':
                    for job in jobs:
                        job.wait(timeout=30)
                    for job, _, finish, _ in detached:
                        finish.touch()
                        job.process.wait(timeout=20)
                        self.assertNotEqual(job.process.returncode, 0)
                elif incompatible:
                    deadline = time.monotonic() + 30
                    status = None
                    while time.monotonic() < deadline:
                        try:
                            status = json.loads(selfupdate.release_status_path().read_text(encoding='utf-8'))
                            if status['waiting'] and len(status['jobs']) >= (4 if force or database or settings_change else 2):
                                break
                        except (OSError, ValueError):
                            pass
                        ended = db.last_self_update(self.conn)
                        if database and ended and ended['ended']:
                            self.assertNotEqual(ended['result'], 'failed', str(dict(ended)))
                        time.sleep(.05)
                    self.assertTrue(status and status['waiting'], str(status))
                    self.assertEqual(status['reason'], reason)
                    self.assertEqual({j['kind'] for j in status['jobs']},
                                     {'Queen', 'Agent', 'Build', 'Deploy'} if force or database or settings_change else {'Queen', 'Agent'})
                    self.assertEqual((root / 'active-version').read_text().strip(), '1.0.0')
                    self.assertTrue(loop.stop_flag().exists())
                    self.assertTrue(all(job.poll() is None for job in jobs))
                    if database:
                        self.assertEqual(self.conn.execute('PRAGMA user_version').fetchone()[0], 17)
                        self.assertIn('actions', [r[1] for r in self.conn.execute('PRAGMA table_info(providers_usage)')])
                        self.assertEqual(hives.start_block_reason(self.conn, 'testproj'), 'stopped')
                        from haivequeen import web_api
                        waiting = web_api.execute(self.conn, 'updates', {})['waiting']
                        self.assertEqual(waiting['reason'], reason)
                        self.assertEqual({j['kind'] for j in waiting['jobs']}, {'Queen', 'Agent', 'Build', 'Deploy'})
                        client = (source / 'haivequeen/web-client.js').read_text(encoding='utf-8')
                        self.assertIn('box.title=[updateState.waiting.reason', client)
                        self.assertIn('updateState.waiting.jobs', client)
                    query = subprocess.run([sys.executable, '-I', str(root / 'launcher.py'), 'update'],
                                           env=env, capture_output=True, text=True, timeout=30)
                    reported = json.loads(query.stdout)
                    self.assertTrue(reported['waiting'])
                    self.assertEqual(reported['reason'], status['reason'])
                    self.assertEqual({j['kind'] for j in reported['jobs']},
                                     {'Queen', 'Agent', 'Build', 'Deploy'} if force or database or settings_change else {'Queen', 'Agent'})
                    self.assertTrue(all(job.poll() is None for job in jobs))
                    if force:
                        if web == 'tray':
                            code = ("import sys; sys.path.insert(0,sys.argv[1]); "
                                    "from haivequeen import selfupdate,tray; "
                                    "tray.confirm=lambda:False; tray.invoke(8); "
                                    "assert not selfupdate.release_force_path().exists(); "
                                    "tray.confirm=lambda:True; tray.invoke(8); print('confirmed')")
                            command = [sys.executable, '-I', '-c', code, str(root / 'versions' / '1.0.0')]
                        elif web:
                            code = ("import json,sys; sys.path.insert(0,sys.argv[1]); "
                                    "from haivequeen import config,db,web_api; "
                                    "c=db.connect(config.db_path()); "
                                    "print(json.dumps(web_api.execute(c,'updates/force',{'confirmed':True})))")
                            command = [sys.executable, '-I', '-c', code, str(root / 'versions' / '1.0.0')]
                        else:
                            command = [sys.executable, '-I', str(root / 'launcher.py'), 'update', '--force']
                        forced = subprocess.run(command, env=env, capture_output=True, text=True, timeout=30)
                        self.assertEqual(forced.returncode, 0, forced.stdout + forced.stderr)
                        if web == 'tray':
                            self.assertIn('confirmed', forced.stdout)
                        else:
                            if not web:
                                self.assertIn(force_stop.WARNING, forced.stdout)
                            self.assertTrue(json.loads(forced.stdout.splitlines()[-1])['force_requested'])
                    else:
                        (self.base / 'finish-agents').touch()
                        if database:
                            (self.base / 'release-runners').touch()
                    for job in jobs:
                        job.wait(timeout=30)
                    for job, _, finish, _ in detached:
                        finish.touch()
                        job.process.wait(timeout=20)
                        if force:
                            self.assertNotEqual(job.process.returncode, 0)
                deadline = time.monotonic() + 90
                row = None
                while time.monotonic() < deadline:
                    row = db.last_self_update(self.conn)
                    if row and row['ended']:
                        break
                    time.sleep(.1)
                self.assertTrue(row and row['ended'], 'release worker did not finish: row=%r status=%r pointer=%s pid=%r jobs=%r' % (dict(row) if row else None, status if incompatible else None, (root / 'active-version').read_text(), loop.loop_running(), [job.poll() for job in jobs]))
                self.assertEqual(row['result'], 'rollback' if rollback else 'ok', str(dict(row)))
            else:
                worker = subprocess.run([sys.executable, '-I', str(root / 'versions' / '1.0.0' / 'haivequeen' / 'cli.py'),
                                         'self-update', '--worker', '--version', '2.0.0'],
                                        env=env, capture_output=True, text=True, timeout=90)
                self.assertEqual(worker.returncode, 0, worker.stdout + worker.stderr)
                self.assertEqual(json.loads(worker.stdout)['result'], 'ok')
            self.assertEqual((root / 'active-version').read_text().strip(), '1.0.0' if rollback else '2.0.0')
            if download:
                self.assertTrue((root / 'versions' / '2.0.0' / 'haivequeen' / 'cli.py').is_file())
                self.assertTrue(any(path.endswith('/zipball/' + target_authority['commit']) for path, _ in requests))
                self.assertTrue(any('/git/trees/' in path for path, _ in requests))
                self.assertEqual((root / 'versions' / '2.0.0' / 'haivequeen' / 'release_message.py').read_text(), 'MESSAGE = \"new application message\"\n')
                self.assertTrue(all(agent == 'HaiveQueen' for _, agent in requests))
            if web == 'browser':
                deadline = time.monotonic() + 30
                while time.monotonic() < deadline:
                    web_status = web_service.control('status')
                    if web_status and web_status.get('running'):
                        break
                    time.sleep(.1)
                self.assertTrue(web_status and web_status.get('running'), str(web_status))
            new_pid = loop.loop_running()
            self.assertTrue(new_pid)
            self.assertNotEqual(new_pid, old_pid)
            receipt = json.loads(runtime.kv_get(self.conn, scheduler.GLOBAL, 'loop-completed-round'))
            self.assertEqual(receipt['pid'], new_pid)
            self.assertEqual(receipt['identity'], platform.process_identity(new_pid))
            self.assertEqual(Path(receipt['code_path']), root / 'versions' / ('1.0.0' if rollback else '2.0.0'))
            if database:
                self.assertEqual(self.conn.execute('PRAGMA user_version').fetchone()[0], 18)
                columns = [r[1] for r in self.conn.execute('PRAGMA table_info(providers_usage)')]
                self.assertIn('actions_json', columns)
                self.assertNotIn('actions', columns)
                import sqlite3
                with self.assertRaises(sqlite3.OperationalError):
                    runtime.usage_add(self.conn, actions='old writer')
            if reason.startswith('Shared machine settings move'):
                self.assertFalse((self.base / 'cfg/config.toml').exists())
                self.assertTrue((self.base / 'cfg/hq-machine.toml').is_file())
                self.assertEqual(self.conn.execute('PRAGMA user_version').fetchone()[0], 17)
            if rollback:
                self.assertIn('rolled back', row['note'])
                fault = json.loads(runtime.kv_get(self.conn, scheduler.GLOBAL, 'global-fault:self_update_rollback'))
                self.assertIn('rolled back', fault['text'])
            if launched:
                for name, pid in identities.items():
                    row = runtime.get_agent(self.conn, 'testproj', name)
                    self.assertEqual(row['pid'], pid)
                    self.assertEqual(row['status'], 'running')
                    self.assertTrue(platform.process_alive(pid))
                    self.assertEqual(Path((self.base / (name + '-started')).read_text()),
                                     root / 'versions' / '1.0.0' / 'haivequeen' / 'cli.py')
                (self.base / 'release-runners').touch()
                deadline = time.monotonic() + 30
                while time.monotonic() < deadline:
                    if all(runtime.get_agent(self.conn, 'testproj', name)['status'] != 'running' for name in real_names):
                        break
                    time.sleep(.05)
                self.assertTrue(all(runtime.get_agent(self.conn, 'testproj', name)['status'] == 'done' for name in real_names))
            if resume:
                self.assertFalse(platform.process_alive(old_queen['pid']))
                (self.base / 'release-runners').touch()
                deadline = time.monotonic() + 50
                scheduled = None
                while time.monotonic() < deadline:
                    rows = self.conn.execute("SELECT name FROM agents WHERE project=? AND plan_path=? AND phase='queen' AND name != ?", ('testproj', plan_path, old_queen['name'])).fetchall()
                    if rows:
                        scheduled = runtime.get_agent(self.conn, 'testproj', rows[-1]['name'])
                        marker = self.base / (scheduled['name'] + '-started')
                        if marker.exists() and scheduled['status'] == 'done':
                            break
                    time.sleep(.1)
                self.assertIsNotNone(scheduled, 'successor did not resume existing plan')
                self.assertTrue(marker.exists(), 'successor Queen did not start')
                self.assertEqual(Path(marker.read_text()), root / 'versions' / '2.0.0' / 'haivequeen' / 'cli.py')
                self.assertEqual(scheduled['status'], 'done')
            if scheduling:
                plan_path = 'docs/plans/next/NEXT-PLAN.md'
                self.plan('RUNNING', [{'id': 'P1', 'class': 'medium'}], rel=plan_path,
                          branch='feature', worktree=str(self.base / 'next-worktree'))
                def git(*args):
                    return subprocess.run(['git', '-C', str(self.repo), *args], check=True,
                                          capture_output=True, text=True)
                git('init', '-q', '-b', 'main')
                git('config', 'user.name', 'Test')
                git('config', 'user.email', 'test@example.invalid')
                git('add', '--', '.haivequeen/project.toml', plan_path)
                git('commit', '-qm', 'Create isolated plan')
                git('update-ref', 'refs/remotes/origin/main', 'HEAD')
                deadline = time.monotonic() + 40
                row = None
                while time.monotonic() < deadline:
                    rows = self.conn.execute("SELECT name, status FROM agents WHERE project=? AND plan_path=? AND phase='queen'", ('testproj', plan_path)).fetchall()
                    if rows:
                        row = rows[-1]
                        break
                    time.sleep(.1)
                self.assertIsNotNone(row, 'successor did not schedule a Queen: ' + repr([dict(r) for r in self.conn.execute("SELECT kind,text FROM events ORDER BY id DESC LIMIT 12")]))
                marker = self.base / (row['name'] + '-started')
                deadline = time.monotonic() + 30
                while not marker.exists() and time.monotonic() < deadline:
                    time.sleep(.05)
                self.assertTrue(marker.exists(), 'scheduled Queen did not start')
                self.assertEqual(Path(marker.read_text()), root / 'versions' / '2.0.0' / 'haivequeen' / 'cli.py')
                deadline = time.monotonic() + 30
                while time.monotonic() < deadline:
                    scheduled = runtime.get_agent(self.conn, 'testproj', row['name'])
                    if scheduled['status'] != 'running':
                        break
                    time.sleep(.05)
                self.assertEqual(scheduled['status'], 'done')
            if not incompatible:
                self.assertTrue(all(job.poll() is None for job in jobs))
                self.assertTrue(all(job.process.poll() is None for job, _, _, _ in detached))
            for job, _, finish, result in detached:
                finish.touch()
                job.process.wait(timeout=20)
                if not force:
                    self.assertEqual(job.process.returncode, 0)
                    self.assertTrue(result.exists())
            if adoption:
                deadline = time.monotonic() + 30
                while time.monotonic() < deadline:
                    states = [orchestrator.load(self.conn, 'testproj',
                              'docs/plans/%s/%s-PLAN.md' % (step.lower(), step.upper()))
                              for step in ('Build', 'Deploy')]
                    if all(state and state['steps'].get(step) == 'done'
                           for state, step in zip(states, ('Build', 'Deploy'))):
                        break
                    time.sleep(.05)
                for state, step in zip(states, ('Build', 'Deploy')):
                    self.assertIsNotNone(state)
                    self.assertEqual(state['steps'][step], 'done')
                    self.assertNotIn('job', state)
                    self.assertEqual(state['step'], 'Close')
                self.assertEqual(states[1]['deployed'], 'old-revision')
        finally:
            if resume:
                (self.base / 'release-runners').touch()
                deadline = time.monotonic() + 30
                while old_queen and platform.process_alive(old_queen['pid']) and time.monotonic() < deadline:
                    time.sleep(.05)
            if launched:
                (self.base / 'release-runners').touch()
                deadline = time.monotonic() + 30
                while time.monotonic() < deadline:
                    rows = [runtime.get_agent(self.conn, 'testproj', name) for name in real_names]
                    if all(not row or not row.get('pid') or not platform.process_alive(row['pid']) for row in rows):
                        break
                    time.sleep(.05)
            if server is not None:
                server.shutdown()
                server.server_close()
                thread.join(timeout=10)
            for job, _, finish, _ in detached:
                finish.touch()
                if job.process.poll() is None:
                    job.process.wait(timeout=20)
            for job in jobs:
                if job.poll() is None:
                    job.terminate()
                job.wait(timeout=10)
            loop.stop_flag().parent.mkdir(parents=True, exist_ok=True)
            loop.stop_flag().touch()
            deadline = time.monotonic() + 30
            while loop.loop_running() and time.monotonic() < deadline:
                time.sleep(.05)
            if core is not None:
                core.wait(timeout=10)
            # A core can return before its independently spawned web service
            # publishes its control endpoint. Stop that owned service once it
            # is reachable and wait for its inherited log handle to close.
            deadline = time.monotonic() + 30
            log = self.base / 'data' / 'web-service.log'
            while True:
                web_service.stop()
                try:
                    if log.exists():
                        probe = log.with_suffix('.closed')
                        log.rename(probe)
                        probe.rename(log)
                    break
                except PermissionError:
                    if time.monotonic() >= deadline:
                        raise
                    time.sleep(.05)

            # Await only the test-owned successor's inherited log handle.
            deadline = time.monotonic() + 30
            core_log = platform.runs_dir() / 'beekeeper.log'
            while core_log.exists():
                try:
                    probe = core_log.with_suffix('.closed')
                    core_log.rename(probe)
                    probe.rename(core_log)
                    break
                except PermissionError:
                    if time.monotonic() >= deadline:
                        raise
                    time.sleep(.05)

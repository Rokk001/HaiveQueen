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
from haivequeen import db, force_stop, hives, loop, orchestrator, platform, runtime, scheduler, selfupdate, updates, version_layout, web_service


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
        self._compatible_switch(public=True, download=True)

    def test_public_install_rolls_back_after_missing_successor_receipt(self):
        self._compatible_switch(public=True, rollback=True)

    def test_incompatible_public_install_waits_for_jobs_then_switches(self):
        self._compatible_switch(public=True, incompatible=True)

    def test_web_api_install_and_force_reach_worker(self):
        self._compatible_switch(public=True, incompatible=True, force=True, web=True)

    def test_shared_settings_change_waits_for_jobs_then_switches(self):
        self._compatible_switch(public=True, incompatible=True, reason="shared settings format change")

    def test_incompatible_public_install_forces_jobs_then_switches(self):
        self._compatible_switch(public=True, incompatible=True, force=True)

    def test_shared_settings_change_forces_jobs_then_switches(self):
        self._compatible_switch(public=True, incompatible=True, force=True, reason="shared settings format change")

    def _compatible_switch(self, public, download=False, rollback=False, incompatible=False, force=False,
                           reason="shared schema change", adoption=False, launched=False, scheduling=False, resume=False, web=False):
        root = self.base / 'install'
        source = Path(__file__).resolve().parent.parent
        for version in (('1.0.0',) if download else ('1.0.0', '2.0.0')):
            dest = root / 'versions' / version
            shutil.copytree(source / 'haivequeen', dest / 'haivequeen',
                            ignore=shutil.ignore_patterns('__pycache__', '*.pyc'))
            shutil.copytree(source / 'roles', dest / 'roles')
        server = None
        requests = []
        if download:
            archive = self.base / 'release.zip'
            with zipfile.ZipFile(archive, 'w', zipfile.ZIP_DEFLATED) as release:
                for folder in ('haivequeen', 'roles'):
                    for path in (source / folder).rglob('*'):
                        if path.is_file() and '__pycache__' not in path.parts and path.suffix != '.pyc':
                            release.write(path, 'release/' + path.relative_to(source).as_posix())
                release.writestr('release/haivequeen-update.json', '{"compatible_with_older": true}')
            payload = archive.read_bytes()

            class ReleaseHandler(BaseHTTPRequestHandler):
                def do_GET(self):
                    requests.append((self.path, self.headers.get('User-Agent')))
                    self.send_response(200)
                    self.send_header('Content-Length', str(len(payload)))
                    self.end_headers()
                    self.wfile.write(payload)

                def log_message(self, format, *args):
                    pass

            server = ThreadingHTTPServer(('127.0.0.1', 0), ReleaseHandler)
            thread = threading.Thread(target=server.serve_forever, daemon=True)
            thread.start()
            # Redirect only the URL in this isolated installation; Request and
            # urlopen execute unchanged in the detached release worker.
            staging = root / 'versions' / '1.0.0' / 'haivequeen' / 'release_staging.py'
            code = staging.read_text(encoding='utf-8')
            code = code.replace('https://api.github.com/repos/',
                                'http://127.0.0.1:%s/repos/' % server.server_port)
            staging.write_text(code, encoding='utf-8')
        else:
            (root / 'versions' / '2.0.0' / 'haivequeen-update.json').write_text(
                (json.dumps({'compatible_with_older': False, 'reason': reason})
                 if incompatible else '{"compatible_with_older": true}'), encoding='utf-8')
        if rollback:
            # The test version runs the real CLI and loop but cannot publish a
            # completed-round receipt. Shorten only the isolated worker timeout.
            target_loop = root / 'versions' / '2.0.0' / 'haivequeen' / 'loop.py'
            code = target_loop.read_text(encoding='utf-8')
            code = code.replace('            record_completed_round(conn)', '            pass  # simulate a failed first round')
            target_loop.write_text(code, encoding='utf-8')
            worker_code = root / 'versions' / '1.0.0' / 'haivequeen' / 'selfupdate.py'
            code = worker_code.read_text(encoding='utf-8')
            code = code.replace('ROUND_TIMEOUT = 1800.0', 'ROUND_TIMEOUT = 8.0')
            code = code.replace('poll: float = 5.0, round_timeout:', 'poll: float = 0.1, round_timeout:')
            worker_code.write_text(code, encoding='utf-8')
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
                "capabilities=['Coding','Queen']\n[providers.classes.medium]\nmodel='test'\neffort='medium'\n"
                % command.as_posix(), encoding='utf-8')
            os.environ['TEST_LIVE_BASE'] = str(self.base)
            self.addCleanup(os.environ.pop, 'TEST_LIVE_BASE', None)
        env = dict(os.environ, BZZ_NO_DESKTOP='1')
        if scheduling or resume:
            env.pop('BZZ_TEST_NOSTART', None)
        jobs = []
        detached = []
        core = None
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
                proc = subprocess.Popen([sys.executable, '-c', code, str(self.base / 'finish-agents')], env=env)
                jobs.append(proc)
                db.add_agent(self.conn, 'testproj', name=phase, phase=phase, status='running', pid=proc.pid)
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
                                           'job': {'step': step, 'token': job.token, 'pid': job.pid,
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
                                           'job': {'step': step, 'pid': job.pid, 'file': str(result)}})
                updates.save_state({'available': {'version': '2.0.0', 'url': 'https://example.invalid/release'}})
                if web:
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
                request = subprocess.run(command, env=env, capture_output=True, text=True, timeout=30)
                self.assertEqual(request.returncode, 0, request.stdout + request.stderr)
                if web:
                    self.assertIn(json.loads(request.stdout)['how'], ('detached', 'schtasks'))
                else:
                    self.assertEqual(json.loads(request.stdout)['installing'], '2.0.0')
                if incompatible:
                    deadline = time.monotonic() + 30
                    status = None
                    while time.monotonic() < deadline:
                        try:
                            status = json.loads(selfupdate.release_status_path().read_text(encoding='utf-8'))
                            if status['waiting'] and len(status['jobs']) >= (4 if force else 2):
                                break
                        except (OSError, ValueError):
                            pass
                        time.sleep(.05)
                    self.assertTrue(status and status['waiting'], str(status))
                    self.assertEqual(status['reason'], reason)
                    self.assertEqual({j['kind'] for j in status['jobs']},
                                     {'Queen', 'Agent', 'Build', 'Deploy'} if force else {'Queen', 'Agent'})
                    self.assertEqual((root / 'active-version').read_text().strip(), '1.0.0')
                    self.assertTrue(loop.stop_flag().exists())
                    self.assertTrue(all(job.poll() is None for job in jobs))
                    query = subprocess.run([sys.executable, '-I', str(root / 'launcher.py'), 'update'],
                                           env=env, capture_output=True, text=True, timeout=30)
                    reported = json.loads(query.stdout)
                    self.assertTrue(reported['waiting'])
                    self.assertEqual(reported['reason'], status['reason'])
                    self.assertEqual({j['kind'] for j in reported['jobs']},
                                     {'Queen', 'Agent', 'Build', 'Deploy'} if force else {'Queen', 'Agent'})
                    self.assertTrue(all(job.poll() is None for job in jobs))
                    if force:
                        if web:
                            code = ("import json,sys; sys.path.insert(0,sys.argv[1]); "
                                    "from haivequeen import config,db,web_api; "
                                    "c=db.connect(config.db_path()); "
                                    "print(json.dumps(web_api.execute(c,'updates/force',{'confirmed':True})))")
                            command = [sys.executable, '-I', '-c', code, str(root / 'versions' / '1.0.0')]
                        else:
                            command = [sys.executable, '-I', str(root / 'launcher.py'), 'update', '--force']
                        forced = subprocess.run(command, env=env, capture_output=True, text=True, timeout=30)
                        self.assertEqual(forced.returncode, 0, forced.stdout + forced.stderr)
                        if not web:
                            self.assertIn(force_stop.WARNING, forced.stdout)
                        self.assertTrue(json.loads(forced.stdout.splitlines()[-1])['force_requested'])
                    else:
                        (self.base / 'finish-agents').touch()
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
                self.assertEqual(len(requests), 1)
                self.assertTrue(requests[0][0].endswith('/zipball/v2.0.0'))
                self.assertEqual(requests[0][1], 'HaiveQueen')
            new_pid = loop.loop_running()
            self.assertTrue(new_pid)
            self.assertNotEqual(new_pid, old_pid)
            receipt = json.loads(runtime.kv_get(self.conn, scheduler.GLOBAL, 'loop-completed-round'))
            self.assertEqual(receipt['pid'], new_pid)
            self.assertEqual(receipt['identity'], platform.process_identity(new_pid))
            self.assertEqual(Path(receipt['code_path']), root / 'versions' / ('1.0.0' if rollback else '2.0.0'))
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
                git('branch', 'feature')
                deadline = time.monotonic() + 40
                row = None
                while time.monotonic() < deadline:
                    rows = self.conn.execute("SELECT name, status FROM agents WHERE project=? AND plan_path=? AND phase='queen'", ('testproj', plan_path)).fetchall()
                    if rows:
                        row = rows[-1]
                        break
                    time.sleep(.1)
                self.assertIsNotNone(row, 'successor did not schedule a Queen')
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
            web_service.stop()

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
from haivequeen import db, force_stop, loop, orchestrator, platform, runtime, scheduler, selfupdate, updates, version_layout, web_service


class ReleaseCliProcessTest(RoundCase):
    def test_compatible_worker_switches_real_core_without_ending_jobs(self):
        self._compatible_switch(public=False)

    def test_public_install_starts_compatible_release_worker(self):
        self._compatible_switch(public=True)

    def test_public_install_stages_downloaded_archive(self):
        self._compatible_switch(public=True, download=True)

    def test_public_install_rolls_back_after_missing_successor_receipt(self):
        self._compatible_switch(public=True, rollback=True)

    def test_incompatible_public_install_waits_for_jobs_then_switches(self):
        self._compatible_switch(public=True, incompatible=True)

    def test_shared_settings_change_waits_for_jobs_then_switches(self):
        self._compatible_switch(public=True, incompatible=True, reason="shared settings format change")

    def test_incompatible_public_install_forces_jobs_then_switches(self):
        self._compatible_switch(public=True, incompatible=True, force=True)

    def _compatible_switch(self, public, download=False, rollback=False, incompatible=False, force=False,
                           reason="shared schema change"):
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
        env = dict(os.environ, BZZ_NO_DESKTOP='1')
        jobs = []
        detached = []
        core = None
        try:
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
                deadline = time.monotonic() + 20
                while not all(started.exists() for _, started, _, _ in detached) and time.monotonic() < deadline:
                    time.sleep(.05)
                self.assertTrue(all(started.exists() for _, started, _, _ in detached))
            core = subprocess.Popen([sys.executable, '-I', str(root / 'launcher.py'), 'start',
                                     '--foreground'], env=env,
                                    stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
            deadline = time.monotonic() + 30
            while not loop.loop_running() and time.monotonic() < deadline:
                time.sleep(.05)
            old_pid = loop.loop_running()
            self.assertTrue(old_pid, 'old core did not start')
            if public:
                updates.save_state({'available': {'version': '2.0.0', 'url': 'https://example.invalid/release'}})
                request = subprocess.run([sys.executable, '-I', str(root / 'launcher.py'),
                                          'update', '--install', '2.0.0'], env=env,
                                         capture_output=True, text=True, timeout=30)
                self.assertEqual(request.returncode, 0, request.stdout + request.stderr)
                self.assertEqual(json.loads(request.stdout)['installing'], '2.0.0')
                if incompatible:
                    deadline = time.monotonic() + 30
                    status = None
                    while time.monotonic() < deadline:
                        try:
                            status = json.loads(selfupdate.release_status_path().read_text(encoding='utf-8'))
                            if status['waiting'] and len(status['jobs']) >= 2:
                                break
                        except (OSError, ValueError):
                            pass
                        time.sleep(.05)
                    self.assertTrue(status and status['waiting'], str(status))
                    self.assertEqual(status['reason'], reason)
                    self.assertEqual({j['kind'] for j in status['jobs']}, {'Queen', 'Agent'})
                    self.assertEqual((root / 'active-version').read_text().strip(), '1.0.0')
                    self.assertTrue(loop.stop_flag().exists())
                    self.assertTrue(all(job.poll() is None for job in jobs))
                    query = subprocess.run([sys.executable, '-I', str(root / 'launcher.py'), 'update'],
                                           env=env, capture_output=True, text=True, timeout=30)
                    reported = json.loads(query.stdout)
                    self.assertTrue(reported['waiting'])
                    self.assertEqual(reported['reason'], status['reason'])
                    self.assertEqual({j['kind'] for j in reported['jobs']}, {'Queen', 'Agent'})
                    self.assertTrue(all(job.poll() is None for job in jobs))
                    if force:
                        for job, _, _, result in detached:
                            step = 'Build' if 'build' in job.token else 'Deploy'
                            orchestrator.save(self.conn, 'testproj', 'force-' + step,
                                              {'phase': 'pipeline', 'steps': {step: 'running'},
                                               'job': {'step': step, 'pid': job.pid, 'file': str(result)}})
                        forced = subprocess.run([sys.executable, '-I', str(root / 'launcher.py'), 'update', '--force'],
                                                env=env, capture_output=True, text=True, timeout=30)
                        self.assertEqual(forced.returncode, 0, forced.stdout + forced.stderr)
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
            if not incompatible:
                self.assertTrue(all(job.poll() is None for job in jobs))
                self.assertTrue(all(job.process.poll() is None for job, _, _, _ in detached))
            for job, _, finish, result in detached:
                finish.touch()
                job.process.wait(timeout=20)
                if not force:
                    self.assertEqual(job.process.returncode, 0)
                    self.assertTrue(result.exists())
        finally:
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

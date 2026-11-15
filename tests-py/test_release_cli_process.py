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
from haivequeen import db, loop, orchestrator, platform, runtime, scheduler, updates, version_layout, web_service


class ReleaseCliProcessTest(RoundCase):
    def test_compatible_worker_switches_real_core_without_ending_jobs(self):
        self._compatible_switch(public=False)

    def test_public_install_starts_compatible_release_worker(self):
        self._compatible_switch(public=True)

    def test_public_install_stages_downloaded_archive(self):
        self._compatible_switch(public=True, download=True)

    def _compatible_switch(self, public, download=False):
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
                '{"compatible_with_older": true}', encoding='utf-8')
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
                proc = subprocess.Popen([sys.executable, '-c', 'import time; time.sleep(120)'], env=env)
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
                deadline = time.monotonic() + 90
                row = None
                while time.monotonic() < deadline:
                    row = db.last_self_update(self.conn)
                    if row and row['ended']:
                        break
                    time.sleep(.1)
                self.assertTrue(row and row['ended'], 'release worker did not finish')
                self.assertEqual(row['result'], 'ok', str(dict(row)))
            else:
                worker = subprocess.run([sys.executable, '-I', str(root / 'versions' / '1.0.0' / 'haivequeen' / 'cli.py'),
                                         'self-update', '--worker', '--version', '2.0.0'],
                                        env=env, capture_output=True, text=True, timeout=90)
                self.assertEqual(worker.returncode, 0, worker.stdout + worker.stderr)
                self.assertEqual(json.loads(worker.stdout)['result'], 'ok')
            self.assertEqual((root / 'active-version').read_text().strip(), '2.0.0')
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
            self.assertEqual(Path(receipt['code_path']), root / 'versions' / '2.0.0')
            self.assertTrue(all(job.poll() is None for job in jobs))
            self.assertTrue(all(job.process.poll() is None for job, _, _, _ in detached))
            for job, _, finish, result in detached:
                finish.touch()
                job.process.wait(timeout=20)
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

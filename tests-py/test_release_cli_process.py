import json
import os
from pathlib import Path
import shutil
import subprocess
import sys
import time

from bzztest import RoundCase
from haivequeen import db, loop, platform, runtime, scheduler, version_layout, web_service


class ReleaseCliProcessTest(RoundCase):
    def test_compatible_worker_switches_real_core_without_ending_jobs(self):
        root = self.base / 'install'
        source = Path(__file__).resolve().parent.parent
        for version in ('1.0.0', '2.0.0'):
            dest = root / 'versions' / version
            shutil.copytree(source / 'haivequeen', dest / 'haivequeen',
                            ignore=shutil.ignore_patterns('__pycache__', '*.pyc'))
            shutil.copytree(source / 'roles', dest / 'roles')
        (root / 'versions' / '2.0.0' / 'haivequeen-update.json').write_text(
            '{"compatible_with_older": true}', encoding='utf-8')
        version_layout.install_launcher(root)
        version_layout.set_active(root, '1.0.0')
        (self.base / 'cfg' / 'config.toml').write_text(
            'roundMinutes = 0.01\n[web]\nport = 0\n', encoding='utf-8')
        env = dict(os.environ, BZZ_NO_DESKTOP='1')
        jobs = []
        core = None
        try:
            for phase in ('queen', 'bee'):
                proc = subprocess.Popen([sys.executable, '-c', 'import time; time.sleep(120)'], env=env)
                jobs.append(proc)
                db.add_agent(self.conn, 'testproj', name=phase, phase=phase, status='running', pid=proc.pid)
            core = subprocess.Popen([sys.executable, '-I', str(root / 'launcher.py'), 'start',
                                     '--foreground'], env=env,
                                    stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
            deadline = time.monotonic() + 30
            while not loop.loop_running() and time.monotonic() < deadline:
                time.sleep(.05)
            old_pid = loop.loop_running()
            self.assertTrue(old_pid, 'old core did not start')
            worker = subprocess.run([sys.executable, '-I', str(root / 'versions' / '1.0.0' / 'haivequeen' / 'cli.py'),
                                     'self-update', '--worker', '--version', '2.0.0'],
                                    env=env, capture_output=True, text=True, timeout=90)
            self.assertEqual(worker.returncode, 0, worker.stdout + worker.stderr)
            self.assertEqual(json.loads(worker.stdout)['result'], 'ok')
            self.assertEqual((root / 'active-version').read_text().strip(), '2.0.0')
            new_pid = loop.loop_running()
            self.assertTrue(new_pid)
            self.assertNotEqual(new_pid, old_pid)
            receipt = json.loads(runtime.kv_get(self.conn, scheduler.GLOBAL, 'loop-completed-round'))
            self.assertEqual(receipt['pid'], new_pid)
            self.assertEqual(receipt['identity'], platform.process_identity(new_pid))
            self.assertEqual(Path(receipt['code_path']), root / 'versions' / '2.0.0')
            self.assertTrue(all(job.poll() is None for job in jobs))
        finally:
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

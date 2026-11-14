import os
from pathlib import Path
import shutil
import subprocess
import sys
import time
from unittest.mock import patch

from bzztest import REL, RoundCase
from haivequeen import db, hives, inbox, orchestrator, runtime, version_layout


class LiveCoreHandoverTest(RoundCase):
    def test_real_core_handover_adopts_build(self):
        self._handover('Build')

    def test_real_core_handover_adopts_deploy(self):
        self._handover('Deploy')

    def _handover(self, step):
        root = self.base / 'install'
        source = Path(__file__).resolve().parent.parent
        for version in ('old', 'new'):
            dest = root / 'versions' / version
            shutil.copytree(source / 'haivequeen', dest / 'haivequeen',
                            ignore=shutil.ignore_patterns('__pycache__', '*.pyc'))
            # The CLI shim supplies a controlled round, while the real run_loop,
            # PID lock, active pointer and fixed launcher perform the handover.
            (dest / 'haivequeen' / 'cli.py').write_text('''
import os, sys, time
from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from unittest.mock import patch
from haivequeen import config, db, loop, orchestrator, proctree, version_leases
base = Path(os.environ['TEST_BASE'])
version = Path(__file__).resolve().parents[1].name
conn = db.connect(config.db_path())
real_round = loop.run_round
def round_(conn, machine, envs, **kwargs):
    (base / (version + '-round')).write_text(str(os.getpid()))
    if version == 'old':
        while not (base / 'release-old').exists(): time.sleep(.02)
    else:
        while not (base / 'build-finished').exists(): time.sleep(.02)
        real_round(conn, machine, envs, **kwargs)
        (base / 'actual-successor-round').touch()
        rows = conn.execute("SELECT name, status FROM agents WHERE project='testproj' ORDER BY name").fetchall()
        (base / 'seen').write_text(repr([tuple(row) for row in rows]))
with patch.object(loop, 'run_round', side_effect=round_), patch.object(loop, 'daily_export'), \\
     patch.object(proctree, 'sweep'), patch.object(version_leases, 'collect', return_value=[]), \\
     patch.object(orchestrator, '_push', return_value=None), patch.object(orchestrator, '_close', return_value=False), \
     patch.object(loop, 'active_count', return_value=0):
    loop.run_loop(conn, {'providers': [], 'projects': [], 'roundMinutes': 0.01}, rounds=1)
conn.close()
(base / (version + '-finished')).touch()
''', encoding='utf-8')
        version_layout.install_launcher(root)
        version_layout.set_active(root, 'old')
        self.plan('IN_PROGRESS', branch='feature', worktree=str(self.wt))
        with (self.repo / '.haivequeen' / 'project.toml').open('a', encoding='utf-8') as settings:
            settings.write('%sCommand = "test-command"\n' % step.lower())
        hives.create(self.conn, 'testproj', REL, 'claude', '123456')
        result = self.base / 'build-result.json'
        started, finish = self.base / 'build-started', self.base / 'finish-build'
        worker = self.base / 'build.py'
        worker.write_text('from pathlib import Path\nimport sys,time\n'
                          'Path(sys.argv[1]).touch()\n'
                          'while not Path(sys.argv[2]).exists(): time.sleep(.02)\n'
                          'print("built")\n', encoding='utf-8')
        command = '"%s" "%s" "%s" "%s"' % (sys.executable, worker, started, finish)
        build_job = orchestrator.Job('handover-build')
        with patch.object(orchestrator, '__file__', str(root / 'versions' / 'old' / 'haivequeen' / 'orchestrator.py')):
            orchestrator._start_detached_work(build_job, {}, step, command, self.wt, None, result)
        state = {'phase': 'pipeline', 'step': step, 'placed': True, 'ran': [],
                 'steps': {step: 'running'}, 'job': {'step': step, 'token': 'handover-build',
                 'file': str(result), 'pid': build_job.pid, 'head': 'old-revision'}}
        orchestrator.save(self.conn, 'testproj', REL, state)
        self.conn.execute("UPDATE hives SET state='integrating' WHERE project=? AND plan_path=?", ('testproj', REL))
        question = db.add_question(self.conn, 'testproj', REL, 'handover-question', 'Continue?', '')
        owner_check = db.add_question(self.conn, 'testproj', REL, 'owner-check', 'Please check.', '')
        env = dict(os.environ, TEST_BASE=str(self.base))
        jobs = [subprocess.Popen([sys.executable, '-c', 'import time; time.sleep(60)'], env=env) for _ in range(2)]
        core = None
        try:
            for name, phase, job in zip(('Queen', 'Agent'), ('queen', 'bee'), jobs):
                db.add_agent(self.conn, 'testproj', name=name, phase=phase, status='running', pid=job.pid)
            core = subprocess.Popen([sys.executable, '-I', str(root / 'launcher.py'), 'start'], env=env)
            def wait(path):
                end = time.monotonic() + 20
                while not path.exists() and time.monotonic() < end:
                    time.sleep(.03)
                self.assertTrue(path.exists(), 'timed out waiting for ' + str(path))
            wait(self.base / 'old-round')
            wait(started)
            # An owner decision arrives while the old round is still running.
            # The successor's real scheduling round must consume it from the
            # shared inbox after the lock changes hands.
            folder = inbox.inbox_dir(self.repo)
            folder.mkdir(parents=True, exist_ok=True)
            (folder / 'handover.toml').write_text(
                '[[decision]]\nsource="owner"\nitem="handover-test"\ntext="continue"\n'
                '[[answer]]\nquestion_id=%d\ntext="yes"\nby="owner"\n' % question,
                encoding='utf-8')
            version_layout.set_active(root, 'new')
            contender = subprocess.run([sys.executable, '-I', str(root / 'launcher.py'), 'start'],
                                       env=env, capture_output=True, text=True, timeout=20)
            self.assertEqual(contender.returncode, 0)
            self.assertIn('Another orchestrator loop runs', contender.stdout)
            self.assertFalse((self.base / 'new-round').exists())
            (self.base / 'new-finished').unlink(missing_ok=True)
            self.assertTrue(all(job.poll() is None for job in jobs))
            self.assertIsNone(build_job.process.poll())
            (self.base / 'release-old').touch()
            core.wait(timeout=20)
            self.assertEqual(core.returncode, 0)
            wait(self.base / 'new-round')
            self.assertIsNone(build_job.process.poll())
            finish.touch()
            build_job.process.wait(timeout=20)
            (self.base / 'build-finished').touch()
            wait(self.base / 'seen')
            wait(self.base / 'actual-successor-round')
            wait(self.base / 'new-finished')
            # The detached launcher closes its inherited log after its child exits.
            end = time.monotonic() + 10
            log = Path(env['BZZ_DATA_DIR']) / 'runs' / 'beekeeper.log'
            while log.exists() and time.monotonic() < end:
                try:
                    with log.open('a'):
                        break
                except PermissionError:
                    time.sleep(.05)
            time.sleep(.3)
            self.assertIn("('Queen', 'running')", (self.base / 'seen').read_text())
            self.assertIn("('Agent', 'running')", (self.base / 'seen').read_text())
            decisions = runtime.list_decisions(self.conn, 'testproj', item='handover-test', active_only=True)
            self.assertEqual([decision['text'] for decision in decisions], ['continue'])
            answered = self.conn.execute('SELECT status, answer FROM questions WHERE id=?', (question,)).fetchone()
            self.assertEqual(tuple(answered), ('answered', 'yes'))
            check = self.conn.execute('SELECT status FROM questions WHERE id=?', (owner_check,)).fetchone()
            self.assertEqual(check['status'], 'open')
            self.assertTrue(all(job.poll() is None for job in jobs))
            adopted = orchestrator.load(self.conn, 'testproj', REL)
            self.assertEqual(adopted['steps'][step], 'done')
            self.assertEqual(adopted['step'], 'Push' if step == 'Build' else 'Close')
            if step == 'Deploy':
                self.assertEqual(adopted['deployed'], 'old-revision')
            self.assertNotIn('job', adopted)
        finally:
            finish.touch()
            (self.base / 'build-finished').touch()
            if build_job.process.poll() is None:
                build_job.process.wait(timeout=20)
            (self.base / 'release-old').touch()
            if core is not None and core.poll() is None:
                core.terminate()
                core.wait(timeout=10)
            for job in jobs:
                job.terminate()
                job.wait(timeout=10)

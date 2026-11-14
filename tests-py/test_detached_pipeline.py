import json
import os
from pathlib import Path
import shutil
import sys
import tempfile
import time
from types import SimpleNamespace
import unittest
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from haivequeen import orchestrator, version_layout, version_leases


class DetachedPipelineTests(unittest.TestCase):
    def test_running_build_and_deploy_survive_version_activation(self):
        for step in ('Build', 'Deploy'):
            with self.subTest(step=step), tempfile.TemporaryDirectory() as folder:
                base = Path(folder)
                root = base / 'install'
                old = root / 'versions' / 'old'
                shutil.copytree(Path(orchestrator.__file__).parent, old / 'haivequeen',
                                ignore=shutil.ignore_patterns('__pycache__'))
                (root / 'versions' / 'new' / 'haivequeen').mkdir(parents=True)
                (root / 'versions' / 'new' / 'haivequeen' / 'cli.py').touch()
                (root / 'active-version').write_text('old', encoding='utf-8')
                started, finish = base / 'started', base / 'finish'
                script = base / 'work.py'
                script.write_text("from pathlib import Path\nimport sys,time\n"
                                  "Path(sys.argv[1]).touch()\n"
                                  "while not Path(sys.argv[2]).exists(): time.sleep(.05)\n"
                                  "print('finished')\n", encoding='utf-8')
                command = '"%s" "%s" "%s" "%s"' % (sys.executable, script, started, finish)
                result = base / 'result.json'
                job = orchestrator.Job('token')
                try:
                    with patch.object(orchestrator, '__file__', str(old / 'haivequeen' / 'orchestrator.py')), \
                         patch.dict(os.environ, {'BZZ_DATA_DIR': str(base / 'data')}):
                        orchestrator._start_detached_work(job, {}, step, command, base, None, result)
                    deadline = time.monotonic() + 30
                    while not started.exists() and time.monotonic() < deadline:
                        time.sleep(.05)
                    self.assertTrue(started.exists())
                    version_layout.set_active(root, 'new')
                    with patch.object(version_leases.process_scan, 'scan', return_value=False):
                        self.assertEqual(version_leases.collect(root), [])
                    self.assertIsNone(job.process.poll())
                    finish.touch()
                    job.process.wait(timeout=30)
                    state = {'step': step, 'steps': {step: 'running'}, 'ran': [],
                             'job': {'step': step, 'token': 'token', 'file': str(result),
                                     'pid': job.pid, 'head': 'old-revision'}}
                    env = SimpleNamespace(name='project')
                    # A successor has no in-memory job, and must advance the
                    # persisted pipeline from the detached worker's result.
                    with patch.dict(orchestrator.JOBS, {}, clear=True), \
                         patch.object(orchestrator, 'commands', return_value={step: command}), \
                         patch.object(orchestrator, 'chain', return_value=['Build', 'Deploy', 'Close']), \
                         patch.object(orchestrator, 'save_step_log'), patch.object(orchestrator, 'save'), \
                         patch.object(orchestrator, '_log'), patch.object(orchestrator, '_save_last'):
                        self.assertEqual(orchestrator._command(None, env, 'plan', state, base, step, 4096), 'next')
                    self.assertEqual(state['steps'][step], 'done')
                    self.assertEqual(state['step'], 'Deploy' if step == 'Build' else 'Close')
                    self.assertNotIn('job', state)
                    if step == 'Deploy':
                        self.assertEqual(state['deployed'], 'old-revision')
                finally:
                    finish.touch()
                    if job.process and job.process.poll() is None:
                        job.process.wait(timeout=30)

    def test_gated_worker_waits_for_durable_registration(self):
        import time
        with tempfile.TemporaryDirectory() as folder:
            folder = Path(folder)
            result = folder / "result.json"
            output = folder / "done.txt"
            program = "from pathlib import Path; Path(%r).write_text('finished')" % str(output)
            command = '"%s" -c "%s"' % (sys.executable, program)
            job = orchestrator.Job("gate-token")
            orchestrator._start_detached_work(job, {}, "Build", command, folder, None, result, gated=True)
            try:
                time.sleep(0.5)
                self.assertFalse(output.exists())
                self.assertIsNone(job.process.poll())
                Path(str(result) + ".go").touch()
                job.process.wait(timeout=30)
                self.assertEqual(output.read_text(), "finished")
            finally:
                if job.process.poll() is None:
                    job.process.terminate()
                    job.process.wait(timeout=10)

    def test_result_can_be_harvested_again_after_core_crash(self):
        with tempfile.TemporaryDirectory() as folder:
            result = Path(folder) / "result.json"
            orchestrator._write_result(result, "token", 0, "done")
            state = {"job": {"step": "Build", "token": "token", "file": str(result)}}
            with patch.object(orchestrator, "save_step_log"):
                for _ in range(2):
                    self.assertEqual(orchestrator._harvest(None, SimpleNamespace(name="project"), "plan", state), (0, "done"))

    def test_deploy_records_the_revision_that_was_started(self):
        state = {"job": {"step": "Deploy", "head": "old"}, "steps": {}}
        env = SimpleNamespace(name="project")
        with patch.object(orchestrator, "commands", return_value={"Deploy": "deploy"}), \
                patch.object(orchestrator, "_harvest", return_value=(0, "")), \
                patch.object(orchestrator, "_save_last") as save_last, \
                patch.object(orchestrator, "_done", return_value="next"):
            self.assertEqual(orchestrator._command(None, env, "plan", state, ".", "Deploy", 4096), "next")
        self.assertEqual(state["deployed"], "old")
        self.assertEqual(save_last.call_args.kwargs["deployed"], "old")

    def test_build_and_deploy_survive_and_are_harvested(self):
        for step in ("Build", "Deploy"):
            with self.subTest(step=step), tempfile.TemporaryDirectory() as folder:
                folder = Path(folder)
                result = folder / "result.json"
                output = folder / "done.txt"
                program = "from pathlib import Path; Path(%r).write_text('finished')" % str(output)
                command = '"%s" -c "%s"' % (sys.executable, program)
                job = orchestrator.Job("token")
                orchestrator._start_detached_work(job, {}, step, command, folder, None, result)
                pid = job.pid
                self.assertTrue(pid)
                job.process.wait(timeout=30)
                self.assertEqual(output.read_text(), "finished")
                self.assertEqual(json.loads(result.read_text())["code"], 0)
                orchestrator.JOBS.clear()
                state = {"job": {"step": step, "token": "token", "file": str(result), "pid": pid}}
                with patch.object(orchestrator, "save_step_log"):
                    self.assertEqual(orchestrator._harvest(None, SimpleNamespace(name="project"), "plan", state)[0], 0)


if __name__ == "__main__":
    unittest.main()

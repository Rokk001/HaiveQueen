import json
from pathlib import Path
import sys
import tempfile
from types import SimpleNamespace
import unittest
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from haivequeen import orchestrator


class DetachedPipelineTests(unittest.TestCase):
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

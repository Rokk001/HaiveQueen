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

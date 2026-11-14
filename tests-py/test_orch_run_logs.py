"""L-1/L-2: durable, complete per-action history and fix-agent handoff."""
import json
import sys
import threading
import time
from pathlib import Path
from unittest import mock

from bzztest import REL, RoundCase
from test_o1_orchestrator import OrchCase
from haivequeen import action_logs, integration, orchestrator


def records(path):
    return [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines()]


def output(path):
    return "".join(r["text"] for r in records(path) if r["event"] == "output")


class RunLogs(OrchCase):
    def setUp(self):
        super().setUp()
        self.script = self.base / "command.py"
        self.script.write_text(
            "import pathlib, sys\n"
            "for i in range(550): print('error-%04d ' % i + 'x' * 200, flush=True)\n"
            "sys.exit(0 if pathlib.Path('fixed.txt').exists() else 1)\n", encoding="utf-8")
        self.check = '"%s" "%s"' % (sys.executable, self.script)
        self.project_toml()

    def logs(self):
        return sorted((self.base / "data" / "orchestrator" / "logs").rglob("*.jsonl"))

    def test_l1_pipeline_history_complete_and_running(self):
        self.build = self.deploy = self.check
        self.project_toml()
        self.ready_plan()
        self.round()
        first = self.logs()
        self.assertEqual(len(first), 2, "Merge and red Check must each have a durable run log")
        snapshots = {p: p.read_bytes() for p in first}
        check = next(p for p in first if records(p)[0]["step"] == "Check")
        self.assertIn("error-0000 " + "x" * 200, output(check))
        self.assertIn("error-0549 " + "x" * 200, output(check))
        self.assertEqual(len(output(check).splitlines()), 550)
        self.assertEqual(records(check)[-1]["result"], "failed")
        self.commit(self.pwt, "fixed.txt")
        self.end_fixer(self.fixers()[0])
        merge = orchestrator._merge_git
        def observed(*args):
            running = [p for p in self.logs() if records(p)[-1]["event"] == "start"]
            self.assertEqual(len(running), 1)
            start = records(running[0])[0]
            self.assertEqual(start["result"], "running")
            self.assertTrue(start["started_at"])
            self.assertEqual(start["plan"], REL)
            return merge(*args)
        with mock.patch.object(orchestrator, "_merge_git", side_effect=observed):
            self.round()
        logs = self.logs()
        self.assertEqual(len(logs), 8)
        self.assertEqual(sorted(records(p)[0]["step"] for p in logs),
                         sorted(["Merge", "Check", "Merge", "Check", "Build", "Push", "Deploy", "Close"]))
        for p in logs:
            data = records(p)
            self.assertTrue(data[0]["started_at"])
            self.assertIn(data[0]["started_at"].replace(":", "-"), p.name)
            self.assertEqual(data[-1]["event"], "end")
            self.assertTrue(data[-1]["ended_at"])
            self.assertIn(data[-1]["result"], ("succeeded", "failed"))
            if data[0]["step"] in ("Check", "Build", "Deploy"):
                self.assertEqual(len(output(p).splitlines()), 550)
        for p, before in snapshots.items():
            self.assertEqual(p.read_bytes(), before)

    def test_l2_fix_prompt_points_to_complete_red_run(self):
        self.ready_plan()
        self.round()
        fix = self.state()["fix"]
        self.assertTrue(fix.get("log_file"), "fix agent must receive the red run's log file")
        path = Path(fix["log_file"])
        text = output(path)
        self.assertEqual(len(text.splitlines()), 550)
        self.assertIn("error-0000", text)
        self.assertIn("error-0549", text)
        prompt = (self.env().run_dir(self.fixers()[0]["name"]) / "prompt.md").read_text(encoding="utf-8")
        self.assertIn(str(path), prompt)
        self.assertIn("Read the complete", prompt)
        self.assertIn("all errors", prompt)
        self.assertIn("one run", prompt)


class OutputLogs(RoundCase):
    def test_stall_and_timeout_keep_every_line(self):
        script = self.base / "output.py"
        script.write_text("import time\nfor i in range(550): print('line-%04d ' % i + 'x'*200, flush=True)\ntime.sleep(30)\n", encoding="utf-8")
        command = '"%s" "%s"' % (sys.executable, script)
        for expected, stall, maximum in ((124, 60, 2), (125, 2, 60)):
            with self.subTest(exit=expected):
                path = action_logs.start("testproj", REL, "Check")
                with mock.patch.object(integration.proctree, "tree_cpu_seconds", return_value=0):
                    code, text = integration._watch_compile(command, str(self.base), stall, maximum, .02, log_file=path)
                self.assertEqual(code, expected)
                self.assertIn("line-0000 " + "x"*200, text)
                self.assertIn("line-0549 " + "x"*200, text)
                self.assertEqual(text, output(Path(path)))
                self.assertEqual(sum(line.startswith("line-") for line in text.splitlines()), 550)

    def test_output_is_visible_before_command_finishes(self):
        marker = self.base / "continue"
        script = self.base / "output.py"
        script.write_text("import pathlib, time\nprint('live output', flush=True)\n"
                          "while not pathlib.Path('continue').exists(): time.sleep(.02)\n", encoding="utf-8")
        path = Path(action_logs.start("testproj", REL, "Build"))
        result = []
        def run():
            result.append(integration._watch_compile('"%s" "%s"' % (sys.executable, script), str(self.base), 10, 10, .02, log_file=path))
        worker = threading.Thread(target=run)
        worker.start()
        try:
            deadline = time.monotonic() + 5
            while "live output" not in output(path) and time.monotonic() < deadline:
                time.sleep(.02)
            self.assertTrue(worker.is_alive())
            self.assertIn("live output", output(path))
            self.assertEqual(records(path)[0]["result"], "running")
            self.assertFalse(any(r["event"] == "end" for r in records(path)))
        finally:
            marker.touch()
            worker.join(15)
        self.assertEqual(result[0][0], 0)

    def test_plan_names_cannot_collide_and_completed_log_is_immutable(self):
        a = Path(action_logs.start("testproj", "a/b.md", "Merge"))
        b = Path(action_logs.start("testproj", "a_b.md", "Merge"))
        self.assertNotEqual(a.parent, b.parent)
        action_logs.append(a, "first\n")
        action_logs.finish(a, 1)
        before = a.read_bytes()
        action_logs.finish(a, 0)
        self.assertEqual(a.read_bytes(), before)

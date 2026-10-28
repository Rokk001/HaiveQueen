"""End-to-end tests of haivequeen/cli.py run as a subprocess against a temp runtime."""
import json
import os
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
from haivequeen import db  # noqa: E402


class CliTest(unittest.TestCase):
    """Runs haivequeen/cli.py with BZZ_* folders pointing into a temp folder."""

    def setUp(self):
        """Create temp config and data folders."""
        self.tmp = tempfile.TemporaryDirectory()
        self.env = dict(os.environ)
        self.env["BZZ_CONFIG_DIR"] = str(Path(self.tmp.name) / "cfg")
        self.env["BZZ_DATA_DIR"] = str(Path(self.tmp.name) / "data")

    def tearDown(self):
        """Remove the temp folder."""
        self.tmp.cleanup()

    def run_orch(self, *args):
        """Run haivequeen/cli.py; return (exit code, stdout)."""
        r = subprocess.run([sys.executable, "-I", str(ROOT / "haivequeen" / "cli.py"), *args], env=self.env,
                           capture_output=True, text=True, encoding="utf-8")
        return r.returncode, r.stdout

    def test_ask_answer_questions(self):
        """ask -> questions --open -> answer -> no open questions."""
        code, o = self.run_orch("ask", "--project", "p", "--plan", "x.md", "--item", "I1",
                                "--question", "Which?", "--recommendation", "A")
        self.assertEqual(code, 0)
        qid = json.loads(o)["id"]
        self.assertEqual(json.loads(self.run_orch("questions", "--open")[1]), [], "a new question waits for the triage first")
        self.assertEqual(self.run_orch("triage", "release", "--project", "p", "--question-id", str(qid))[0], 0)
        self.assertEqual(len(json.loads(self.run_orch("questions", "--open")[1])), 1)
        code, o = self.run_orch("answer", str(qid), "A", "--by", "owner")
        self.assertEqual(code, 0)
        self.assertEqual(json.loads(self.run_orch("questions", "--open")[1]), [])
        code, o = self.run_orch("answer", str(qid), "B", "--by", "owner")
        self.assertEqual(code, 1)
        self.assertFalse(json.loads(o)["ok"])

    def test_ready_queue_note(self):
        """ready adds a queue row; note succeeds."""
        code, _ = self.run_orch("ready", "--project", "p", "--branch", "b", "--tip", "abc",
                                "--plan", "x.md", "--note", "n")
        self.assertEqual(code, 0)
        q = json.loads(self.run_orch("queue")[1])
        self.assertEqual((q[0]["branch"], q[0]["status"]), ("b", "ready"))
        self.assertEqual(self.run_orch("note", "--project", "p", "--text", "hello")[0], 0)

    def test_done(self):
        """done reads the result file and marks the agent done."""
        conn = db.connect(Path(self.env["BZZ_DATA_DIR"]) / "haivequeen.db")
        aid = db.add_agent(conn, "p", name="a", status="running")
        conn.close()
        res = Path(self.tmp.name) / "res.txt"
        res.write_text("report\nfinished", encoding="utf-8")
        code, o = self.run_orch("done", "--agent", str(aid), "--result", str(res))
        self.assertEqual(code, 0)
        self.assertEqual(json.loads(o)["project"], "p")
        self.assertEqual(self.run_orch("done", "--agent", "99", "--result", str(res))[0], 1)

    def test_status_text_and_json(self):
        """status prints 'X / Y integrated (P%)' and a table; --json gives the same data."""
        conn = db.connect(Path(self.env["BZZ_DATA_DIR"]) / "haivequeen.db")
        conn.execute("INSERT INTO packages (project, plan_path, id, title) VALUES ('p','x.md','P1','First')")
        conn.close()
        code, o = self.run_orch("status")
        self.assertEqual(code, 0)
        self.assertIn("0 / 1 integrated (0%)", o)
        self.assertIn("Task     | Agent | Status", o)
        data = json.loads(self.run_orch("status", "--json")[1])
        self.assertEqual((data[0]["project"], data[0]["total"]), ("p", 1))

    def test_start_stop(self):
        """stop writes the STOP flag; start (foreground, one round, no project) removes it and ends."""
        code, o = self.run_orch("stop")
        self.assertEqual(code, 0)
        self.assertTrue((Path(self.tmp.name) / "data" / "STOP").is_file())
        code, o = self.run_orch("start", "--foreground", "--rounds", "1")
        self.assertEqual(code, 0)
        self.assertFalse((Path(self.tmp.name) / "data" / "STOP").exists())

    def test_real_folders_untouched(self):
        """The runtime was created only inside the temp folder."""
        self.run_orch("queue")
        self.assertTrue((Path(self.tmp.name) / "data" / "haivequeen.db").is_file())


if __name__ == "__main__":
    unittest.main()

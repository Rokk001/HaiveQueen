"""Tests of the runtime commands of haivequeen/cli.py against a temporary database (run as subprocesses, like the scripts do)."""
import json
import os
import subprocess
import sys
import tempfile
import unittest
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
from haivequeen import db  # noqa: E402


class RuntimeTest(unittest.TestCase):
    """Runs haivequeen/cli.py with BZZ_* folders pointing into a temp folder."""

    def setUp(self):
        """Create temp config, data and log folders."""
        self.tmp = tempfile.TemporaryDirectory()
        base = Path(self.tmp.name)
        self.env = dict(os.environ)
        self.env["BZZ_CONFIG_DIR"] = str(base / "cfg")
        self.env["BZZ_DATA_DIR"] = str(base / "data")
        self.env["BZZ_LOG_DIR"] = str(base / "logs")
        self.base = base

    def tearDown(self):
        """Remove the temp folder."""
        self.tmp.cleanup()

    def orch(self, *args, stdin=None):
        """Run haivequeen/cli.py; return (exit code, parsed JSON)."""
        r = subprocess.run([sys.executable, "-I", str(ROOT / "haivequeen" / "cli.py"), *args], env=self.env, input=stdin,
                           capture_output=True, text=True, encoding="utf-8")
        return r.returncode, json.loads(r.stdout)

    def ok(self, *args, **kw):
        """Run a command that must succeed and return its JSON."""
        code, res = self.orch(*args, **kw)
        self.assertEqual(code, 0, res)
        return res

    def test_agents(self):
        """upsert creates, merges free fields, update-fields needs an existing agent, archive hides old rows."""
        a = self.ok("agents", "upsert", "--project", "p", "--name", "Max",
                    "--fields", '{"status":"running","plan":"a.md","startedAt":"2020-01-01T00:00:00","buildJobs":3}')
        self.assertEqual((a["status"], a["plan_path"], a["buildJobs"]), ("running", "a.md", 3))
        self.ok("agents", "upsert", "--project", "p", "--name", "Max",
                "--fields", '{"endedAt":"2020-01-01T01:00:00","status":"done","note":"x"}')
        got = self.ok("agents", "get", "--project", "p", "--name", "Max")
        self.assertEqual((got["status"], got["buildJobs"], got["note"]), ("done", 3, "x"))
        self.assertEqual(len(self.ok("agents", "list", "--project", "p")), 1)
        code, res = self.orch("agents", "update-fields", "--project", "p", "--name", "Nobody", "--fields", "{}")
        self.assertEqual(code, 1)
        self.assertFalse(res["ok"])
        self.ok("agents", "upsert", "--project", "p", "--name", "Fritz", "--fields", '{"status":"running"}')
        self.assertEqual(self.ok("agents", "archive", "--project", "p")["moved"], 1)
        self.assertEqual([r["name"] for r in self.ok("agents", "list", "--project", "p")], ["Fritz"])
        self.assertEqual(len(self.ok("agents", "list", "--project", "p", "--scope", "all")), 2)
        self.assertEqual(self.ok("agents", "list", "--project", "p", "--scope", "archive")[0]["archived"], True)
        self.assertEqual(self.ok("agents", "archive", "--project", "p", "--match", "^Fri")["moved"], 1)

    def test_event_mirror(self):
        """event append writes the table row and the mirror lines; events list/tail read them."""
        self.ok("event", "append", "--project", "p", "--text", "round started")
        self.ok("event", "append", "--project", "p", "--text", "brain  failed\n x", "--mirror", "events", "--error")
        self.ok("event", "append", "--project", "p", "--text", "both", "--mirror", "both")
        self.ok("event", "append", "--project", "p", "--text", "hidden", "--mirror", "none")
        paths = self.ok("events", "paths", "--project", "p")
        rounds = Path(paths["rounds"]).read_text(encoding="utf-8").splitlines()
        self.assertEqual(len(rounds), 2)
        self.assertTrue(rounds[0].endswith(" round started"))
        evlines = Path(paths["events"]).read_text(encoding="utf-8").splitlines()
        self.assertTrue(evlines[0].endswith("|E|brain failed x"))
        self.assertTrue(evlines[1].endswith("|O|both"))
        self.assertEqual(len(self.ok("events", "list", "--project", "p")), 4)
        self.assertEqual([e["text"] for e in self.ok("events", "tail", "--project", "p", "-n", "2")], ["both", "hidden"])
        self.assertEqual(len(self.ok("events", "list", "--project", "p", "--since-id", "3")), 1)

    def test_kv_handled_nofollow(self):
        """kv, handled (with value) and nofollow behave as the replaced files did."""
        self.assertFalse(self.ok("kv", "get", "--project", "p", "--key", "state")["found"])
        self.ok("kv", "set", "--project", "p", "--key", "state", "--value", "-", stdin='{"a":"ä"}')
        self.assertEqual(self.ok("kv", "get", "--project", "p", "--key", "state")["value"], '{"a":"ä"}')
        self.assertEqual(self.ok("kv", "get", "--project", "q", "--key", "state")["found"], False)
        self.assertEqual(len(self.ok("kv", "list", "--project", "p", "--prefix", "st")), 1)
        self.assertEqual(self.ok("kv", "delete", "--project", "p", "--key", "state")["deleted"], 1)
        self.assertFalse(self.ok("handled", "check", "--project", "p", "--key", "k")["handled"])
        self.ok("handled", "add", "--project", "p", "--key", "try:k", "--value", "1")
        self.ok("handled", "add", "--project", "p", "--key", "try:k", "--value", "2")
        r = self.ok("handled", "check", "--project", "p", "--key", "try:k")
        self.assertEqual((r["handled"], r["value"]), (True, "2"))
        self.assertEqual(len(self.ok("handled", "list", "--project", "p")), 1)
        self.ok("handled", "remove", "--project", "p", "--key", "try:k")
        self.assertFalse(self.ok("handled", "check", "--project", "p", "--key", "try:k")["handled"])
        self.ok("nofollow", "add", "--project", "p", "--key", "A-Plan.md|A6")
        self.ok("nofollow", "add", "--project", "p", "--key", "a-plan.md|a6")
        self.assertEqual(self.ok("nofollow", "list", "--project", "p"), ["a-plan.md|a6"])
        self.ok("nofollow", "remove", "--project", "p", "--key", "A-PLAN.md|a6")
        self.assertEqual(self.ok("nofollow", "list", "--project", "p"), [])

    def test_build_usage(self):
        """build keeps the maximum per host and project; usage stores the extra brain fields."""
        self.assertIsNone(self.ok("build", "max-per-job", "--project", "p", "--host", "h")["per_job_mb"])
        for v in ("600", "900", "400"):
            self.ok("build", "add", "--project", "p", "--host", "h", "--jobs", "2", "--free-start-mb", "8000",
                    "--free-min-mb", "6000", "--per-job-mb", v, "--duration-s", "60")
        self.assertEqual(self.ok("build", "max-per-job", "--project", "p", "--host", "h")["per_job_mb"], 900)
        self.assertIsNone(self.ok("build", "max-per-job", "--project", "p", "--host", "other")["per_job_mb"])
        self.ok("usage", "add", "--kind", "brain", "--project", "p", "--model", "m", "--tokens-in", "10",
                "--tokens-out", "5", "--trigger", "event", "--context", "1234", "--duration-s", "3.5",
                "--actions", "a;b", "--error", "0")
        self.ok("usage", "add", "--kind", "agent", "--provider", "x", "--tokens-in", "1")
        rows = self.ok("usage", "list", "--kind", "brain")
        self.assertEqual(len(rows), 1)
        self.assertEqual((rows[0]["trigger"], rows[0]["context"], rows[0]["duration_s"], rows[0]["actions"]),
                         ("event", 1234, 3.5, "a;b"))
        self.assertEqual(len(self.ok("usage", "list")), 2)
        self.assertEqual(len(self.ok("usage", "list", "--limit", "1")), 1)

    def test_plans_packages(self):
        """plans sync reads front matter plans, honours locks and done phases, set-status edits the file."""
        ws = self.base / "ws"
        (ws / "docs" / "plans").mkdir(parents=True)
        plan = ws / "docs" / "plans" / "one.md"
        plan.write_text('+++\nstatus = "READY"\n[[packages]]\nid = "P1"\ntitle = "First"\n'
                        '[[packages]]\nid = "P2"\ntitle = "Second"\ndepends_on = ["P1"]\n+++\n# One\n', encoding="utf-8")
        res = self.ok("plans", "sync", "--project", "p", "--workspace", str(ws))
        self.assertEqual([s["id"] for s in res["startable"]], ["P1"])
        self.assertEqual(res["plans"][0]["status"], "READY")
        self.ok("packages", "set", "--project", "p", "--plan", "docs/plans/one.md", "--id", "P1", "--phase", "done")
        res = self.ok("plans", "sync", "--project", "p", "--workspace", str(ws))
        self.assertEqual([s["id"] for s in res["startable"]], ["P2"])
        pk = self.ok("packages", "set", "--project", "p", "--plan", "docs/plans/one.md", "--id", "P2",
                     "--locked", "1", "--reason", "needs input")
        self.assertEqual((pk["locked"], pk["lock_reason"], pk["depends_on"]), (True, "needs input", ["P1"]))
        self.assertEqual(self.ok("plans", "sync", "--project", "p", "--workspace", str(ws))["startable"], [])
        self.ok("plans", "set-status", "--project", "p", "--plan", "docs/plans/one.md", "--status", "in_progress",
                "--workspace", str(ws))
        self.assertIn('status = "IN_PROGRESS"', plan.read_text(encoding="utf-8"))
        self.assertEqual(self.ok("plans", "list", "--project", "p")[0]["status"], "IN_PROGRESS")
        self.assertEqual(len(self.ok("packages", "list", "--project", "p")), 2)
        code, _ = self.orch("plans", "sync", "--project", "nowhere")
        self.assertEqual(code, 1)

    def test_package_capability_kept(self):
        """A package with agent and capability keeps both in the table (the capability is not lost behind the agent)."""
        ws = self.base / "ws"
        (ws / "docs" / "plans").mkdir(parents=True)
        (ws / "docs" / "plans" / "cap.md").write_text(
            '+++\nstatus = "READY"\n[[packages]]\nid = "G1"\ntitle = "Art"\nagent = "claude"\ncapability = "images"\n'
            '[[packages]]\nid = "G2"\ntitle = "Plain"\n+++\n# Cap\n', encoding="utf-8")
        self.ok("plans", "sync", "--project", "p", "--workspace", str(ws))
        pk = {r["id"]: r for r in self.ok("packages", "list", "--project", "p")}
        self.assertEqual((pk["G1"]["agent_type"], pk["G1"]["capability"]), ("claude", "images"))
        self.assertFalse(pk["G2"]["capability"])
        # a second sync keeps the value, a changed header replaces it
        (ws / "docs" / "plans" / "cap.md").write_text(
            '+++\nstatus = "READY"\n[[packages]]\nid = "G1"\ntitle = "Art"\nagent = "claude"\ncapability = "voice"\n+++\n# Cap\n',
            encoding="utf-8")
        self.ok("plans", "sync", "--project", "p", "--workspace", str(ws))
        self.assertEqual(self.ok("packages", "list", "--project", "p")[0]["capability"], "voice")

    def test_migrates_version_2_capability(self):
        """A version 2 database gets the packages.capability column when it is opened."""
        import sqlite3
        path = Path(self.env["BZZ_DATA_DIR"]) / "haivequeen.db"
        path.parent.mkdir(parents=True)
        conn = sqlite3.connect(path)
        conn.executescript("CREATE TABLE packages (project TEXT NOT NULL, plan_path TEXT NOT NULL, id TEXT NOT NULL, title TEXT, "
                           "class TEXT, agent_type TEXT, depends_on TEXT, phase TEXT, locked INTEGER NOT NULL DEFAULT 0, "
                           "lock_reason TEXT, PRIMARY KEY (project, plan_path, id));"
                           "INSERT INTO packages (project, plan_path, id) VALUES ('p', 'a.md', 'P1'); PRAGMA user_version = 2;")
        conn.commit()
        conn.close()
        rows = self.ok("packages", "list", "--project", "p")
        self.assertEqual(len(rows), 1)
        self.assertIn("capability", rows[0])

    def test_queue_questions_decisions(self):
        """queue upsert changes rows in place, questions filter by item, decisions carry the item."""
        self.ok("queue", "upsert", "--project", "p", "--branch", "b", "--tip", "t1", "--plan", "x.md")
        row = self.ok("queue", "upsert", "--project", "p", "--branch", "b", "--status", "in_run", "--run", "r1")
        self.assertEqual((row["status"], row["tip"], row["run"]), ("in_run", "t1", "r1"))
        self.ok("queue", "upsert", "--project", "p", "--branch", "b", "--status", "integrated")
        self.ok("queue", "upsert", "--project", "p", "--branch", "b", "--tip", "t2")
        self.assertEqual([r["status"] for r in self.ok("queue", "list", "--project", "p")], ["integrated", "ready"])
        code, _ = self.orch("queue", "upsert", "--project", "p", "--branch", "b", "--status", "bogus")
        self.assertEqual(code, 1)
        qid = self.ok("ask", "--project", "p", "--plan", "x.md", "--item", "I1", "--question", "Q?")["id"]
        self.ok("ask", "--project", "p", "--plan", "x.md", "--item", "I2", "--question", "Q2?")
        self.assertEqual(len(self.ok("questions", "--project", "p", "--item", "I1")), 1)
        self.ok("answer", str(qid), "yes", "--by", "owner")
        self.assertEqual(len(self.ok("questions", "--open")), 1)
        self.assertEqual(len(self.ok("questions", "--status", "answered")), 1)
        self.assertEqual(self.ok("decisions", "list", "--project", "p", "--item", "I1")[0]["source"], "owner")
        self.ok("decisions", "add", "--project", "p", "--source", "auto", "--text", "d", "--item", "I3")
        self.assertEqual(len(self.ok("decisions", "list", "--project", "p")), 2)

    def test_migrates_version_1(self):
        """A database created by schema version 1 gets the new columns and tables."""
        import sqlite3
        path = Path(self.env["BZZ_DATA_DIR"]) / "haivequeen.db"
        path.parent.mkdir(parents=True)
        conn = sqlite3.connect(path)
        conn.executescript("CREATE TABLE agents (id INTEGER PRIMARY KEY AUTOINCREMENT, project TEXT NOT NULL, name TEXT, status TEXT);"
                           "CREATE TABLE providers_usage (ts TEXT, provider TEXT, model TEXT, tokens_in INTEGER, "
                           "tokens_out INTEGER, kind TEXT);"
                           "CREATE TABLE handled (project TEXT NOT NULL, key TEXT NOT NULL, ts TEXT, PRIMARY KEY (project, key));"
                           "CREATE TABLE decisions (id INTEGER PRIMARY KEY AUTOINCREMENT, project TEXT, ts TEXT, "
                           "source TEXT, text TEXT, plan_path TEXT);"
                           "INSERT INTO agents (project, name) VALUES ('p', 'Old'); PRAGMA user_version = 1;")
        conn.commit()
        conn.close()
        self.assertEqual(self.ok("agents", "get", "--project", "p", "--name", "Old")["archived"], False)
        self.ok("kv", "set", "--project", "p", "--key", "k", "--value", "v")
        self.ok("usage", "add", "--kind", "brain", "--trigger", "t")

    def test_concurrent_writers(self):
        """Many processes writing at once all succeed and lose nothing."""
        def work(i):
            """One process: an event, an agent update and a kv write."""
            self.ok("event", "append", "--project", "p", "--text", f"e{i}", "--mirror", "both")
            self.ok("agents", "upsert", "--project", "p", "--name", "Max", "--fields", json.dumps({f"f{i}": i}))
            self.ok("kv", "set", "--project", "p", "--key", f"k{i}", "--value", str(i))
        with ThreadPoolExecutor(max_workers=8) as pool:
            list(pool.map(work, range(24)))
        self.assertEqual(len(self.ok("events", "list", "--project", "p")), 24)
        agent = self.ok("agents", "get", "--project", "p", "--name", "Max")
        self.assertEqual(sorted(k for k in agent if k.startswith("f") and k[1:].isdigit()), sorted(f"f{i}" for i in range(24)))
        self.assertEqual(len(self.ok("kv", "list", "--project", "p", "--prefix", "k")), 24)
        paths = self.ok("events", "paths", "--project", "p")
        self.assertEqual(len(Path(paths["rounds"]).read_text(encoding="utf-8").splitlines()), 24)
        conn = db.connect(Path(self.env["BZZ_DATA_DIR"]) / "haivequeen.db")
        self.assertEqual(conn.execute("PRAGMA integrity_check").fetchone()[0], "ok")
        conn.close()


if __name__ == "__main__":
    unittest.main()

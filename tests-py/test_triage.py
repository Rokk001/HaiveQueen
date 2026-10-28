"""Tests of the triage commands against a temporary database with two invented locks."""
import json
import os
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
from haivequeen import config, db  # noqa: E402

PLAN = "docs/plans/x.md"


class TriageTest(unittest.TestCase):
    """Runs haivequeen/cli.py triage commands with BZZ_* folders pointing into a temp folder."""

    def setUp(self):
        """Create temp folders and two locked packages: T1 (one technical point) and G1 (a game point plus a technical one)."""
        self.tmp = tempfile.TemporaryDirectory()
        base = Path(self.tmp.name)
        self.env = dict(os.environ)
        self.env["BZZ_CONFIG_DIR"] = str(base / "cfg")
        self.env["BZZ_DATA_DIR"] = str(base / "data")
        self.env["BZZ_LOG_DIR"] = str(base / "logs")
        old = dict(os.environ)
        os.environ.update({k: self.env[k] for k in ("BZZ_CONFIG_DIR", "BZZ_DATA_DIR", "BZZ_LOG_DIR")})
        conn = db.connect(config.db_path())
        for pid, title, reason in (("T1", "Docs", "doc1: write the module docstring"),
                                   ("G1", "Rules", "dmg: how much damage does the blade do\nlayout: split the file")):
            conn.execute("INSERT INTO packages (project, plan_path, id, title, locked, lock_reason) VALUES (?,?,?,?,1,?)",
                         ("p", PLAN, pid, title, reason))
        conn.commit()
        conn.close()
        os.environ.clear()
        os.environ.update(old)

    def tearDown(self):
        """Remove the temp folder."""
        self.tmp.cleanup()

    def orch(self, *args):
        """Run haivequeen/cli.py; return (exit code, parsed JSON)."""
        r = subprocess.run([sys.executable, "-I", str(ROOT / "haivequeen" / "cli.py"), *args], env=self.env,
                           capture_output=True, text=True, encoding="utf-8")
        return r.returncode, json.loads(r.stdout)

    def ok(self, *args):
        """Run a command that must succeed and return its JSON."""
        code, res = self.orch(*args)
        self.assertEqual(code, 0, res)
        return res

    def pkg(self, pid):
        """Return the package row as listed by the CLI."""
        return [p for p in self.ok("packages", "list", "--project", "p") if p["id"] == pid][0]

    def decide(self, pid, item):
        """Run triage decide for one point."""
        return self.orch("triage", "decide", "--project", "p", "--plan", PLAN, "--package", pid, "--item", item,
                         "--text", "use a docstring", "--reason", "docs rule", "--source", "plan rule 3")

    def test_technical_lock_is_decided_and_unlocked(self):
        """decide writes an auto decision with reason and source and unlocks the package."""
        self.assertEqual({x["id"] for x in self.ok("triage", "list", "--project", "p")}, {"T1", "G1"})
        code, res = self.decide("T1", "doc1")
        self.assertEqual(code, 0, res)
        self.assertTrue(res["unlocked"])
        self.assertFalse(self.pkg("T1")["locked"])
        d = self.ok("decisions", "list", "--project", "p", "--item", "doc1")[0]
        self.assertEqual(d["source"], "auto")
        self.assertIn("docs rule", d["text"])
        self.assertIn("plan rule 3", d["text"])
        self.assertEqual({x["id"] for x in self.ok("triage", "list", "--project", "p")}, {"G1"})

    def test_game_lock_stays_locked_with_open_question(self):
        """ask files an open owner question, the package stays locked and leaves the triage list point by point."""
        res = self.ok("triage", "ask", "--project", "p", "--plan", PLAN, "--package", "G1", "--item", "dmg",
                      "--question", "How much damage?", "--recommendation", "5")
        self.assertEqual(res["answered_by"], "owner")
        qs = self.ok("questions", "--project", "p", "--open")
        self.assertEqual([(q["item"], q["status"]) for q in qs], [("dmg", "open")])
        self.assertTrue(self.pkg("G1")["locked"])
        self.assertEqual([p["item"] for p in [x for x in self.ok("triage", "list", "--project", "p") if x["id"] == "G1"][0]["points"]], ["layout"])
        code, _ = self.orch("triage", "ask", "--project", "p", "--plan", PLAN, "--package", "G1", "--item", "dmg",
                            "--question", "again")
        self.assertEqual(code, 1)

    def test_unlock_only_after_last_point(self):
        """A package with two points is unlocked by the decision of the last point only; an open question blocks it."""
        self.ok("triage", "ask", "--project", "p", "--plan", PLAN, "--package", "G1", "--item", "dmg",
                "--question", "How much damage?")
        code, res = self.decide("G1", "layout")
        self.assertEqual(code, 0, res)
        self.assertFalse(res["unlocked"])
        self.assertEqual(res["remaining"], ["dmg"])
        self.assertTrue(self.pkg("G1")["locked"])
        self.ok("answer", "1", "five", "--by", "owner")
        self.assertTrue(self.pkg("G1")["locked"])
        self.assertEqual(self.ok("questions", "--project", "p", "--open"), [])

    def test_two_points_decided_one_after_the_other(self):
        """The first decision keeps the lock, the second removes it."""
        _, first = self.decide("G1", "dmg")
        self.assertFalse(first["unlocked"])
        self.assertTrue(self.pkg("G1")["locked"])
        _, second = self.decide("G1", "layout")
        self.assertTrue(second["unlocked"])
        self.assertFalse(self.pkg("G1")["locked"])

    def test_running_triage_agent_hides_the_package(self):
        """A package with a running triage agent is not listed again."""
        self.ok("agents", "upsert", "--project", "p", "--name", "Tri",
                "--fields", json.dumps({"status": "running", "plan": PLAN, "package": "T1", "phase": "triage"}))
        self.assertEqual([x["id"] for x in self.ok("triage", "list", "--project", "p")], ["G1"])

    def test_prompt_rules(self):
        """The prompt names both kinds, the commands and the rule 'in doubt game/balance'."""
        res = self.ok("triage", "prompt", "--project", "p", "--plan", PLAN, "--package", "G1", "--python", "py")
        text = res["prompt"]
        self.assertIn("In doubt it is game/balance", text)
        self.assertIn("triage decide", text)
        self.assertIn("triage ask", text)
        self.assertIn("dmg: how much damage", text)
        self.assertEqual(len(res["points"]), 2)

    def test_decide_needs_reason_and_known_item(self):
        """An unknown item or an empty reason is rejected."""
        code, _ = self.orch("triage", "decide", "--project", "p", "--plan", PLAN, "--package", "T1", "--item", "nope",
                            "--text", "x", "--reason", "r", "--source", "s")
        self.assertEqual(code, 1)
        code, _ = self.orch("triage", "decide", "--project", "p", "--plan", PLAN, "--package", "T1", "--item", "doc1",
                            "--text", "x", "--reason", " ", "--source", "s")
        self.assertEqual(code, 1)


if __name__ == "__main__":
    unittest.main()

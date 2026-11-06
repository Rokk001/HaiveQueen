"""Exercise the installed workflow's CLI and inbox contract without an agent or live data."""
import json
import subprocess
import sys
from pathlib import Path

import tomlkit

from bzztest import REL, ROOT, RoundCase, config, db
from haivequeen import bzz_install, inbox


class ProfileWorkflowTests(RoundCase):
    def cli(self, *args):
        result = subprocess.run([sys.executable, "-I", str(ROOT / "haivequeen/cli.py"), *args],
                                capture_output=True, text=True, encoding="utf-8", timeout=30)
        self.assertEqual(result.returncode, 0, result.stderr + result.stdout)
        return json.loads(result.stdout)

    def test_all_apiaries_answers_roundtrip_through_inbox(self):
        second = self.add_project("second", extra='language = "German"\nownerTopics = ["retention"]\n')
        # Register through the real CLI so both the config list the prompt reads
        # and the database used by questions --open contain both Apiaries.
        for workspace in (self.repo, second):
            self.assertTrue(self.cli("register", str(workspace))["ok"])
        self.assertEqual(len(config.load_config()["projects"]), 2)
        technical = db.add_question(self.conn, "testproj", REL, "P1", "Which import fixes the error?", "")
        owner = db.add_question(self.conn, "second", REL, "P2", "How long should retention last?", "")
        questions = self.cli("questions", "--open")
        self.assertEqual({q["id"] for q in questions}, {technical, owner})
        self.assertEqual(config.load_project(second)["language"], "German")
        self.assertEqual(config.load_project(second)["ownerTopics"], ["retention"])
        home = self.base / "user-home"
        bzz_install.install(home)
        installed = (home / ".claude/commands/bzz.md").read_text(encoding="utf-8")
        for instruction in ("ownerTopics", "one at a time", "Question X of Y", "language", "Never invent an owner answer"):
            self.assertIn(instruction, installed)
        for project, workspace, qid, by in (("testproj", self.repo, technical, "coordinator"),
                                           ("second", second, owner, "owner")):
            answer = 'Answer with "quotes", a backslash \\, # hash,\nand Unicode: Grüsse.'
            folder = inbox.inbox_dir(workspace)
            folder.mkdir(parents=True)
            temporary = folder / "unique.toml.tmp"
            temporary.write_text(tomlkit.dumps({"answer": [{"question_id": qid, "text": answer, "by": by}]}), encoding="utf-8")
            self.assertEqual(inbox.process_inbox(self.env(project)), {"done": [], "failed": []})
            temporary.rename(folder / "unique.toml")
            self.assertEqual(inbox.process_inbox(self.env(project)), {"done": ["unique.toml"], "failed": []})
            self.assertEqual(inbox.process_inbox(self.env(project)), {"done": [], "failed": []})
            row = self.conn.execute("SELECT answer, answered_by FROM questions WHERE id=?", (qid,)).fetchone()
            self.assertEqual(tuple(row), (answer, by))
        self.assertEqual(self.cli("questions", "--open"), [])

    def test_init_example_through_real_cli(self):
        workspace = self.base / "new-project"
        self.assertTrue(self.cli("init", str(workspace))["ok"])
        path = workspace / "docs/plans/EXAMPLE-PLAN.md"
        text = path.read_text(encoding="utf-8")
        self.assertIn('status = "DRAFT"', text)
        self.assertIn('requirements = ["E-1", "E-2"]', text)
        self.assertIn('Feature branch: `example`', text)
        path.write_text("User plan", encoding="utf-8")
        self.cli("init", str(workspace))
        self.assertEqual(path.read_text(encoding="utf-8"), "User plan")

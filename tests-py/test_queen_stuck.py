"""A Queen that cannot go on asks one concrete question and waits; the Beekeeper never restarts a Queen at once after an unfinished end without a new commit."""
import json
import os
import subprocess
import unittest

from bzztest import REL, RoundCase
from haivequeen import bees, hives, runtime

OLD = "2020-01-01T00:00:00"


class QueenInstructionTests(unittest.TestCase):
    """The Queen instructions tell her what to do when she is stuck and how the completeness count works (HQ-HIVES A6, A8)."""

    def text(self):
        """The Queen instructions with the default limits."""
        return bees.queen_instructions("medium", 3, 3, 300000)

    def test_stuck_queen_asks_and_hands_off(self):
        """A Queen that cannot go on files a concrete question with bzz ask and ends with WAITING_FOR_OWNER, never silently unfinished."""
        text = self.text()
        self.assertIn("bzz ask", text)
        self.assertIn("contradict", text)
        self.assertIn("WAITING_FOR_OWNER", text)
        self.assertIn("never end silently", text)

    def test_owner_only_points_do_not_count_as_open(self):
        """Points only the owner can check, and points the owner set aside in a decision, are not counted; this wins over a project or Codex rule."""
        text = self.text()
        self.assertIn("only the owner can check", text)
        self.assertIn("owner has set aside", text)
        self.assertIn("wins over any project rule", text)


class UnfinishedEndTests(RoundCase):
    """After a Queen ended without a report of waiting and without new commit, the Hive waits for an answer and carries her end text as a question (A8)."""

    def git(self, *args, date=None):
        """Run git in the Queen worktree, with an optional fixed commit date."""
        env = dict(os.environ, GIT_AUTHOR_NAME="t", GIT_AUTHOR_EMAIL="t@example.com", GIT_COMMITTER_NAME="t", GIT_COMMITTER_EMAIL="t@example.com")
        if date:
            env["GIT_COMMITTER_DATE"] = date
            env["GIT_AUTHOR_DATE"] = date
        subprocess.run(["git", *args], cwd=self.wt, env=env, check=True, capture_output=True)

    def setUp(self):
        """Make the Queen worktree a git repository whose only commit is old."""
        super().setUp()
        self.git("init", "-q")
        (self.wt / "a.txt").write_text("a", encoding="utf-8")
        self.git("add", "a.txt")
        self.git("commit", "-q", "-m", "old", date=OLD)

    def finish(self, name, final="", status="done", project="testproj"):
        """Simulate a finished runner, including its process exit."""
        super().finish(name, final=final, status=status, project=project)
        runtime.upsert_agent(self.conn, project, name, {"pid": None})

    def queen(self):
        """Start the Queen of the dummy plan and return her agent row."""
        self.plan("READY", [{"id": "P1", "title": "First"}])
        self.round()
        return [a for a in runtime.list_agents(self.conn, "testproj", "all") if a["status"] in ("running", "starting")][0]

    def queens(self):
        """All Queen rows of the dummy plan."""
        return [a for a in runtime.list_agents(self.conn, "testproj", "all") if a["phase"] == "queen"]

    def test_end_without_commit_waits_for_an_answer(self):
        """No new commit and no waiting report: no new Queen, the Hive is in 'answer' with one open question that holds the end text."""
        first = self.queen()
        self.finish(first["name"], final="Projektregel verbietet die Freigabe bei offenen Teilanforderungen.")
        self.round()
        self.assertEqual(len(self.queens()), 1)
        self.assertEqual(hives.get(self.conn, "testproj", REL)["state"], "answer")
        questions = runtime.list_questions(self.conn, "testproj", status="open", plan=REL)
        self.assertEqual(len(questions), 1)
        self.assertIn("Projektregel verbietet die Freigabe", questions[0]["question"])
        self.assertEqual(json.loads(hives.get(self.conn, "testproj", REL)["questions_json"]), [questions[0]["id"]])

    def test_end_with_new_commit_restarts_at_once(self):
        """Real work (a new commit during the run) starts the next Queen at once."""
        first = self.queen()
        (self.wt / "b.txt").write_text("b", encoding="utf-8")
        self.git("add", "b.txt")
        self.git("commit", "-q", "-m", "work")
        self.finish(first["name"], final="Package done, more to do.")
        self.round()
        self.assertEqual(len(self.queens()), 2)
        self.assertEqual(runtime.list_questions(self.conn, "testproj", status="open", plan=REL), [])

    def test_context_handoff_without_commit_restarts_at_once(self):
        """A Queen that ended because of the HANDOFF file did real handoff work and is replaced at once."""
        first = self.queen()
        flag = self.env().run_dir(first["name"]) / "HANDOFF"
        flag.parent.mkdir(parents=True, exist_ok=True)
        flag.write_text("x", encoding="utf-8")
        self.finish(first["name"], final="Handoff written.")
        self.round()
        self.assertEqual(len(self.queens()), 2)

    def ask(self, n):
        """File `n` open owner questions for the dummy plan and return their ids."""
        from haivequeen import db
        return [db.add_question(self.conn, "testproj", REL, "item-%d" % i, "Question %d?" % i, "Recommendation") for i in range(n)]

    def test_open_owner_questions_and_an_empty_final_report_wait_for_the_answer(self):
        """A Queen that ends without a new commit while owner questions are open sends the Hive to 'answer' whatever her final text says; no new question is filed."""
        ids = self.ask(3)
        first = self.queen()
        self.finish(first["name"], final="")
        self.round()
        self.assertEqual(len(self.queens()), 1)
        hive = hives.get(self.conn, "testproj", REL)
        self.assertEqual(hive["state"], "answer")
        self.assertEqual(json.loads(hive["questions_json"]), ids)
        self.assertEqual(len(runtime.list_questions(self.conn, "testproj", status="open", plan=REL)), 3)

    def test_a_provider_wait_does_not_hide_the_unfinished_end(self):
        """The Queen asked for a Bee (Hive in codex_wait) and then ended without a commit while owner questions are open: no new Queen at once, the Hive waits for an answer."""
        self.ask(2)
        first = self.queen()
        bees.wait_for_provider(self.conn, "testproj", REL, "codex")
        self.finish(first["name"], final="")
        self.round()
        self.round()
        self.assertEqual(len(self.queens()), 1)
        self.assertEqual(hives.get(self.conn, "testproj", REL)["state"], "answer")

    def test_a_new_answer_starts_the_next_queen_again(self):
        """After an answer to one of the open questions the next Queen starts."""
        from haivequeen import db
        ids = self.ask(2)
        first = self.queen()
        self.finish(first["name"], final="")
        self.round()
        db.answer_question(self.conn, ids[0], "Count it as closed.", "owner")
        self.round()
        self.assertEqual(len(self.queens()), 2)

    def test_answer_starts_the_next_queen(self):
        """When the owner answers the automatic question, the Hive starts the next Queen."""
        from haivequeen import db
        first = self.queen()
        self.finish(first["name"], final="Stuck.")
        self.round()
        question = runtime.list_questions(self.conn, "testproj", status="open", plan=REL)[0]
        db.answer_question(self.conn, question["id"], "Go on, count M2.4 as closed.", "owner")
        self.round()
        self.assertEqual(len(self.queens()), 2)


if __name__ == "__main__":
    unittest.main()

"""Owner check after integration: a Plan whose plan has points only the owner can check stays open in the state 'owner_check'."""
import subprocess

from bzztest import REL, RoundCase
from haivequeen import bees, db, hives, plans, runtime, owner

OTHER = "docs/plans/other/OTHER-PLAN.md"


class OwnerCheckTests(RoundCase):
    """The owner check question keeps an integrated Plan open; the owner's answer closes the plan or sends the next Queen to fix exactly that."""

    def finish(self, name, final="", status="done", project="testproj"):
        """Simulate a finished runner, including its process exit."""
        super().finish(name, final=final, status=status, project=project)
        runtime.upsert_agent(self.conn, project, name, {"pid": None})

    def integrated_hive_with_question(self, item="owner-check"):
        """Start the Queen, mark her branch integrated, let her ask the owner check question (item `item`) and end; return the question id."""
        self.plan("READY", [{"id": "P1", "title": "First"}])
        self.round()
        queen = [a for a in runtime.list_agents(self.conn, "testproj", "all") if a["status"] in ("running", "starting")][0]
        def git(*args):
            return subprocess.run(["git", "-C", str(self.repo), *args], check=True, capture_output=True, text=True).stdout.strip()
        git("init", "-q", "-b", "main")
        git("config", "user.name", "Test")
        git("config", "user.email", "test@example.invalid")
        git("add", "-A")
        git("commit", "-qm", "integrated plan")
        tip = git("rev-parse", "HEAD")
        ready = db.add_ready(self.conn, "testproj", "branch", tip, REL, "")
        self.conn.execute("UPDATE hives SET ready_id=? WHERE project='testproj' AND plan_path=?", (ready["id"], REL))
        runtime.queue_upsert(self.conn, "testproj", "branch", status="integrated")
        qid = db.add_question(self.conn, "testproj", REL, item, "Please check the courtyard light.", "")
        self.finish(queen["name"], final="Integrated and deployed. WAITING_FOR_OWNER")
        self.round()
        return qid

    def status(self):
        """The plan status read from the plan file."""
        return plans.parse_plan_text((self.repo / REL).read_text(encoding="utf-8")).status

    def queens(self):
        """All Queen rows of the dummy plan."""
        return [a for a in runtime.list_agents(self.conn, "testproj", "all") if a["phase"] == "queen"]

    def test_owner_check_requires_contained_integrated_tip(self):
        """A linked integrated row cannot justify an owner check for an unknown tip."""
        self.integrated_hive_with_question()
        hive = hives.get(self.conn, "testproj", REL)
        self.assertIsNotNone(hives.integrated_ready(self.conn, self.env(), hive))
        self.conn.execute("UPDATE queue SET tip='abcdef0' WHERE id=?", (hive["ready_id"],))
        self.assertIsNone(hives.integrated_ready(self.conn, self.env(), hive))

    def test_open_question_keeps_the_hive_open(self):
        """After integration the plan is not DONE while the owner check question is open; the Plan waits in 'owner_check' and no Queen starts."""
        self.integrated_hive_with_question()
        self.assertNotEqual(self.status(), "DONE")
        self.assertEqual(hives.get(self.conn, "testproj", REL)["state"], "owner_check")
        self.assertEqual(len(self.queens()), 1)

    def test_ok_answer_leaves_the_plan_open(self):
        """A free-text answer is only a note."""
        qid = self.integrated_hive_with_question()
        db.answer_question(self.conn, qid, "ok", "owner")
        self.round()
        self.assertEqual(self.status(), "REVIEW")

    def test_item_with_a_suffix_is_an_owner_check_too(self):
        """A Queen that names the item owner-check-build17 keeps the Plan open; a not-ok answer reopens it and the plan never becomes DONE."""
        qid = self.integrated_hive_with_question("owner-check-build17")
        self.assertNotEqual(self.status(), "DONE")
        self.assertEqual(hives.get(self.conn, "testproj", REL)["state"], "owner_check")
        db.answer_question(self.conn, qid, "Nicht in Ordnung: er faellt noch um.", "owner")
        self.round()
        self.assertNotEqual(self.status(), "DONE")
        self.assertEqual(len(self.queens()), 1)
        self.assertEqual(db.rows(self.conn, "SELECT item FROM questions WHERE id=?", (qid,))[0]["item"], "owner-check-build17")
        db.add_question(self.conn, "testproj", REL, "owner-check-build18", "Check again.", "")
        self.assertEqual([q["item"] for q in hives.owner_check_questions(self.conn, "testproj", REL)], ["owner-check-build17", "owner-check-build18"])

    def test_other_answer_sends_the_next_queen_to_fix(self):
        """Any other answer starts the next Queen of the same Plan; the plan stays open and the old ready row no longer closes it."""
        qid = self.integrated_hive_with_question()
        db.answer_question(self.conn, qid, "The courtyard is still too green.", "owner")
        self.round()
        self.assertNotEqual(self.status(), "DONE")
        self.assertEqual(len(self.queens()), 1)
        self.assertEqual(hives.get(self.conn, "testproj", REL)["state"], "owner_check")
        self.round()
        self.assertNotEqual(self.status(), "DONE")
        self.assertEqual(len(self.queens()), 1)

    def test_owner_check_does_not_count_against_the_hive_limit(self):
        """A Plan waiting for the owner check leaves its place to another plan."""
        self.machine["hives"] = 1
        self.integrated_hive_with_question()
        self.plan("READY", [{"id": "P1", "title": "Other"}], rel=OTHER, title="Other plan")
        self.round()
        self.assertTrue([a for a in runtime.list_agents(self.conn, "testproj", "all") if a["plan_path"] == OTHER and a["phase"] == "queen"])

    def test_explicit_rejection_starts_a_fix_agent_with_the_owner_finding(self):
        """O-6: no Queen starts for the fix; the Orchestrator starts a fix agent that gets the owner's answer."""
        self.integrated_hive_with_question()
        owner.apply(self.conn, "reject", "testproj", REL, text="Fix the courtyard lighting")
        self.round()
        self.assertEqual(len(self.queens()), 1)
        fixers = [a for a in runtime.list_agents(self.conn, "testproj", "all") if a["phase"] == "fix"]
        self.assertEqual(len(fixers), 1)
        prompt = self.env().run_dir(fixers[0]['name']) / 'prompt.md'
        self.assertIn("Fix the courtyard lighting", prompt.read_text(encoding='utf-8'))
        self.assertNotEqual(self.status(), "DONE")

    def test_explicit_acceptance_survives_round(self):
        self.integrated_hive_with_question()
        owner.apply(self.conn, "accept", "testproj", REL)
        self.round()
        self.assertEqual(self.status(), "DONE")
        self.assertEqual(len(self.queens()), 1)

    def test_queen_is_not_told_to_file_the_owner_check(self):
        """O-1: HaiveQueen files the owner check after the integration; the Queen ends at bzz ready."""
        text = bees.queen_instructions("medium", 3, 3, 300000)
        self.assertNotIn("--item owner-check", text)
        self.assertIn("bzz ready", text)

"""Owner check after integration: a Hive whose plan has points only the owner can check stays open in the state 'owner_check' (HQ-DISPLAY K4)."""
from bzztest import REL, RoundCase
from haivequeen import bees, db, hives, plans, runtime

OTHER = "docs/plans/other/OTHER-PLAN.md"


class OwnerCheckTests(RoundCase):
    """The owner check question keeps an integrated Hive open; the owner's answer closes the plan or sends the next Queen to fix exactly that."""

    def finish(self, name, final="", status="done", project="testproj"):
        """Simulate a finished runner, including its process exit."""
        super().finish(name, final=final, status=status, project=project)
        runtime.upsert_agent(self.conn, project, name, {"pid": None})

    def integrated_hive_with_question(self):
        """Start the Queen, mark her branch integrated, let her ask the owner check question and end; return the question id."""
        self.plan("READY", [{"id": "P1", "title": "First"}])
        self.round()
        queen = [a for a in runtime.list_agents(self.conn, "testproj", "all") if a["status"] in ("running", "starting")][0]
        ready = db.add_ready(self.conn, "testproj", "branch", "abc", REL, "")
        self.conn.execute("UPDATE hives SET ready_id=? WHERE project='testproj' AND plan_path=?", (ready["id"], REL))
        runtime.queue_upsert(self.conn, "testproj", "branch", status="integrated")
        qid = db.add_question(self.conn, "testproj", REL, "owner-check", "Please check the courtyard light.", "")
        self.finish(queen["name"], final="Integrated and deployed. WAITING_FOR_OWNER")
        self.round()
        return qid

    def status(self):
        """The plan status read from the plan file."""
        return plans.parse_plan_text((self.repo / REL).read_text(encoding="utf-8")).status

    def queens(self):
        """All Queen rows of the dummy plan."""
        return [a for a in runtime.list_agents(self.conn, "testproj", "all") if a["phase"] == "queen"]

    def test_open_question_keeps_the_hive_open(self):
        """After integration the plan is not DONE while the owner check question is open; the Hive waits in 'owner_check' and no Queen starts."""
        self.integrated_hive_with_question()
        self.assertNotEqual(self.status(), "DONE")
        self.assertEqual(hives.get(self.conn, "testproj", REL)["state"], "owner_check")
        self.assertEqual(len(self.queens()), 1)

    def test_ok_answer_makes_the_plan_done(self):
        """The answer 'ok' closes the plan."""
        qid = self.integrated_hive_with_question()
        db.answer_question(self.conn, qid, "ok", "owner")
        self.round()
        self.assertEqual(self.status(), "DONE")

    def test_other_answer_sends_the_next_queen_to_fix(self):
        """Any other answer starts the next Queen of the same Hive; the plan stays open and the old ready row no longer closes it."""
        qid = self.integrated_hive_with_question()
        db.answer_question(self.conn, qid, "The courtyard is still too green.", "owner")
        self.round()
        self.assertNotEqual(self.status(), "DONE")
        self.assertEqual(len(self.queens()), 2)
        self.assertIsNone(hives.get(self.conn, "testproj", REL)["ready_id"])
        self.round()
        self.assertNotEqual(self.status(), "DONE")
        self.assertEqual(len(self.queens()), 2)

    def test_owner_check_does_not_count_against_the_hive_limit(self):
        """A Hive waiting for the owner check leaves its place to another plan."""
        self.machine["hives"] = 1
        self.integrated_hive_with_question()
        self.plan("READY", [{"id": "P1", "title": "Other"}], rel=OTHER, title="Other plan")
        self.round()
        self.assertTrue([a for a in runtime.list_agents(self.conn, "testproj", "all") if a["plan_path"] == OTHER and a["phase"] == "queen"])

    def test_queen_is_told_to_ask_for_the_play_test(self):
        """The Queen instructions say how to ask for the owner check after integration and deploy."""
        text = bees.queen_instructions("medium", 3, 3, 300000)
        self.assertIn("--item owner-check", text)
        self.assertIn("after integration and deploy", text.lower())

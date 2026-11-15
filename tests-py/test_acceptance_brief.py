import copy
import json
from unittest.mock import patch

from bzztest import RoundCase, REL
from haivequeen import acceptance, hives, runtime


class AcceptanceBriefTests(RoundCase):
    def setUp(self):
        super().setUp()
        self.evidence = {"packages": [{"id": "menu"}, {"id": "art"}],
                         "decisions": [{"id": 12, "source": "owner", "text": "Restore Load game"}],
                         "answers": [{"id": 8, "answer": "Art accepted", "answered_by": "owner"}],
                         "integration": {"commit": "abc123", "steps": ["Build"], "deployed": "abc123"}}
        self.brief = {"decisions_considered": [12], "packages": [
            {"id": "menu", "summary": "Load game is available again", "state": "pending",
             "evidence": ["menu result"], "steps": [{"action": "Start a game and open F10", "expected": "Load game is visible"},
                 {"action": "Open Load game and cancel", "expected": "The same game continues"},
                 {"action": "Choose another save", "expected": "Its saved state loads"}]},
            {"id": "art", "summary": "Menu artwork", "state": "accepted", "evidence": ["art result"], "acceptance_ids": [8]}],
            "checks": [{"text": "Menu regression passed", "evidence": "test result"}],
            "uncertainties": [], "technical_notes": ["Port selection repaired"]}

    def test_multipart_later_decision_and_partial_acceptance(self):
        acceptance.validate(self.brief, self.evidence)
        text = acceptance.render(self.brief, self.evidence, "English")
        self.assertIn("[menu]", text)
        self.assertIn("The same game continues", text)
        self.assertIn("Already accepted", text)
        self.assertLess(text.index("Load game"), text.index("Port selection"))
        self.assertIn("abc123", text)

    def test_missing_package_or_later_decision_is_not_publishable(self):
        for key, value in (("packages", self.brief["packages"][:1]), ("decisions_considered", [])):
            broken = dict(self.brief, **{key: value})
            with self.assertRaises(ValueError):
                acceptance.validate(broken, self.evidence)

    def test_no_unproven_acceptance_or_vague_manual_step(self):
        for change in ("acceptance", "steps", "evidence"):
            broken = copy.deepcopy(self.brief)
            if change == "acceptance":
                broken["packages"][1]["acceptance_ids"] = [999]
            else:
                broken["packages"][0][change] = []
            with self.assertRaises(ValueError):
                acceptance.validate(broken, self.evidence)

    def test_build_execution_does_not_prove_success_or_installation(self):
        self.evidence["integration"].pop("deployed")
        self.brief["checks"] = []
        text = acceptance.render(self.brief, self.evidence, "English")
        self.assertIn("not verified", text)
        self.assertNotIn("Build passed", text)

    def test_scheduler_waits_and_reuses_same_review(self):
        self.plan("READY", packages=[{"id": "menu", "title": "Menu"}])
        env = self.env()
        with patch("haivequeen.readygate._launch_check_agent", return_value=("review-1", "")) as launch:
            self.assertIsNone(acceptance.prepare(self.conn, env, REL))
            self.assertIsNone(acceptance.prepare(self.conn, env, REL))
            self.assertEqual(launch.call_count, 1)

    def test_fix_report_cannot_replace_factual_brief(self):
        self.plan("READY")
        env = self.env()
        with patch.object(acceptance, "prepare", return_value="Functional acceptance brief"), patch.object(hives, "wait_for_owner_check"):
            qid = hives.file_owner_check(self.conn, env, REL)
        row = self.conn.execute("SELECT question FROM questions WHERE id=?", (qid,)).fetchone()
        self.assertEqual(row[0], "Functional acceptance brief")

    def test_completed_review_is_rendered_and_cached(self):
        self.plan("READY")
        env = self.env()
        runtime.upsert_agent(self.conn, env.name, "review-1", {"status": "done", "phase": "verify", "package": "OWNER-ACCEPTANCE", "plan_path": REL})
        with patch.object(acceptance, "evidence", return_value=self.evidence), patch("haivequeen.readygate._launch_check_agent", return_value=("review-1", "")) as launch, patch("haivequeen.readygate.answer_text", return_value=json.dumps(self.brief)), patch("haivequeen.agent_process.is_live", return_value=False):
            self.assertIsNone(acceptance.prepare(self.conn, env, REL))
            first = acceptance.prepare(self.conn, env, REL)
            self.assertIn("Load game", first)
            self.assertEqual(acceptance.prepare(self.conn, env, REL), first)
            self.assertEqual(launch.call_count, 1)
            self.evidence["decisions"].append({"id": 13, "source": "owner", "text": "New correction"})
            self.assertIsNone(acceptance.prepare(self.conn, env, REL))
            self.assertEqual(launch.call_count, 2)

    def test_review_status_change_does_not_start_another_review(self):
        self.plan("READY")
        env = self.env()
        with patch("haivequeen.readygate._launch_check_agent", return_value=("review-1", "")) as launch:
            acceptance.prepare(self.conn, env, REL)
            runtime.set_plan_status(self.conn, env.name, REL, "REVIEW", str(env.workspace))
            acceptance.prepare(self.conn, env, REL)
            self.assertEqual(launch.call_count, 1)

    def test_owner_decision_can_prove_previous_acceptance(self):
        self.brief["packages"][1].pop("acceptance_ids")
        self.brief["packages"][1]["acceptance_decisions"] = [12]
        acceptance.validate(self.brief, self.evidence)

    def test_malformed_result_gets_one_reasoned_correction_then_fault(self):
        self.plan("READY")
        env = self.env()
        runtime.upsert_agent(self.conn, env.name, "review-1", {"status": "done", "phase": "verify", "package": "OWNER-ACCEPTANCE", "plan_path": REL})
        with patch.object(acceptance, "evidence", return_value=self.evidence), patch("haivequeen.readygate._launch_check_agent", return_value=("review-1", "")) as launch, patch("haivequeen.readygate.answer_text", return_value="just the port fix"), patch("haivequeen.agent_process.is_live", return_value=False), patch("haivequeen.faults.file_fault") as fault:
            for _ in range(4):
                self.assertIsNone(acceptance.prepare(self.conn, env, REL))
            self.assertEqual(launch.call_count, 2)
            self.assertEqual(fault.call_count, 1)
            self.assertIn("Previous validation finding", launch.call_args.args[2]["prompt"])

    def test_partial_unshipped_requirement_remains_separate(self):
        self.brief["open_requirements"] = [{"package": "menu", "text": "Controller navigation not delivered"}]
        acceptance.validate(self.brief, self.evidence)
        text = acceptance.render(self.brief, self.evidence, "German")
        self.assertIn("Offen / nicht nachweisbar ausgeliefert", text)
        self.assertIn("[menu] Controller navigation not delivered", text)

    def test_real_launcher_keeps_acceptance_prompt_read_only(self):
        from haivequeen import readygate
        self.plan("READY")
        env = self.env()
        name, reason = readygate._launch_check_agent(env, {"name": "acceptance"},
            {"plan": REL, "cwd": str(env.code_repo), "prompt": "Prepare the brief", "package": "OWNER-ACCEPTANCE"})
        self.assertTrue(name, reason)
        prompt = (env.run_dir(name) / "prompt.md").read_text(encoding="utf-8")
        self.assertIn("Read-only", prompt)
        self.assertNotIn("Update the plan's state", prompt)
        self.assertNotIn("in the plan state file under", prompt)

    def test_review_fault_is_visible_and_resolves_after_valid_description(self):
        from datetime import datetime
        from haivequeen import faults
        self.plan("READY")
        env = self.env()
        key = "acceptance-review:" + REL
        runtime.kv_set(self.conn, env.name, key, json.dumps({"error": "No package coverage"}))
        qid = faults.file_fault(self.conn, env.name, "acceptance_review", key)
        faults._resolve_open(env, 0, datetime.now())
        self.assertEqual(self.conn.execute("SELECT status FROM questions WHERE id=?", (qid,)).fetchone()[0], "open")
        runtime.kv_set(self.conn, env.name, key, json.dumps({"brief": self.brief}))
        faults._resolve_open(env, 0, datetime.now())
        self.assertEqual(self.conn.execute("SELECT status FROM questions WHERE id=?", (qid,)).fetchone()[0], "answered")

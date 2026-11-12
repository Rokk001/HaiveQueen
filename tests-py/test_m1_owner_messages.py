"""M1: owner decisions stored while a Queen runs reach her once in the answer of her next bzz command."""
import contextlib
import io
import json
import os
import unittest
from datetime import datetime
from unittest import mock

import bzztest
from bzztest import REL, RoundCase, db, runtime
from haivequeen import cli, inbox, profile


class OwnerMessageTest(RoundCase):
    """Commands run through cli.main with BZZ_AGENT set, like a Queen's bzz call."""

    def setUp(self):
        super().setUp()
        self.agent("Mia", phase="queen", status="running", started=5)
        self.agent("Nora", phase="queen", status="running", started=5, plan_path="other.md")

    def bzz(self, *argv, agent="Mia"):
        """Run a bzz command as the agent and return (raw stdout, parsed JSON)."""
        env = {"BZZ_AGENT": agent} if agent else {}
        buf = io.StringIO()
        with mock.patch.dict(os.environ, env), contextlib.redirect_stdout(buf):
            if not agent:
                os.environ.pop("BZZ_AGENT", None)
            cli.main(list(argv))
        return buf.getvalue(), json.loads(buf.getvalue())

    def note(self, agent="Mia"):
        return self.bzz("note", "--project", "testproj", "--text", "x", agent=agent)

    def test_plan_and_project_decisions_once_with_delivery_mark(self):
        own = runtime.add_decision(self.conn, "testproj", "owner", "OWN-TEXT", REL)
        wide = runtime.add_decision(self.conn, "testproj", "owner", "WIDE-TEXT")
        runtime.add_decision(self.conn, "testproj", "owner", "OTHER-PLAN", "other.md")
        _raw, res = self.note()
        self.assertEqual(res["owner_messages"], [{"id": own, "text": "OWN-TEXT"}, {"id": wide, "text": "WIDE-TEXT"}])
        self.assertNotIn("owner_messages", self.note()[1])
        marks = self.conn.execute("SELECT decision_id, agent FROM decision_deliveries ORDER BY decision_id").fetchall()
        self.assertEqual([(r[0], r[1]) for r in marks], [(own, "Mia"), (wide, "Mia")])

    def test_list_command_delivers_pending_message_once(self):
        did = runtime.add_decision(self.conn, "testproj", "owner", "LIST-MESSAGE", REL)
        _, res = self.bzz("decisions", "list", "--project", "testproj")
        self.assertEqual(res["owner_messages"], [{"id": did, "text": "LIST-MESSAGE"}])
        self.assertIsInstance(res["result"], list)
        self.assertNotIn("owner_messages", self.note()[1])

    def test_text_and_error_commands_deliver(self):
        first = runtime.add_decision(self.conn, "testproj", "owner", "TEXT", REL)
        _, res = self.bzz("status", "--project", "testproj")
        self.assertEqual(res["owner_messages"], [{"id": first, "text": "TEXT"}])
        self.assertIsInstance(res["result"], str)
        second = runtime.add_decision(self.conn, "testproj", "owner", "ERROR", REL)
        _, res = self.bzz("decisions", "update", "999999", "--status", "active")
        self.assertFalse(res["ok"])
        self.assertEqual(res["owner_messages"], [{"id": second, "text": "ERROR"}])

    def test_superseded_pending_decisions_are_delivered_in_order(self):
        first = runtime.add_decision(self.conn, "testproj", "owner", "FIRST", REL, "owner-release")
        second = runtime.add_decision(self.conn, "testproj", "owner", "SECOND", REL, "owner-release")
        self.assertEqual(self.note()[1]["owner_messages"], [
            {"id": first, "text": "FIRST"}, {"id": second, "text": "SECOND"}])
        self.assertNotIn("owner_messages", self.note()[1])

    def test_bzz_decisions_add_is_delivered(self):
        self.bzz("decisions", "add", "--project", "testproj", "--source", "owner", "--plan", REL, "--text", "VIA-CLI", agent=None)
        self.assertEqual([m["text"] for m in self.note()[1]["owner_messages"]], ["VIA-CLI"])

    def test_inbox_decision_is_delivered(self):
        box = inbox.inbox_dir(self.repo)
        box.mkdir(parents=True)
        (box / "a.toml").write_text('[[decision]]\ntext = "VIA-INBOX"\nsource = "owner"\n', encoding="utf-8")
        self.round()
        self.agent("Mia", phase="queen", status="running", started=5)   # the round may close the fake Queen
        self.assertEqual([m["text"] for m in self.note()[1]["owner_messages"]], ["VIA-INBOX"])

    def test_dashboard_store_is_delivered(self):
        runtime.add_decision(self.conn, "testproj", "owner", "any commit", REL, "owner-release")
        self.assertEqual(len(self.note()[1]["owner_messages"]), 1)

    def test_coordinator_and_auto_decisions_are_not_delivered(self):
        runtime.add_decision(self.conn, "testproj", "coordinator", "COORD", REL)
        runtime.add_decision(self.conn, "testproj", "auto", "AUTO", REL)
        raw, res = self.note()
        self.assertNotIn("owner_messages", res)

    def test_answer_unchanged_without_message(self):
        raw, res = self.note()
        self.assertEqual(raw, json.dumps({"ok": True, "id": res["id"]}) + "\n")
        runtime.add_decision(self.conn, "testproj", "owner", "OLD", REL)
        self.note()
        raw2, res2 = self.note()
        self.assertEqual(raw2, json.dumps({"ok": True, "id": res2["id"]}) + "\n")
        # a caller that is no running Queen gets no message
        runtime.add_decision(self.conn, "testproj", "owner", "NEW", REL)
        self.assertNotIn("owner_messages", self.bzz("note", "--project", "testproj", "--text", "x", agent=None)[1])
        self.assertNotIn("owner_messages", self.note(agent="Unknown")[1])

    def test_each_running_queen_of_a_project_decision_gets_it(self):
        runtime.add_decision(self.conn, "testproj", "owner", "WIDE")
        self.assertEqual(len(self.note("Mia")[1]["owner_messages"]), 1)
        self.assertEqual(len(self.note("Nora")[1]["owner_messages"]), 1)

    def test_decision_stored_before_the_queen_started_is_not_an_owner_message(self):
        old = runtime.add_decision(self.conn, "testproj", "owner", "OLD", REL)
        old_wide = runtime.add_decision(self.conn, "testproj", "owner", "OLD-WIDE")
        self.conn.execute("UPDATE decisions SET ts=? WHERE id IN (?,?)", (bzztest.minutes_ago(60), old, old_wide))
        self.agent("Late", phase="queen", status="running", started=1)
        self.assertNotIn("owner_messages", self.note("Late")[1])
        self.assertEqual(self.conn.execute("SELECT COUNT(*) FROM decision_deliveries WHERE agent='Late'").fetchone()[0], 2)

    def test_decision_before_start_in_same_second_is_not_delivered(self):
        self.agent("Zoe", phase="work", status="done")
        with mock.patch.object(db, "datetime") as clock:
            clock.now.return_value = datetime(2026, 1, 1, 12, 0, 0, 100000)
            runtime.add_decision(self.conn, "testproj", "owner", "BEFORE", REL)
            clock.now.return_value = datetime(2026, 1, 1, 12, 0, 0, 200000)
            self.agent("Zoe", phase="queen", status="starting", started_at=db.now())
            clock.now.return_value = datetime(2026, 1, 1, 12, 0, 0, 300000)
            after = runtime.add_decision(self.conn, "testproj", "owner", "AFTER", REL)
            self.assertEqual(self.note("Zoe")[1]["owner_messages"], [{"id": after, "text": "AFTER"}])

    def test_ended_queen_leaves_it_to_the_next_start_prompt_once(self):
        did = runtime.add_decision(self.conn, "testproj", "owner", "LEFT-OVER", REL)
        self.conn.execute("UPDATE agents SET status='done' WHERE name='Mia'")
        self.assertNotIn("owner_messages", self.note()[1])
        text = profile.queen_load(self.conn, "testproj", self.repo, REL, None, "Zoe")
        self.assertIn("LEFT-OVER", text)
        self.agent("Zoe", phase="queen", status="running", started=1)
        self.assertNotIn("owner_messages", self.note("Zoe")[1])
        self.assertEqual(self.conn.execute("SELECT agent FROM decision_deliveries WHERE decision_id=?", (did,)).fetchall()[0][0], "Zoe")


class QueenRoleTextTest(unittest.TestCase):
    def test_role_text(self):
        from pathlib import Path
        text = (Path(__file__).resolve().parent.parent / "roles" / "queen.md").read_text(encoding="utf-8")
        self.assertIn("owner_messages", text)
        self.assertIn("applies at once", text)
        self.assertIn("wins over the plan and earlier decisions", text)
        self.assertIn("bzz note", text[text.index("owner_messages"):])


if __name__ == "__main__":
    unittest.main()

"""Tests of package classes and the Queen workflow without the old package locks."""
import unittest

from bzztest import REL, RoundCase, runtime
from haivequeen import routine
from haivequeen import scheduler as sch


def pk(i, title, deps="none", cls=""):
    """Package dict for write_plan."""
    return {"id": i, "title": title, "depends": [] if deps == "none" else [deps], "class": cls}


class S10Test(RoundCase):
    """Classes, bundling, escalation and the lock, without a pane and without a model."""

    def setUp(self):
        """Every test starts with the seed worktree row."""
        super().setUp()
        self.seed()

    # Verify package classes and the configured three-attempt escalation threshold.
    def test_class_units(self):
        """Plan classes are retained and the default escalation threshold is three."""
        self.plan("IN_PROGRESS", [pk("A1", "one", cls="light"), pk("A2", "two", cls="Heavy"), pk("A3", "three"), pk("A4", "four", cls="bogus")])
        env = self.env()
        self.assertEqual([p["cls"] for p in env.packages(REL)], ["light", "heavy", "medium", "medium"])
        self.assertEqual(env.settings["escalateAfter"], 3)
        self.assertEqual(sch.resolve_agent_class(env.settings, "work", "light", 2), "light")
        self.assertEqual(sch.resolve_agent_class(env.settings, "work", "light", 3), "medium")
        self.assertEqual(sch.resolve_agent_class(env.settings, "work", "light", 6), "heavy")
        self.assertEqual(sch.class_model(env.providers, "codex", "medium")["Model"], "gpt-6.1-terra")

    # Verify one Queen receives the plan without old package bundling.
    def test_bundling(self):
        """Independent packages remain in the plan and one Queen starts."""
        self.plan("IN_PROGRESS", [pk("L1", "first", cls="light"), pk("L2", "second", cls="light"), pk("L3", "needs first", "L1", "light")])
        self.round()
        rows = [r for r in runtime.list_agents(self.conn, "testproj", "active") if r["status"] == "running"]
        self.assertEqual(len(rows), 1)
        self.assertEqual(rows[0]["phase"], "queen")
        self.assertFalse(rows[0].get("bundle_json"))
        self.assertEqual(len(self.env().packages(REL)), 3)

    # Verify old verification results do not escalate a Queen.
    def test_escalation(self):
        """Old verification history does not change the configured Queen class."""
        self.plan("IN_PROGRESS", [pk("E1", "single light task", cls="light")])
        self.agent("old-verify", package="E1", phase="verify", status="done", started=20, ended=10)
        self.finish("old-verify", "Offene Anforderungen: 2")
        self.round()
        rows = [r for r in runtime.list_agents(self.conn, "testproj", "active") if r["status"] == "running"]
        self.assertEqual(len(rows), 1)
        self.assertEqual((rows[0]["phase"], rows[0]["class"]), ("queen", "medium"))

    # Verify old verification streaks no longer create a package lock.
    def test_lock_without_triage(self):
        """Repeated old verification results do not prevent the Queen starting."""
        self.plan("IN_PROGRESS", [pk("K1", "task", cls="medium")])
        for i in range(3):
            name = "old-verify-%d" % i
            self.agent(name, package="K1", phase="verify", status="done", started=60-i*10, ended=55-i*10)
            self.finish(name, "Offene Anforderungen: 1")
        self.round()
        rows = [r for r in runtime.list_agents(self.conn, "testproj", "active") if r["status"] == "running"]
        self.assertEqual(len(rows), 1)
        self.assertEqual(rows[0]["phase"], "queen")
        self.assertFalse(runtime.list_packages(self.conn, "testproj", REL)[0]["locked"])


class LockQuestionWordsTest(unittest.TestCase):
    """The words that mark a question as a package lock match whole words only."""

    def test_locked_is_matched_as_a_word(self):
        """"locked" and "gesperrt" match; "unlocked" does not."""
        from haivequeen import routine
        self.assertTrue(routine.LOCK_QUESTION_WORDS.search("package is locked after two reviews"))
        self.assertTrue(routine.LOCK_QUESTION_WORDS.search("Paket gesperrt"))
        self.assertFalse(routine.LOCK_QUESTION_WORDS.search("the door is unlocked"))


if __name__ == "__main__":
    unittest.main()

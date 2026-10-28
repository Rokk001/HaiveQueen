"""Port of test-s10.ps1: effort classes, bundling of small packages, escalation, minimum class of verify and integrator, package lock."""
import unittest

from orchtest import REL, RoundCase, runtime
from orchlib import routine
from orchlib import scheduler as sch


def pk(i, title, deps="none", cls=""):
    """Package dict for write_plan."""
    return {"id": i, "title": title, "depends": [] if deps == "none" else [deps], "class": cls}


class S10Test(RoundCase):
    """Classes, bundling, escalation and the lock, without a pane and without a model."""

    def setUp(self):
        """Every test starts with the seed worktree row."""
        super().setUp()
        self.seed()

    def test_class_units(self):
        """Class column, resolution with escalation and minimum, class models."""
        self.plan("IN_PROGRESS", [pk("A1", "one", cls="light"), pk("A2", "two", cls="Heavy"), pk("A3", "three"), pk("A4", "four", cls="bogus")])
        env = self.env()
        self.assertEqual([p["cls"] for p in env.packages(REL)], ["light", "heavy", "medium", "medium"])
        s = env.settings
        r = lambda ph, c, f=0: sch.resolve_agent_class(s, ph, c, f)  # noqa: E731
        self.assertEqual(r("verify", "light"), "medium")
        self.assertEqual(r("integrate", "light"), "medium")
        self.assertEqual(r("verify", "heavy"), "heavy")
        self.assertEqual(r("work", "light", 1), "light")
        self.assertEqual(r("work", "light", 2), "medium")
        self.assertEqual(r("work", "light", 4), "heavy")
        self.assertEqual((r("work", "medium", 2), r("work", "heavy", 9)), ("heavy", "heavy"))
        self.assertEqual(r("work", "bogus"), "medium")
        cm = lambda p, c: sch.class_model(env.providers, p, c)["Model"]  # noqa: E731
        self.assertEqual((cm("claude", "light"), cm("claude", "medium"), cm("claude", "heavy")), ("claude-haiku-5-5", "claude-sonnet-5-5", "claude-opus-5-5"))
        self.assertEqual(cm("codex", "medium"), "gpt-6.1-terra")

    def test_bundling(self):
        """Light packages of one plan share one agent (up to maxPackages); a dependent light package does not start."""
        self.plan("IN_PROGRESS", [pk("L1", "first small", cls="light"), pk("L2", "second small", cls="light"), pk("L3", "third small", cls="light"),
                                  pk("L4", "fourth small", cls="light"), pk("M1", "medium task", cls="medium"), pk("L5", "needs L1", "L1", "light")])
        self.round()
        rows = runtime.list_agents(self.conn, "testproj", "active")
        b1 = [r for r in rows if r["phase"] == "work" and r.get("bundle_json") and len(eval(r["bundle_json"])) > 1]
        self.assertEqual(len(b1), 1)
        self.assertEqual(len(eval(b1[0]["bundle_json"])), 3)
        self.assertEqual((b1[0]["class"], b1[0]["modelShort"][:3]), ("light", "Hai"))
        prompt = (self.env().run_dir(b1[0]["name"]) / "prompt.md").read_text(encoding="utf-8")
        for p in ("L1", "L2", "L3"):
            self.assertIn("package %s" % p, prompt)
        self.assertFalse([r for r in rows if r["phase"] == "work" and r["package"] in ("L2", "L3")], "bundled packages count as running")
        self.assertFalse([r for r in rows if r["package"] == "L5"])
        self.assertLessEqual(len([r for r in rows if r["phase"] == "work"]), 4)
        self.finish(b1[0]["name"])
        self.round()
        v = [r for r in runtime.list_agents(self.conn, "testproj", "active") if r["phase"] == "verify"]
        self.assertTrue(v)
        self.assertTrue(all(r["class"] == "medium" for r in v), "verify runs at least at medium")

    def test_escalation(self):
        """Light stays light after one open verify, medium after two."""
        self.plan("IN_PROGRESS", [pk("E1", "single light task", cls="light")])
        self.round()
        e1 = self.ag("DUMMY-E1-work")
        self.assertEqual(e1["class"], "light")
        self.finish("DUMMY-E1-work")
        self.round()
        self.finish("DUMMY-E1-verify", "Offene Anforderungen: 2")
        self.round()
        self.assertEqual(self.ag("DUMMY-E1-work-2")["class"], "light")
        self.finish("DUMMY-E1-work-2")
        self.round()
        self.finish("DUMMY-E1-verify-2", "Offene Anforderungen: 1")
        self.round()
        f2 = self.ag("DUMMY-E1-work-3")
        self.assertEqual(f2["class"], "medium")
        self.assertTrue(f2["modelShort"].startswith("Son"))

    def three_open_verifies(self):
        """A package with three finished work/verify pairs, each verify with open items."""
        self.plan("IN_PROGRESS", [pk("K1", "stubborn task", cls="medium")])
        for i, n in enumerate(("", "-2", "-3")):
            t = 60 - 20 * i
            self.agent("DUMMY-K1-work" + n, package="K1", started=t, ended=t - 5)
            self.agent("DUMMY-K1-verify" + n, type="codex", provider="codex", package="K1", phase="verify", started=t - 10, ended=t - 15)
            self.finish("DUMMY-K1-verify" + n, "Offen: A.\nOffene Anforderungen: %d" % (2 if i == 0 else 1))
            if i == 1:
                self.assertEqual(routine.start_class(self.env(), "work", REL, "K1", ""), "heavy")
                self.assertFalse(routine.package_locked(self.env(), REL, "K1"), "two open verifies: not locked yet")

    def test_lock_without_triage(self):
        """A third open verify locks the package: no new agent, reported once in the database (kv and package row)."""
        self.machine["triage"] = False
        self.three_open_verifies()
        self.assertTrue(routine.package_locked(self.env(), REL, "K1"))
        self.round()
        self.assertFalse([n for n in self.names() if n.startswith(("DUMMY-K1-work-4", "DUMMY-K1-verify-4"))])
        text = runtime.kv_get(self.conn, "testproj", "lock:%s|K1" % REL)
        self.assertIn("Open for Owner - LOCK-K1", text)
        self.assertTrue([p for p in runtime.list_packages(self.conn, "testproj", REL) if p["id"] == "K1"][0]["locked"])
        self.round()
        self.assertEqual(self.log().count("package K1 of"), 1, "the second round does not record it twice")
        self.assertEqual(text.count("Open for Owner - LOCK-K1"), 1)
        self.assertEqual(len(runtime.kv_list(self.conn, "testproj", "lock:")), 1)


class LockQuestionWordsTest(unittest.TestCase):
    """The words that mark a question as a package lock match whole words only."""

    def test_locked_is_matched_as_a_word(self):
        """"locked" and "gesperrt" match; "unlocked" does not."""
        from orchlib import routine
        self.assertTrue(routine.LOCK_QUESTION_WORDS.search("package is locked after two reviews"))
        self.assertTrue(routine.LOCK_QUESTION_WORDS.search("Paket gesperrt"))
        self.assertFalse(routine.LOCK_QUESTION_WORDS.search("the door is unlocked"))


if __name__ == "__main__":
    unittest.main()

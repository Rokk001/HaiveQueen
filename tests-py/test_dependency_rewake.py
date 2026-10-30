"""Tests of the wake-up of a package whose last verify found open items while its dependency was not done yet: when the dependency is done
later, the brain is asked once more about the verify (before, the first answer stayed 'handled' and the package never started again)."""
import unittest

from bzztest import RoundCase, runtime

PKGS = [{"id": "P1", "title": "first"}, {"id": "P2", "title": "second", "depends": ["P1"]}]


class DependencyRewakeTest(RoundCase):
    """P2 has a finished work and a verify with one open item; the event of that verify was handled while P1 was open."""

    def setUp(self):
        """Plan P1 (open) and P2 (verify with open item, event already handled)."""
        super().setUp()
        self.plan("IN_PROGRESS", PKGS)
        self.agent("T-P1-work", package="P1", started=60, ended=55)
        self.agent("T-P1-verify", type="codex", provider="codex", package="P1", phase="verify", started=50, ended=45)
        self.finish("T-P1-verify", "- Item A: teilweise\nOffene Anforderungen: 1")
        self.agent("T-P2-work", package="P2", started=40, ended=35)
        self.agent("T-P2-verify", type="codex", provider="codex", package="P2", phase="verify", started=30, ended=25)
        self.finish("T-P2-verify", "- Item B: teilweise\nOffene Anforderungen: 1")
        runtime.handled_add(self.conn, "testproj", "verify:T-P2-verify", None)
        runtime.handled_add(self.conn, "testproj", "verify:T-P1-verify", None)
        self.note = self.brain_answer({"action": "note", "text": "nothing to start"})

    def finish_p1(self):
        """P1 is verified: a newer verify without open items."""
        self.agent("T-P1-work-2", package="P1", started=20, ended=18)
        self.agent("T-P1-verify-2", type="codex", provider="codex", package="P1", phase="verify", started=17, ended=15)
        self.finish("T-P1-verify-2", "Alles erfuellt\nOffene Anforderungen: 0")

    def test_dependency_done_asks_the_brain_once_more(self):
        """With P1 verified the handled verify of P2 is put to the brain again, once."""
        self.finish_p1()
        before = self.calls()
        self.round(self.note)
        self.assertEqual(self.calls(), before + 1)
        self.round(self.note)
        self.assertEqual(self.calls(), before + 1, "asked only once")

    def test_dependency_still_open_asks_nothing(self):
        """While P1 is not verified the handled verify of P2 stays quiet."""
        before = self.calls()
        self.round(self.note)
        self.assertEqual(self.calls(), before)


if __name__ == "__main__":
    unittest.main()

"""Tests that the Queen receives plans with dependencies and no separate brain agent."""
import unittest

from bzztest import RoundCase, runtime

PKGS = [{"id": "P1", "title": "first"}, {"id": "P2", "title": "second", "depends": ["P1"]}]


# Verify package dependencies remain available to the Queen.
class DependencyRewakeTest(RoundCase):
    """A Queen handles a plan containing dependent packages."""

    # Prepare a plan with one dependency.
    def setUp(self):
        """Create the dependent plan in the isolated runtime."""
        super().setUp()
        self.plan("IN_PROGRESS", PKGS)

    # Verify the scheduler starts one Queen without a separate decision agent.
    def test_plan_starts_queen_without_brain(self):
        """The Queen starts once and no brain call is made."""
        self.round()
        self.round()
        self.assertEqual(self.calls(), 0)
        active = [a for a in runtime.list_agents(self.conn, "testproj", "all") if a["status"] == "running"]
        self.assertEqual([a["phase"] for a in active], ["queen"])

    # Verify dependencies are retained in the plan data.
    def test_plan_retains_dependencies(self):
        """The dependent package retains its prerequisite for the Queen."""
        packages = {p["id"]: p for p in self.env().packages("docs/plans/dummy/DUMMY-PLAN.md")}
        self.assertEqual(packages["P2"]["depends"], ["P1"])

    # Verify the full plan is supplied even while a dependency is incomplete.
    def test_open_dependency_does_not_start_package_agent(self):
        """The Queen receives the plan rather than a premature package agent."""
        self.round()
        active = [a for a in runtime.list_agents(self.conn, "testproj", "all") if a["status"] == "running"]
        self.assertEqual(len(active), 1)
        self.assertEqual(active[0]["phase"], "queen")
        self.assertEqual(self.calls(), 0)


if __name__ == "__main__":
    unittest.main()

"""Checks of the old PowerShell round, class and usage tests that had no Python counterpart yet."""
import json
import tomllib
import unittest
from unittest import mock

from bzztest import REL, RoundCase, runtime
from haivequeen import config
from haivequeen import scheduler as sch


def pk(i, title, deps="none", cls=""):
    """Package dict for write_plan."""
    return {"id": i, "title": title, "depends": [] if deps == "none" else [deps], "class": cls}


class ParityRoundsTest(RoundCase):
    """Rounds in a temp runtime; no pane, no model."""

    def test_runs_against_own_runtime_only(self):
        """The test runtime override is active and the database lies below the temp folder (guard against live data)."""
        self.assertTrue(str(config.db_path()).startswith(str(self.base)))
        self.assertTrue(str(config.data_dir()).startswith(str(self.base)))

    # Verify packages of one plan share one Queen without automatic work agents.
    def test_multiple_packages_start_one_queen(self):
        """A plan with light and medium packages starts one Queen and no package agent."""
        self.seed()
        self.plan("IN_PROGRESS", [pk("L1", "first small", cls="light"), pk("L2", "second small", cls="light"), pk("L3", "third small", cls="light"),
                                  pk("L4", "fourth small", cls="light"), pk("M1", "medium task", cls="medium"), pk("L5", "needs L1", "L1", "light")])
        self.round()
        rows = runtime.list_agents(self.conn, "testproj", "active")
        self.assertEqual(len([r for r in rows if r["phase"] == "queen" and r["status"] == "running"]), 1)
        self.assertFalse([r for r in rows if r["phase"] == "work" and r["status"] == "running"])
    def test_medium_class_effort(self):
        """The medium class of Claude is Sonnet at effort medium."""
        cm = sch.class_model(self.env().providers, "claude", "medium")
        self.assertEqual((cm["Model"], cm["Effort"]), ("claude-sonnet-5-5", "medium"))

    def test_project_file_valid_and_bad_escape_rejected(self):
        """The written project file loads; a file with a bad escape sequence (single backslash path) is rejected by the parser."""
        self.assertEqual(config.load_project(self.repo)["name"], "testproj")
        bad = self.base / "bad-ws"
        (bad / ".haivequeen").mkdir(parents=True)
        (bad / ".haivequeen" / "project.toml").write_text('name = "dir\\qname"\n', encoding="utf-8")
        with self.assertRaises(tomllib.TOMLDecodeError):
            config.load_project(bad)


if __name__ == "__main__":
    unittest.main()

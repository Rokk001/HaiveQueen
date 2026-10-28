"""Checks of the old PowerShell round, class and usage tests that had no Python counterpart yet."""
import json
import tomllib
import unittest
from unittest import mock

from orchtest import REL, RoundCase, runtime
from orchlib import agent_runner, config
from orchlib import scheduler as sch


def pk(i, title, deps="none", cls=""):
    """Package dict for write_plan."""
    return {"id": i, "title": title, "depends": [] if deps == "none" else [deps], "class": cls}


class ParityRoundsTest(RoundCase):
    """Rounds in a temp runtime; no pane, no model."""

    def test_runs_against_own_runtime_only(self):
        """The test runtime override is active and the database lies below the temp folder (guard against live data)."""
        self.assertTrue(str(config.db_path()).startswith(str(self.base)))
        self.assertTrue(str(config.data_dir()).startswith(str(self.base)))

    def test_bundle_leaves_a_second_agent(self):
        """Next to the bundle of three light packages a second work agent starts: the medium package or the remaining light one."""
        self.seed()
        self.plan("IN_PROGRESS", [pk("L1", "first small", cls="light"), pk("L2", "second small", cls="light"), pk("L3", "third small", cls="light"),
                                  pk("L4", "fourth small", cls="light"), pk("M1", "medium task", cls="medium"), pk("L5", "needs L1", "L1", "light")])
        self.round()
        rows = runtime.list_agents(self.conn, "testproj", "active")
        others = [r for r in rows if r["phase"] == "work" and len(json.loads(r.get("bundle_json") or "[]")) <= 1]
        self.assertTrue([r for r in others if (r["package"] == "M1" and r["class"] == "medium") or (r["package"] == "L4" and r["class"] == "light")],
                        [(r["package"], r["class"]) for r in others])

    def test_medium_class_effort(self):
        """The medium class of Claude is Sonnet at effort medium."""
        cm = sch.class_model(self.env().providers, "claude", "medium")
        self.assertEqual((cm["Model"], cm["Effort"]), ("claude-sonnet-5-5", "medium"))

    def test_pane_start_error_waits_and_exits_zero(self):
        """A pane wrapper whose settings cannot be read prints the error, waits 30 s before closing and still exits 0."""
        args = mock.Mock(project="testproj", pane_name="x")
        with mock.patch.object(config, "load_config", side_effect=ValueError("bad settings")), \
                mock.patch.object(agent_runner.time, "sleep") as sleep, mock.patch("builtins.print") as out:
            code = agent_runner.pane_wrap(args, [])
        self.assertEqual(code, 0)
        sleep.assert_called_once_with(30)
        printed = " ".join(str(c.args[0]) for c in out.call_args_list)
        self.assertIn("pane start error", printed)

    def test_project_file_valid_and_bad_escape_rejected(self):
        """The written project file loads; a file with a bad escape sequence (single backslash path) is rejected by the parser."""
        self.assertEqual(config.load_project(self.repo)["name"], "testproj")
        bad = self.base / "bad-ws"
        (bad / ".orchestrator").mkdir(parents=True)
        (bad / ".orchestrator" / "project.toml").write_text('name = "dir\\qname"\n', encoding="utf-8")
        with self.assertRaises(tomllib.TOMLDecodeError):
            config.load_project(bad)


if __name__ == "__main__":
    unittest.main()

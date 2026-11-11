"""Tests of the fixes after the first live look at V2: plain female Queen names without a number, the Queen order, one build job and the end rules."""
import os
import re
import tempfile
import unittest
from pathlib import Path

from bzztest import RoundCase
from haivequeen import db, hives, loop, runtime

class QueenNameTest(RoundCase):
    """Item 4: a Queen carries a plain female name that no running agent uses, never a number suffix."""

    def test_names_of_old_agents_are_reused_without_a_number(self):
        """Every list name was an agent of the project long ago: the new Queen still shows a plain name, her row alone carries the suffix."""
        for n in loop.AGENT_NAMES:
            self.agent(n, phase="work", status="done", person=n, started=3000, ended=2900)
        self.plan("READY", [{"id": "P1", "title": "First"}])
        self.round()
        queens = [a for a in runtime.list_agents(self.conn, "testproj", "all") if a["phase"] == "queen"]
        self.assertEqual(len(queens), 1)
        self.assertIn(queens[0]["person"], loop.AGENT_NAMES)
        self.assertNotRegex(queens[0]["person"], r"-\d")
        events = [e["text"] for e in db.rows(self.conn, "SELECT text FROM events WHERE text LIKE '%started%'")]
        self.assertTrue(any(queens[0]["person"] + " started" in e for e in events), events)
        self.assertFalse(any(re.search(r"-\d+ started", e) for e in events), events)
        prompt = (self.env().run_dir(queens[0]["name"]) / "prompt.md").read_text(encoding="utf-8")
        self.assertIn("--name %s " % queens[0]["name"], prompt)   # the Queen updates her own row, so the prompt names the row key

    def test_the_list_is_long_enough_for_many_running_agents(self):
        """The list of female names is longer than the number of agents that can run at once, and has no duplicates."""
        self.assertGreaterEqual(len(loop.AGENT_NAMES), 40)
        self.assertEqual(len(set(loop.AGENT_NAMES)), len(loop.AGENT_NAMES))

    def test_no_running_agent_shares_a_name(self):
        """When every list name but one is held by a running agent, the new agent gets the free one."""
        free = loop.AGENT_NAMES[-1]
        for n in loop.AGENT_NAMES[:-1]:
            self.agent(n, phase="work", status="running", person=n, started=5)
        self.assertEqual(loop.agent_identity(self.conn)["Person"], free)


class QueenOrderTest(RoundCase):
    """The Queen order runs ready before acquiring the integration place: the release worktree may only be recreated by the Queen who holds the place."""

    def test_the_queen_prompt_ends_at_ready(self):
        """O-1: the Queen is told to call bzz ready and end; no integration step follows."""
        self.machine["compileCheck"] = "npm test"
        self.plan("READY", [{"id": "P1", "title": "First"}])
        self.round()
        queen = next(a for a in runtime.list_agents(self.conn, "testproj", "all") if a["phase"] == "queen")
        text = (self.env().run_dir(queen["name"]) / "prompt.md").read_text(encoding="utf-8")
        self.assertIn("bzz ready", text)
        for mark in ("bzz compile-check --project", "bzz integration acquire --project", "release worktree", "bzz integration release --project"):
            self.assertNotIn(mark, text)


class PlannedBuildJobsTest(unittest.TestCase):
    """The job count that bzz integration acquire hands out is the computed one (cores, RAM, measurements), never a fixed 1."""

    def test_the_planned_build_gets_the_computed_count(self):
        """plan_build follows the core selection and the RAM limit: more cores and RAM give more jobs than one."""
        from haivequeen import buildjobs

        with tempfile.TemporaryDirectory() as tmp:
            conn = db.connect(Path(tmp) / "db")

            class Env:
                """Just enough of a project environment for plan_build."""
                name = "ODP"
                settings = {"buildJobs": {"envVar": "BUILD_MP_JOBS", "perJobMB": 300, "cores": 8}}

            Env.conn = conn
            for cores, free, jobs in ((12, 32000, 8), (6, 4096, 6), (2, 100000, 2), (12, 1500, 1)):
                plan = buildjobs.plan_build(Env, free_mb=free, cores=cores)
                self.assertEqual(plan["Jobs"], jobs, (cores, free))
                self.assertEqual(plan["EnvVar"], "BUILD_MP_JOBS")
                self.assertEqual(plan["Environment"]["BUILD_MP_JOBS"], str(jobs))
            conn.close()


class QueenEndRuleTest(unittest.TestCase):
    """A Queen ends only when bzz ready was accepted, only owner questions remain, or the Beekeeper asked for a handoff."""

    def test_the_queen_order_names_the_three_ways_to_end(self):
        """The Queen instructions say that she works through all open packages in one run and list the only three reasons to end."""
        from haivequeen import bees
        text = bees.queen_instructions("medium", 3, 3, 300000)
        self.assertIn("You end only when", text)
        for reason in ("bzz ready", "WAITING_FOR_OWNER", "HANDOFF"):   # O-1: the Queen ends at an accepted ready
            self.assertIn(reason, text[text.index("You end only when"):])
        self.assertIn("never end after an intermediate step", text)
        self.assertIn("wins over any rule", text[text.index("never end after an intermediate step"):])


class EndedWithoutWorkTest(unittest.TestCase):
    """K2 looks for new commits in the repository of the Queen, in every worktree and branch, not only in her start folder."""

    def git(self, cwd, *args, date=None):
        """Run git in `cwd` with a fixed identity and an optional commit date."""
        env = dict(os.environ, GIT_AUTHOR_NAME="t", GIT_AUTHOR_EMAIL="t@example.com", GIT_COMMITTER_NAME="t", GIT_COMMITTER_EMAIL="t@example.com")
        if date:
            env["GIT_COMMITTER_DATE"] = env["GIT_AUTHOR_DATE"] = date
        import subprocess
        subprocess.run(["git", *args], cwd=cwd, env=env, check=True, capture_output=True)

    def test_a_commit_in_another_worktree_counts_as_work(self):
        """The Queen started in the plan worktree; her commit in a second worktree of the same repository means she did work."""
        with tempfile.TemporaryDirectory() as tmp:
            main, other = Path(tmp) / "main", Path(tmp) / "other"
            main.mkdir()
            self.git(main, "init", "-q")
            (main / "a.txt").write_text("a", encoding="utf-8")
            self.git(main, "add", "a.txt")
            self.git(main, "commit", "-q", "-m", "old", date="2020-01-01T00:00:00")
            self.git(main, "worktree", "add", "-q", "-b", "work3", str(other))
            (other / "b.txt").write_text("b", encoding="utf-8")
            self.git(other, "add", "b.txt")
            self.git(other, "commit", "-q", "-m", "new work")
            run = Path(tmp) / "run"
            run.mkdir()

            class Env:
                """Just enough of an environment: the run folder of an agent."""
                def run_dir(self, name):
                    return run

            agent = {"name": "Ada", "cwd": str(main), "started_at": "2021-01-01T00:00:00"}
            self.assertFalse(hives.ended_without_work(Env(), agent))
            from datetime import datetime, timedelta
            tomorrow = (datetime.now() + timedelta(days=1)).isoformat(timespec="seconds")
            self.assertTrue(hives.ended_without_work(Env(), dict(agent, started_at=tomorrow)))   # nothing is newer than tomorrow


if __name__ == "__main__":
    unittest.main()



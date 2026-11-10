"""Package B1 of HQ-PLAN-BRANCH: the plan header carries `branch` and `worktree` (B-1 to B-6): written at the cut, checked by bzz plan check, the only source."""
import contextlib
import io
import subprocess
import unittest
from pathlib import Path
from unittest import mock

from bzztest import REL, RoundCase, runtime
from haivequeen import bees, cli, hives, plans, roles


def plan_of(branch="", worktree=""):
    """A cut plan with one package and the given header fields."""
    return plans.Plan(path="x.md", status="SENT", packages=[plans.Package(id="P1", title="First")], branch=branch, worktree=worktree)


class HeaderCheckTests(unittest.TestCase):
    """B-2: plans.check_plan names a missing field and the main checkout."""

    def test_header_fields_are_parsed(self):
        parsed = plans.parse_plan_text('+++\nstatus = "SENT"\nbranch = "fix/x"\nworktree = "C:\\\\w\\\\x"\n+++\n# T\n')
        self.assertEqual((parsed.branch, parsed.worktree), ("fix/x", "C:\\w\\x"))

    def test_missing_fields_are_named(self):
        text = " ".join(plans.check_plan(plan_of(), set(), main_checkout="/main"))
        self.assertIn("branch", text)
        self.assertIn("worktree", text)
        self.assertIn("worktree", " ".join(plans.check_plan(plan_of(branch="fix/x"), set(), main_checkout="/main")))
        self.assertNotIn("branch", " ".join(plans.check_plan(plan_of(branch="fix/x"), set(), main_checkout="/main")))

    def test_main_checkout_is_refused(self):
        text = " ".join(plans.check_plan(plan_of("fix/x", "/main"), set(), main_checkout="/main"))
        self.assertIn("main checkout", text)

    def test_complete_header_passes(self):
        self.assertEqual(plans.check_plan(plan_of("fix/x", "/other"), set(), main_checkout="/main"), [])


class GitCase(RoundCase):
    """A real throw-away git repository as the code repo (the workspace of the dummy project)."""

    def setUp(self):
        super().setUp()
        self.git("init", "-b", "main")
        (self.repo / "tracked.txt").write_text("x", encoding="utf-8")
        self.git("add", "tracked.txt")
        self.commit("initial")
        self.git("remote", "add", "origin", str(self.repo))
        self.git("fetch", "origin", "main")
        self.log_lines = []

    def git(self, *args, cwd=None):
        return subprocess.run(["git", "-C", str(cwd or self.repo), *args], check=True, capture_output=True, text=True, timeout=30).stdout.strip()

    def commit(self, message, cwd=None):
        self.git("-c", "user.name=T", "-c", "user.email=t@example.invalid", "commit", "-q", "-m", message, "--allow-empty", cwd=cwd)

    def header(self):
        return plans.load_plan(str(self.repo / REL))

    def hq_env(self):
        env = self.env()
        env.settings["integrationBranch"] = "main"
        return env

    def sent(self, **kw):
        self.plan("SENT", [{"id": "P1", "title": "First"}], **kw)


class HeaderIsTheOnlySourceTests(GitCase):
    """B-3: the plan branch and worktree come from the two header fields only."""

    def test_header_wins_over_unknown_text_wording(self):
        self.plan("QUEUED", [{"id": "P1", "title": "First"}], branch="fix/x", worktree=str(self.base / "wt-x"))
        with (self.repo / REL).open("a", encoding="utf-8") as f:
            f.write("\nPlan branch `other/y`, worktree `%s`.\n- Feature branch: z\n" % (self.base / "elsewhere"))
        self.assertEqual(bees.plan_worktree(self.hq_env(), REL), ("fix/x", self.base / "wt-x"))

    def test_no_fields_no_guess(self):
        self.plan("QUEUED", [{"id": "P1", "title": "First"}])
        with (self.repo / REL).open("a", encoding="utf-8") as f:
            f.write("\n- Feature branch: from-text\n")
        (self.repo / "docs/plans/dummy/DUMMY-STATE.md").write_text("Plan: DUMMY-PLAN.md; branch from-state; baseline x\n", encoding="utf-8")
        self.assertIsNone(bees.plan_worktree(self.hq_env(), REL))   # neither the text, nor the state file, nor the file name

    def test_plan_starts_on_the_header_branch(self):
        target = self.base / "wt-x"
        self.plan("QUEUED", [{"id": "P1", "title": "First"}], branch="fix/x", worktree=str(target))
        with (self.repo / REL).open("a", encoding="utf-8") as f:
            f.write("\nBranch is somewhere called a flurb.\n")
        self.assertEqual(bees.prepare_queen_worktree(self.hq_env(), REL), str(target))
        self.assertEqual(self.git("branch", "--show-current", cwd=target), "fix/x")

    def test_owner_takeover_writes_the_header(self):
        from haivequeen import db
        self.git("branch", "fix/x")
        self.plan("QUEUED", [{"id": "P1", "title": "First"}], branch="fix/x", worktree=str(self.base / "wt-x"))
        qid = db.add_question(self.conn, "testproj", REL, bees.BRANCH_ITEM, "q", "")
        db.answer_question(self.conn, qid, "take over", "owner")
        self.assertEqual(bees.resolve_existing_branch(self.hq_env(), REL), "take over")
        self.assertEqual((self.header().branch, self.header().worktree), ("fix/x", str(self.base / "wt-x")))

    def test_owner_new_branch_writes_the_header(self):
        from haivequeen import db
        self.git("branch", "fix/x")
        self.plan("QUEUED", [{"id": "P1", "title": "First"}], branch="fix/x", worktree=str(self.base / "wt-x"))
        qid = db.add_question(self.conn, "testproj", REL, bees.BRANCH_ITEM, "q", "")
        db.answer_question(self.conn, qid, "new", "owner")
        self.assertEqual(bees.resolve_existing_branch(self.hq_env(), REL), "new")
        self.assertEqual(self.header().branch, "fix/x-2")
        self.assertTrue(self.header().worktree.endswith("fix-x-2"), self.header().worktree)

    def test_existing_header_branch_without_history_asks_the_owner(self):
        self.git("branch", "fix/x")
        self.plan("QUEUED", [{"id": "P1", "title": "First"}], branch="fix/x", worktree=str(self.base / "wt-x"))
        self.assertTrue(bees.foreign_plan_branch(self.hq_env(), REL))


class ConflictTests(GitCase):
    """B-4: a folder with another branch, or the branch checked out elsewhere, blocks the start with one line and changes nothing."""

    def test_folder_holds_another_branch(self):
        folder = self.base / "wt-x"
        self.git("worktree", "add", "-b", "other", str(folder), "main")
        self.plan("QUEUED", [{"id": "P1", "title": "First"}], branch="fix/x", worktree=str(folder))
        line = bees.worktree_conflict(self.hq_env(), REL)
        self.assertIn(str(folder), line)
        self.assertIn("other", line)
        self.assertIn("fix/x", line)
        self.assertEqual(line.count("\n"), 0)
        self.assertIsNone(bees.prepare_queen_worktree(self.hq_env(), REL))
        self.assertEqual(self.git("branch", "--show-current", cwd=folder), "other")
        self.assertEqual(self.git("branch", "--list", "fix/x"), "")

    def test_branch_checked_out_in_another_folder(self):
        elsewhere = self.base / "wt-else"
        self.git("worktree", "add", "-b", "fix/x", str(elsewhere), "main")
        self.plan("QUEUED", [{"id": "P1", "title": "First"}], branch="fix/x", worktree=str(self.base / "wt-x"))
        line = bees.worktree_conflict(self.hq_env(), REL)
        self.assertIn(str(elsewhere), line)
        self.assertIn("fix/x", line)
        self.assertIsNone(bees.prepare_queen_worktree(self.hq_env(), REL))
        self.assertFalse((self.base / "wt-x").exists())

    def test_no_conflict_when_header_matches(self):
        folder = self.base / "wt-x"
        self.git("worktree", "add", "-b", "fix/x", str(folder), "main")
        self.plan("QUEUED", [{"id": "P1", "title": "First"}], branch="fix/x", worktree=str(folder))
        self.assertEqual(bees.worktree_conflict(self.hq_env(), REL), "")
        self.assertEqual(bees.prepare_queen_worktree(self.hq_env(), REL), str(folder))

    def test_round_logs_the_line_and_starts_no_queen(self):
        folder = self.base / "wt-x"
        self.git("worktree", "add", "-b", "other", str(folder), "main")
        self.plan("QUEUED", [{"id": "P1", "title": "First"}], branch="fix/x", worktree=str(folder))
        from haivequeen import loop
        loop.run_round(self.conn, self.machine, self.envs(), free_ram=lambda: 16.0, settle=0)
        self.assertFalse(any(a["phase"] == "queen" for a in runtime.list_agents(self.conn, "testproj", "all")))
        self.assertIn("other", self.log())
        self.assertIn(str(folder), self.log())


class CutTests(GitCase):
    """B-1 and B-5: the cut text, the default rule and the end of the cut Queen."""

    def test_cut_text_names_the_fields_and_the_default(self):
        text = roles.role_text("queen-cut", {"project": "p", "plan": REL, "branch": "dummy", "worktree": "/w/wt-dummy"})
        self.assertIn("branch =", text)
        self.assertIn("worktree =", text)
        self.assertIn("/w/wt-dummy", text)
        self.assertIn("handoff", text)

    def test_default_rule_is_the_lowercase_file_name_with_the_project_folder(self):
        branch, worktree = bees.cut_default(self.hq_env(), REL)
        self.assertEqual(branch, "dummy")
        self.assertEqual(Path(worktree).name, "wt-dummy")

    def test_owner_choice_before_the_cut_wins(self):
        runtime.kv_set(self.conn, "testproj", bees.NEW_BRANCH_KEY + REL, "dummy-2")
        self.assertEqual(bees.cut_default(self.hq_env(), REL)[0], "dummy-2")

    def test_cut_queen_starts_in_the_default_worktree(self):
        self.sent()
        folder = bees.prepare_queen_worktree(self.hq_env(), REL)
        self.assertEqual(Path(folder), Path(bees.cut_default(self.hq_env(), REL)[1]))
        self.assertEqual(self.git("branch", "--show-current", cwd=folder), "dummy")

    def check(self):
        args = cli.build_parser().parse_args(["plan", "check", "--project", "testproj", "--plan", REL])
        output = io.StringIO()
        with mock.patch.dict("os.environ", {"BZZ_AGENT": "queen"}), mock.patch.object(cli.config, "load_config", return_value=self.machine), contextlib.redirect_stdout(output):
            code = cli.cmd_plan(args, self.conn)
        return code, output.getvalue()

    def queen(self):
        hives.create(self.conn, "testproj", REL, "claude", "123456")
        self.agent("queen", phase="queen", status="running", provider="claude", pid=42)

    def test_check_refuses_a_missing_field_and_the_plan_stays_sent(self):
        self.sent()
        self.queen()
        code, output = self.check()
        self.assertNotEqual(code, 0)
        self.assertIn("branch", output)
        self.assertEqual(self.header().status, "SENT")

    def test_check_refuses_the_main_checkout(self):
        self.sent(branch="fix/x", worktree=str(self.repo))
        self.queen()
        code, output = self.check()
        self.assertNotEqual(code, 0)
        self.assertIn("main checkout", output)
        self.assertEqual(self.header().status, "SENT")

    def test_equal_header_lets_the_queen_go_on(self):
        default = bees.cut_default(self.hq_env(), REL)
        self.sent(branch=default[0], worktree=str(default[1]))
        self.queen()
        code, output = self.check()
        self.assertEqual(code, 0, output)
        self.assertIn('"handoff": false', output)

    def test_different_header_ends_the_cut_queen_with_a_handoff(self):
        self.sent(branch="fix/x", worktree=str(self.base / "wt-x"))
        self.queen()
        code, output = self.check()
        self.assertEqual(code, 0, output)
        self.assertIn('"handoff": true', output)

    def test_unused_default_worktree_and_branch_are_removed(self):
        self.sent(branch="fix/x", worktree=str(self.base / "wt-x"))
        default = bees.cut_default(self.hq_env(), REL)
        self.git("worktree", "add", "-b", default[0], str(default[1]), "main")
        bees.cleanup_cut_default(self.hq_env(), REL)
        self.assertFalse(Path(default[1]).exists())
        self.assertEqual(self.git("branch", "--list", default[0]), "")

    def test_used_default_worktree_stays_and_is_named(self):
        self.sent(branch="fix/x", worktree=str(self.base / "wt-x"))
        default = bees.cut_default(self.hq_env(), REL)
        self.git("worktree", "add", "-b", default[0], str(default[1]), "main")
        self.commit("own work", cwd=default[1])
        lines = []
        env = self.hq_env()
        env.log = lines.append
        bees.cleanup_cut_default(env, REL)
        self.assertTrue(Path(default[1]).exists())
        self.assertEqual(self.git("branch", "--list", default[0]).strip("*+ "), default[0])
        self.assertTrue(any(default[0] in line for line in lines))

    def test_dirty_default_worktree_stays(self):
        self.sent(branch="fix/x", worktree=str(self.base / "wt-x"))
        default = bees.cut_default(self.hq_env(), REL)
        self.git("worktree", "add", "-b", default[0], str(default[1]), "main")
        (Path(default[1]) / "new.txt").write_text("x", encoding="utf-8")
        bees.cleanup_cut_default(self.hq_env(), REL)
        self.assertTrue(Path(default[1]).exists())


class MigrationTests(GitCase):
    """B-6: plans past the cut get the fields at the first round, with one log line each."""

    def test_last_queen_folder_gives_the_fields(self):
        folder = self.base / "wt-q"
        self.git("worktree", "add", "-b", "fix/q", str(folder), "main")
        self.plan("RUNNING", [{"id": "P1", "title": "First"}])
        self.agent("old-queen", phase="queen", cwd=str(folder))
        lines = []
        env = self.hq_env()
        env.log = lines.append
        bees.migrate_headers(env)
        self.assertEqual((self.header().branch, Path(self.header().worktree)), ("fix/q", folder))
        self.assertEqual(sum("branch and worktree written to the header: fix/q, " in line for line in lines), 1)

    def test_without_history_today_search_is_used_once(self):
        self.plan("QUEUED", [{"id": "P1", "title": "First"}])
        with (self.repo / REL).open("a", encoding="utf-8") as f:
            f.write("\n- Feature branch: from-text\n")
        lines = []
        env = self.hq_env()
        env.log = lines.append
        bees.migrate_headers(env)
        self.assertEqual(self.header().branch, "from-text")
        self.assertTrue(self.header().worktree)
        self.assertEqual(sum("branch and worktree written to the header" in line for line in lines), 1)

    def test_gone_folder_falls_back_to_the_search(self):
        self.plan("PAUSED", [{"id": "P1", "title": "First"}])
        self.agent("old-queen", phase="queen", cwd=str(self.base / "gone"))
        bees.migrate_headers(self.hq_env())
        self.assertEqual(self.header().branch, "dummy")

    def test_plan_with_fields_is_untouched_and_not_logged(self):
        self.plan("RUNNING", [{"id": "P1", "title": "First"}], branch="fix/x", worktree=str(self.base / "wt-x"))
        lines = []
        env = self.hq_env()
        env.log = lines.append
        bees.migrate_headers(env)
        self.assertEqual(self.header().branch, "fix/x")
        self.assertEqual(lines, [])

    def test_sent_plan_is_not_migrated(self):
        self.sent()
        bees.migrate_headers(self.hq_env())
        self.assertEqual(self.header().branch, "")

    def test_first_round_migrates(self):
        self.plan("QUEUED", [{"id": "P1", "title": "First"}])
        from haivequeen import loop
        loop.run_round(self.conn, self.machine, self.envs(), free_ram=lambda: 16.0, settle=0)
        self.assertEqual(self.header().branch, "dummy")
        self.assertEqual(self.log().count("branch and worktree written to the header: dummy, "), 1)


if __name__ == "__main__":
    unittest.main()

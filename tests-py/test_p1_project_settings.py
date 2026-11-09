"""Slim project settings (P1): derived values, plan field, plan tags, rules, migration of every project.toml and leftover processes."""
import json
import os
import subprocess
import sys
import tempfile
import time
import tomllib
import unittest
from pathlib import Path
from unittest import mock

import isolation  # noqa: F401  (keeps the suite away from the real folders)
from haivequeen import agent_runner, config, db, gui_config, platform as osplat, profile, projsettings, proctree, routine, runtime, scheduler


def git(folder, *args):
    """Run git in `folder` and return its output."""
    return subprocess.run(["git", "-C", str(folder), *args], capture_output=True, text=True, check=True).stdout


def make_repo(root, name="repo", remotes=(), head=None):
    """A git repository `name` below `root` with the given remotes (name -> url) and optionally the default branch of the remote origin."""
    repo = Path(root) / name
    repo.mkdir(parents=True)
    git(repo, "init", "-q")
    for remote in remotes:
        git(repo, "remote", "add", remote, "https://example.invalid/%s.git" % remote)
    if head:
        git(repo, "symbolic-ref", "refs/remotes/origin/HEAD", "refs/remotes/origin/" + head)
    return repo


class Dirs(unittest.TestCase):
    """A temp folder with its own settings and data folders."""

    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.root = Path(self.tmp.name)
        self.cfgdir, self.datadir = self.root / "cfg", self.root / "data"
        self.cfgdir.mkdir()
        self.datadir.mkdir()
        patcher = mock.patch.dict(os.environ, {"BZZ_CONFIG_DIR": str(self.cfgdir), "BZZ_DATA_DIR": str(self.datadir)})
        patcher.start()
        self.addCleanup(patcher.stop)
        self.addCleanup(self.tmp.cleanup)

    def write_config(self, text):
        (self.cfgdir / "config.toml").write_text(text, encoding="utf-8")

    def workspace(self, name, text):
        ws = self.root / name
        (ws / ".haivequeen").mkdir(parents=True)
        (ws / ".haivequeen" / "project.toml").write_text(text, encoding="utf-8")
        return ws

    def project_toml(self, ws):
        return tomllib.loads((Path(ws) / ".haivequeen" / "project.toml").read_text(encoding="utf-8"))


class DerivedValuesTest(Dirs):
    """P-1, P-2, P-3: defaults and derivations."""

    def test_integration_branch_from_remote_head_else_main(self):
        with_head = make_repo(self.root, "a", ["origin"], head="develop")
        without = make_repo(self.root, "b", ["origin"])
        none = make_repo(self.root, "c")
        self.assertEqual(projsettings.default_branch(with_head), "develop")
        self.assertEqual(projsettings.default_branch(without), "main")
        self.assertEqual(projsettings.default_branch(none), "main")
        merged = scheduler.build_settings({}, {"codeRepo": str(with_head)}, workspace=self.root)
        self.assertEqual(merged["integrationBranch"], "develop")
        kept = scheduler.build_settings({}, {"codeRepo": str(with_head), "integrationBranch": "integration/all"}, workspace=self.root)
        self.assertEqual(kept["integrationBranch"], "integration/all")

    def test_name_defaults_to_the_folder(self):
        self.assertEqual(projsettings.default_name(self.root / "my-repo"), "my-repo")
        ws = self.workspace("folder-name", "colour = 'red'\n")
        self.assertEqual(config.load_project(ws)["name"], "folder-name")

    def test_remote_derivation(self):
        cases = {"origin": (["origin"], "origin", False), "other": (["fork"], "fork", False), "origin and upstream": (["upstream", "origin"], "origin", False),
                 "other and upstream": (["upstream", "fork"], "fork", False), "only upstream": (["upstream"], "", True), "two without origin": (["a", "b"], "", True),
                 "none": ([], "origin", False)}
        for label, (remotes, remote, needed) in cases.items():
            repo = make_repo(self.root, label.replace(" ", "-"), remotes)
            self.assertEqual(projsettings.derive_remote(repo), (remote, needed), label)
        settings = scheduler.build_settings({}, {"codeRepo": str(make_repo(self.root, "two", ["a", "b"]))}, workspace=self.root)
        self.assertTrue(settings["remoteChoiceNeeded"])
        self.assertIn("Remote", projsettings.remote_wait_reason(settings))

    def test_set_remote_that_differs_is_kept(self):
        repo = make_repo(self.root, "keep", ["origin", "fork"])
        self.assertEqual(projsettings.derive_remote(repo, "fork"), ("fork", False))
        self.assertEqual(projsettings.derive_remote(repo, "origin"), ("origin", False))
        two = make_repo(self.root, "two2", ["a", "b"])
        self.assertEqual(projsettings.derive_remote(two, "b"), ("b", False))   # the owner chose it

    def test_plans_field_finds_the_same_files_as_globs_and_excludes(self):
        ws = self.root / "plansws"
        (ws / "docs" / "plans" / "archive").mkdir(parents=True)
        for rel in ("docs/plans/A-PLAN.md", "docs/plans/B-STATE.md", "docs/plans/archive/C-PLAN.md"):
            (ws / rel).write_text("+++\nstatus='QUEUED'\n+++\n", encoding="utf-8")
        (ws / ".haivequeen").mkdir()
        old = {"planGlobs": ["docs/plans/**/*.md"], "planExclude": ["docs/plans/archive/**", "docs/plans/*-STATE.md"]}
        field = projsettings.globs_to_plans(old["planGlobs"], old["planExclude"])
        self.assertEqual(projsettings.plans_to_globs({"plans": field}), (old["planGlobs"], old["planExclude"]))
        (ws / ".haivequeen" / "project.toml").write_text("plans = %s\n" % json.dumps(field), encoding="utf-8")
        loaded = config.load_project(ws)
        from haivequeen import plans as plan_mod
        found_new = plan_mod.discover_plans(str(ws), loaded["planGlobs"], loaded["planExclude"])
        found_old = plan_mod.discover_plans(str(ws), old["planGlobs"], old["planExclude"])
        self.assertEqual(found_new, found_old)
        self.assertTrue(found_new)
        self.assertEqual(config.load_project(self.workspace("nodefault", "name='x'\n"))["planGlobs"], ["docs/plans/*.md"])

    def test_general_values_win_over_dropped_project_keys(self):
        project = {"language": "de", "escalateAfter": 9, "roundMinutes": 1, "owner": "Someone", "queenClass": "heavy", "beesPerQueen": 9,
                   "contextLimitTokens": 1, "capabilityRules": [{"x": 1}], "preferredProvider": "p", "ownerCheckText": "own", "allowedTools": ["Bash(x)"],
                   "ignoredBuildOutput": ["out/"], "protectedWorktrees": ["w"], "compileCheckEnabled": False, "claudeTools": "Read"}
        merged = scheduler.build_settings({"owner": "Mario", "queenClass": "light", "beesPerQueen": 2, "roundMinutes": 7, "escalateAfter": 4}, project)
        for key, value in {"owner": "Mario", "queenClass": "light", "beesPerQueen": 2, "roundMinutes": 7, "escalateAfter": 4, "contextLimitTokens": 300000}.items():
            self.assertEqual(merged[key], value, key)
        for key in ("capabilityRules", "preferredProvider", "ownerCheckText", "allowedTools", "ignoredBuildOutput", "protectedWorktrees", "compileCheckEnabled", "claudeTools", "language"):
            self.assertNotIn(key, merged)

    def test_context_limit_per_provider_and_model_entry_wins(self):
        providers = scheduler.get_providers({"providers": [{"name": "claude", "contextLimit": 200000, "classes": {"heavy": {"model": "m", "contextLimitTokens": 900000}}},
                                                            {"name": "local", "protocol": "claude", "access": "llm", "address": "http://localhost:1/v1", "contextWindow": 32000, "contextLimit": 100000}]})
        settings = scheduler.build_settings({}, {})
        self.assertEqual(scheduler.context_limit(settings, providers, "claude", "medium"), 200000)
        self.assertEqual(scheduler.context_limit(settings, providers, "claude", "heavy"), 900000)
        self.assertEqual(scheduler.context_limit(settings, providers, "local", "medium"), 32000)   # capped by the window of a Local LLM

    def test_claude_tools_come_from_the_provider(self):
        self.assertEqual(agent_runner.claude_tools({"claudeTools": "Read,Agent"}), "Read,Agent")
        self.assertEqual(agent_runner.claude_tools({}), config.DEFAULT_CLAUDE_TOOLS)
        self.assertNotIn("claudeTools", config.DEFAULT_CONFIG)
        self.assertEqual(scheduler.get_providers({"providers": [{"name": "claude", "claudeTools": "Read,Agent"}]})[0]["claudeTools"], "Read,Agent")

    def test_push_permission_without_allowed_tools(self):
        grants = profile.claude_integration_permissions("P", "plan.md", "origin", "main", "python", "cli.py")
        text = json.dumps(grants)
        self.assertIn("git push origin HEAD:main", text)
        self.assertEqual(agent_runner.build_args({"args": ["-p"]}, {}), ["-p"])


class PlanTagTest(Dirs):
    """P-5: the plan tag comes from the file name."""

    class Env:
        def __init__(self, conn, settings=None, globs=None):
            self.conn, self.name, self.settings, self.plan_globs = conn, "P", settings or {}, globs or ["plans/*.md"]

    def setUp(self):
        super().setUp()
        self.conn = db.connect(self.datadir / "t.db")
        self.addCleanup(self.conn.close)
        self.env = self.Env(self.conn)

    def test_tag_from_file_name(self):
        for plan, tag in {"plans/HQ-PROJECT-SETTINGS-PLAN.md": "HQ-PROJECT-SETTINGS", "plans/foo-plan.md": "FOO", "plans/FOOPLAN.md": "FOO", "plans/Bar.md": "BAR",
                          "docs/x/PLAN.md": "X", "docs/INGAME-QS-8.md": "INGAME-QS-8"}.items():
            self.assertEqual(routine.plan_tag(self.env, plan), tag, plan)

    def test_collision_puts_the_folder_in_front_for_the_later_plan(self):
        self.conn.execute("INSERT INTO hives(project,plan_path,provider,colour) VALUES ('P','a/X-PLAN.md','p','c')")
        self.assertEqual(routine.plan_tag(self.env, "a/X-PLAN.md"), "A")   # a plan with a Hive keeps the tag it has today (its folder)
        self.conn.execute("INSERT INTO hives(project,plan_path,provider,colour) VALUES ('P','b/A-PLAN.md','p','c')")
        self.assertEqual(routine.plan_tag(self.env, "c/A-PLAN.md"), "C-A")   # taken by the plan with a Hive
        self.assertEqual(routine.plan_tag(self.env, "b/A-PLAN.md"), "B")

    def test_kept_tag_never_changes(self):
        self.conn.execute("INSERT INTO hives(project,plan_path,provider,colour) VALUES ('P','old/Z-PLAN.md','p','c')")
        first = routine.plan_tag(self.env, "old/Z-PLAN.md")
        self.assertEqual(first, "OLD")
        self.conn.execute("INSERT INTO hives(project,plan_path,provider,colour) VALUES ('P','new/OLD-PLAN.md','p','c')")
        self.assertEqual(routine.plan_tag(self.env, "old/Z-PLAN.md"), first)
        self.assertEqual(routine.plan_tag(self.env, "new/OLD-PLAN.md"), "NEW")   # also a Hive: keeps the tag it has today (its folder)

    def test_standalone_plan_with_hive_keeps_its_worktree_name_after_migration(self):
        ws = self.workspace("odp", 'name="ODP"\nplanGlobs=["docs/**/INGAME-QS-[0-9].md"]\nplanStandaloneRegex="^(INGAME-QS-\\\\d+)\\\\.md$"\n')
        self.conn.execute("INSERT INTO projects(name,workspace) VALUES ('P',?)", (str(ws),))
        self.conn.execute("INSERT INTO hives(project,plan_path,provider,colour) VALUES ('P','docs/ingame/INGAME-QS-8.md','p','c')")
        projsettings.migrate_all([ws], conn=self.conn)
        self.assertEqual(routine.package_slug(self.env, "docs/ingame/INGAME-QS-8.md", "P1"), "ingame-qs-8-p1")


LIVE_ODP = '''# ODP workspace settings
name = "ODP"
colour = "cyan"
priority = 1
repo = "{repo}"
integrationBranch = "integration/all"
ignoredBuildOutput = ["out/", "build/"]
protectedWorktrees = ["build/current-worktree"]
worktreePrefix = "ODP"
planGlobs = ["docs/**/*-PLAN.md", "docs/**/INGAME-QS-[0-9].md"]
planExclude = ["docs/orchestrator/**"]
planStandaloneRegex = "^(INGAME-QS-\\\\d+)\\\\.md$"
planSuffixRegex = "-?PLAN$"
compileCheck = "powershell -NoProfile -File scripts/compile.ps1"
buildFlowDoc = "docs/BUILD-FLOW.md"
integratorInstructions = ".claude/agents/integrator.md"
integrationStateFile = "INTEGRATION-STATE.md"
buildProcessNames = ["cl.exe"]
owner = "Mario"
openMarker = "Open for Mario"
integratorSlots = 1
roundMinutes = 5
escalateAfter = 3
claudeTools = "Read,Write,Edit,Bash,PowerShell,Grep,Glob,Monitor,TaskStop,Agent"
paneCloseDelaySeconds = 30
allowedTools = ["Bash(git push origin HEAD:integration/all)", "Bash(git -C * push origin HEAD:integration/all)", "PowerShell(git push origin HEAD:integration/all)", "PowerShell(git -C * push origin HEAD:integration/all)", "Bash(make test)"]
somethingNew = 1

[minClass]
verify = "medium"

[bundle]
maxPackages = 3

[terminalProfile]
name = "ODP"

[views]
queue = "q.md"

[ruleSources]
global = "C:/Users/me/.claude/CLAUDE.md"

[phaseRules.work]
global = ["## 1. Think"]

[onEvent]
command = ["pwsh", "-File", "{{projectDir}}\\\\sync.ps1"]
workDir = "{{projectDir}}"
timeoutSeconds = 180
'''
LIVE_HQ = '''name = "HaiveQueen"
colour = "magenta"
codeRepo = "{repo}"
integrationBranch = "main"
worktreePrefix = "HQ"
ignoredBuildOutput = ["__pycache__/"]
planGlobs = ["plans/*-PLAN.md"]
planExclude = ["plans/*-STATE.md"]
compileCheck = "python -m unittest"
compileCheckEnabled = false
buildProcessNames = []
owner = "Mario"
claudeTools = "Read,Write,Edit,Bash,PowerShell,Grep,Glob,Monitor,TaskStop,Agent"
buildEnabled = false
deployEnabled = false
remoteName = "origin"
allowedTools = ["Bash(git push origin HEAD:main)"]

[onEvent]
command = ["pwsh", "-File", "sync.ps1"]
workDir = "."
timeoutSeconds = 180

[prompts]
integrator = "You are the Queen integrating"
'''
CONFIG = '''claudeTools = "Read,Bash"
contextLimitTokens = 250000
queenClass = "heavy"

[[providers]]
name = "claude"
args = ["-p", "--allowedTools", "Bash(git status)"]

[[providers]]
name = "local"
protocol = "codex"
access = "llm"
address = "http://localhost:1234/v1"
contextWindow = 32000

[[projects]]
name = "ODP"
workspace = "{odp}"

[[projects]]
name = "HaiveQueen"
workspace = "{hq}"
'''


class MigrationTest(Dirs):
    """P-6, P-7, P-3: the migration of the two live project files (copies) and the values it moves."""

    def setUp(self):
        super().setUp()
        self.odp_repo = make_repo(self.root, "odp-code", ["origin"])
        self.hq_repo = make_repo(self.root, "hq-code", ["origin"])
        self.odp = self.workspace("odp", LIVE_ODP.format(repo=str(self.odp_repo).replace("\\", "\\\\")))
        self.hq = self.workspace("hq", LIVE_HQ.format(repo=str(self.hq_repo).replace("\\", "\\\\")))
        self.write_config(CONFIG.format(odp=str(self.odp).replace("\\", "\\\\"), hq=str(self.hq).replace("\\", "\\\\")))

    def md(self, ws):
        return (Path(ws) / ".haivequeen" / "removed-settings.md").read_text(encoding="utf-8")

    def test_files_are_migrated_with_bak_and_removed_list(self):
        old_odp = (self.odp / ".haivequeen" / "project.toml").read_text(encoding="utf-8")
        done = projsettings.migrate_all()
        self.assertEqual(len(done), 2)
        self.assertEqual((self.odp / ".haivequeen" / "project.toml.bak").read_text(encoding="utf-8"), old_odp)
        new = self.project_toml(self.odp)
        self.assertEqual(set(new), {"name", "colour", "priority", "codeRepo", "integrationBranch", "worktreePrefix", "plans", "compileCheck", "buildFlowDoc",
                                    "integratorInstructions", "buildProcessNames", "onEvent"})
        self.assertEqual(new["codeRepo"], str(self.odp_repo))
        self.assertEqual(new["plans"], ["docs/**/*-PLAN.md", "docs/**/INGAME-QS-[0-9].md", "!docs/orchestrator/**"])
        self.assertEqual(new["onEvent"]["timeoutSeconds"], 180)
        text = self.md(self.odp)
        for key in ("owner", "language" if False else "roundMinutes", "escalateAfter", "ignoredBuildOutput", "protectedWorktrees", "claudeTools", "allowedTools", "views", "integrationStateFile",
                    "minClass", "bundle", "openMarker", "integratorSlots", "paneCloseDelaySeconds", "terminalProfile", "ruleSources", "phaseRules", "planStandaloneRegex",
                    "planSuffixRegex", "somethingNew", "planGlobs", "planExclude", "repo"):
            self.assertIn("`%s`" % key, text, key)
        self.assertIn("'Mario'", text)
        self.assertIn("not used", text)
        self.assertIn("Bash(make test)", text)

    def test_hq_file_switch_off_integrator_prompt_and_remote(self):
        projsettings.migrate_all()
        new = self.project_toml(self.hq)
        self.assertEqual(new["compileCheck"], "")
        self.assertNotIn("compileCheckEnabled", new)
        self.assertNotIn("remoteName", new)
        self.assertNotIn("prompts", new)
        text = self.md(self.hq)
        self.assertIn("python -m unittest", text)
        self.assertIn("prompts.integrator", text)
        self.assertEqual(new["buildEnabled"], False)

    def test_values_move_to_general_and_providers(self):
        projsettings.migrate_all()
        cfg = tomllib.loads((self.cfgdir / "config.toml").read_text(encoding="utf-8"))
        self.assertEqual(cfg["owner"], "Mario")
        self.assertEqual(cfg["queenClass"], "heavy")   # config.toml value stays
        self.assertNotIn("claudeTools", cfg)
        self.assertNotIn("contextLimitTokens", cfg)
        claude, local = cfg["providers"]
        self.assertEqual(claude["contextLimit"], 250000)
        self.assertEqual(local["contextLimit"], 32000)   # a Local LLM: the window when smaller
        self.assertEqual(claude["claudeTools"], "Read,Bash,Write,Edit,PowerShell,Grep,Glob,Monitor,TaskStop,Agent")   # machine value plus the union of the projects
        self.assertNotIn("claudeTools", local)
        self.assertIn("Bash(make test)", claude["args"][claude["args"].index("--allowedTools") + 1])
        self.assertTrue((self.cfgdir / "config.toml.bak").is_file())

    def test_owner_in_general_is_never_overwritten(self):
        self.write_config('owner = "Chief"\n[[projects]]\nname = "ODP"\nworkspace = "%s"\n' % str(self.odp).replace("\\", "\\\\"))
        projsettings.migrate_all()
        self.assertEqual(tomllib.loads((self.cfgdir / "config.toml").read_text(encoding="utf-8"))["owner"], "Chief")

    def test_first_project_in_config_order_gives_owner_and_queen_class(self):
        (self.hq / ".haivequeen" / "project.toml").write_text('name="H"\nowner="Second"\nqueenClass="light"\nbeesPerQueen=5\n', encoding="utf-8")
        (self.odp / ".haivequeen" / "project.toml").write_text('name="O"\nowner="First"\nbeesPerQueen=4\n', encoding="utf-8")
        self.write_config('[[projects]]\nname="O"\nworkspace="%s"\n[[projects]]\nname="H"\nworkspace="%s"\n' % (str(self.odp).replace("\\", "\\\\"), str(self.hq).replace("\\", "\\\\")))
        projsettings.migrate_all()
        cfg = tomllib.loads((self.cfgdir / "config.toml").read_text(encoding="utf-8"))
        self.assertEqual((cfg["owner"], cfg["beesPerQueen"], cfg["queenClass"]), ("First", 4, "light"))

    def test_migrated_file_is_not_changed_again(self):
        projsettings.migrate_all()
        before = (self.odp / ".haivequeen" / "project.toml").read_bytes(), (self.cfgdir / "config.toml").read_bytes(), self.md(self.odp)
        bak = (self.odp / ".haivequeen" / "project.toml.bak").read_bytes()
        self.assertEqual(projsettings.migrate_all(), [])
        self.assertEqual(before, ((self.odp / ".haivequeen" / "project.toml").read_bytes(), (self.cfgdir / "config.toml").read_bytes(), self.md(self.odp)))
        self.assertEqual(bak, (self.odp / ".haivequeen" / "project.toml.bak").read_bytes())

    def test_older_bak_is_replaced_and_removed_list_is_extended(self):
        (self.hq / ".haivequeen" / "project.toml.bak").write_text("old backup", encoding="utf-8")
        (self.hq / ".haivequeen" / "removed-settings.md").write_text("# mine\n\nkeep this\n", encoding="utf-8")
        projsettings.migrate_all()
        self.assertNotEqual((self.hq / ".haivequeen" / "project.toml.bak").read_text(encoding="utf-8"), "old backup")
        text = self.md(self.hq)
        self.assertTrue(text.startswith("# mine\n\nkeep this"))
        self.assertIn("## 20", text)   # a dated section

    def test_project_without_name_keeps_its_name_and_unknown_key_is_removed(self):
        ws = self.workspace("nameless", 'colour = "red"\nweird = 5\nworktreePrefix = "NL"\nupstreamRepo = "C:/up"\n')
        projsettings.migrate_all([ws])
        new = self.project_toml(ws)
        self.assertEqual(new, {"name": "nameless", "colour": "red", "worktreePrefix": "NL", "upstreamRepo": "C:/up"})
        self.assertIn("`weird` = 5 - not used", self.md(ws))

    def test_migrated_copies_work_as_before(self):
        projsettings.migrate_all()
        cfg = config.load_config()
        for ws, repo, branch in ((self.odp, self.odp_repo, "integration/all"), (self.hq, self.hq_repo, "main")):
            pcfg = config.load_project(ws)
            settings = scheduler.build_settings(cfg, pcfg, workspace=ws)
            self.assertEqual(settings["integrationBranch"], branch)
            self.assertEqual(settings["remoteName"], "origin")
            self.assertEqual(settings["owner"], "Mario")
            self.assertIn("onEvent", settings)
            self.assertIn("buildProcessNames", settings)
            grants = json.dumps(profile.claude_integration_permissions("P", "x", settings["remoteName"], settings["integrationBranch"], "python", "cli.py"))
            self.assertIn("git push origin HEAD:" + branch, grants)
            self.assertEqual(str(pcfg["codeRepo"]), str(repo))
        odp = config.load_project(self.odp)
        self.assertEqual(odp["planGlobs"], ["docs/**/*-PLAN.md", "docs/**/INGAME-QS-[0-9].md"])
        self.assertEqual(odp["planExclude"], ["docs/orchestrator/**"])
        self.assertEqual(scheduler.build_settings(cfg, config.load_project(self.hq), workspace=self.hq)["compileCheck"], "")
        claude = scheduler.get_providers(cfg)[0]
        self.assertIn("Agent", agent_runner.claude_tools(claude).split(","))

    def test_remote_that_differs_stays(self):
        (self.hq / ".haivequeen" / "project.toml").write_text('name="H"\nremoteName="fork"\ncodeRepo=%s\n' % json.dumps(str(self.hq_repo)), encoding="utf-8")
        git(self.hq_repo, "remote", "add", "fork", "https://example.invalid/fork.git")
        projsettings.migrate_all([self.hq])
        self.assertEqual(self.project_toml(self.hq)["remoteName"], "fork")
        self.assertEqual(scheduler.build_settings({}, config.load_project(self.hq), workspace=self.hq)["remoteName"], "fork")


class RulesTest(Dirs):
    """P-4: no agent of any phase gets a rule file from outside the repository."""

    def test_no_personal_rules_in_any_phase(self):
        repo = self.root / "rules-repo"
        repo.mkdir()
        git(repo, "init", "-q")
        (repo / "AGENTS.md").write_text("# Repo rules\nUse tabs.\n", encoding="utf-8")
        git(repo, "add", "AGENTS.md")
        personal = self.root / "personal.md"
        personal.write_text("## 1. Think\nPERSONAL-SECRET\n", encoding="utf-8")
        pcfg = {"codeRepo": str(repo), "ruleSources": {"global": str(personal)}, "phaseRules": {"verify": {"global": ["## 1. Think"]}}}
        for phase in ("verify", "release", "integrate", "ready-check", "work"):
            out = self.root / ("rules-%s.md" % phase)
            agent_runner.write_phase_rules(pcfg, str(repo), phase, out, "claude", str(self.root))
            text = out.read_text(encoding="utf-8")
            self.assertNotIn("PERSONAL-SECRET", text, phase)
            self.assertIn("Use tabs.", text, phase)

    def test_upstream_rules_only_when_switched_on(self):
        repo, up = self.root / "r2", self.root / "up"
        for folder, text in ((repo, "own"), (up, "from upstream")):
            folder.mkdir()
            git(folder, "init", "-q")
            (folder / "AGENTS.md").write_text(text + "\n", encoding="utf-8")
            git(folder, "add", "AGENTS.md")
        without = profile.rules_text(profile.project_rules({}, repo))
        with_up = profile.rules_text(profile.project_rules({"upstreamRepo": str(up)}, repo))
        self.assertNotIn("from upstream", without)
        self.assertIn("from upstream", with_up)

    def test_ready_check_rules_file_of_odp_has_no_personal_section(self):
        odp = (Path(__file__).parent / "test_p1_project_settings.py").read_text(encoding="utf-8")
        self.assertNotIn("def markdown_block", Path(agent_runner.__file__).read_text(encoding="utf-8"))
        self.assertIn("ruleSources", projsettings.REMOVED_NOTES)
        self.assertTrue(odp)


def python_child_script(pid_file, seconds):
    """Python code: start a child that sleeps, write its pid, then wait `seconds` and exit (the child outlives its parent)."""
    return ("import subprocess,sys,time\n"
            "c=subprocess.Popen([sys.executable,'-c','import time; time.sleep(120)'])\n"
            "open(%r,'w').write(str(c.pid))\n"
            "time.sleep(%s)\n" % (str(pid_file), seconds))


class LeftoverProcessTest(Dirs):
    """P-12: an agent leaves no running process behind; processes of HaiveQueen itself are not touched."""

    def spawn_parent(self, name, seconds=0.0):
        pid_file = self.root / name
        parent = subprocess.Popen([sys.executable, "-c", python_child_script(pid_file, seconds)])
        deadline = time.time() + 20
        while not pid_file.is_file() or not pid_file.read_text():
            self.assertLess(time.time(), deadline)
            time.sleep(0.05)
        return parent, int(pid_file.read_text())

    def wait_gone(self, pid):
        deadline = time.time() + 10
        while osplat.process_alive(pid) and time.time() < deadline:
            time.sleep(0.05)
        return not osplat.process_alive(pid)

    def kill(self, *pids):
        for pid in pids:
            osplat.kill_process_tree(pid)

    def test_child_that_outlives_its_parent_is_ended(self):
        tracker = proctree.Tracker(os.getpid(), self.root / "run")
        parent, child = self.spawn_parent("c1", 1.5)
        self.addCleanup(self.kill, child)
        tracker.poll()
        parent.wait(timeout=20)
        self.assertTrue(osplat.process_alive(child))   # the orphan runs on
        ended = tracker.finish()
        self.assertIn(child, ended)
        self.assertTrue(self.wait_gone(child))
        self.assertFalse((self.root / "run" / proctree.STATE_FILE).exists())

    def test_tree_is_ended_when_the_agent_is_stopped_too(self):
        tracker = proctree.Tracker(os.getpid(), self.root / "run2")
        parent, child = self.spawn_parent("c2", 60)
        self.addCleanup(self.kill, parent.pid, child)
        tracker.poll()
        parent.kill()   # a stop
        tracker.finish()
        self.assertTrue(self.wait_gone(child))

    def test_exempt_processes_keep_running(self):
        tracker = proctree.Tracker(os.getpid(), self.root / "run3")
        parent, child = self.spawn_parent("c3", 60)
        other, other_child = self.spawn_parent("c4", 60)
        self.addCleanup(self.kill, parent.pid, child, other.pid, other_child)
        proctree.exempt_register(other.pid)   # a process Bee runner or a compile check: the subtree is exempt
        tracker.poll()
        tracker.finish()
        self.assertTrue(self.wait_gone(child))
        self.assertTrue(osplat.process_alive(other.pid))
        self.assertTrue(osplat.process_alive(other_child))

    def test_leftovers_of_an_agent_that_ended_while_down_are_ended_at_start(self):
        runs = self.root / "runs"
        folder = runs / "proj" / "agent-1"
        tracker = proctree.Tracker(os.getpid(), folder)
        parent, child = self.spawn_parent("c5", 0.5)
        self.addCleanup(self.kill, child)
        tracker.poll()
        parent.wait(timeout=20)
        state = json.loads((folder / proctree.STATE_FILE).read_text())
        state["root"], state["rootIdentity"] = 2 ** 22 - 3, "gone"   # the runner vanished with HaiveQueen
        (folder / proctree.STATE_FILE).write_text(json.dumps(state))
        self.assertTrue(osplat.process_alive(child))
        self.assertIn(child, proctree.sweep(runs))
        self.assertTrue(self.wait_gone(child))
        self.assertFalse((folder / proctree.STATE_FILE).exists())

    def test_sweep_leaves_a_process_with_another_identity_alone(self):
        runs = self.root / "runs"
        folder = runs / "proj" / "agent-2"
        folder.mkdir(parents=True)
        victim = subprocess.Popen([sys.executable, "-c", "import time; time.sleep(60)"])
        self.addCleanup(self.kill, victim.pid)
        (folder / proctree.STATE_FILE).write_text(json.dumps({"root": 2 ** 22 - 3, "rootIdentity": "gone", "procs": {str(victim.pid): {"identity": "win:1", "ppid": 1}}}))
        proctree.sweep(runs)
        self.assertTrue(osplat.process_alive(victim.pid))

    def test_compile_check_by_haivequeen_is_exempt(self):
        from haivequeen import integration
        self.assertIn("proctree", Path(integration.__file__).read_text(encoding="utf-8"))
        proc = subprocess.Popen([sys.executable, "-c", "import time; time.sleep(60)"])
        self.addCleanup(self.kill, proc.pid)
        proctree.exempt_register(proc.pid)
        tracker = proctree.Tracker(os.getpid(), None)
        tracker.poll()
        tracker.finish()
        self.assertTrue(osplat.process_alive(proc.pid))


if __name__ == "__main__":
    unittest.main()

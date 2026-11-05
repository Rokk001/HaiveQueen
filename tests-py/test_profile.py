"""The HaiveQueen profile: every provider CLI is started with its user files in the profile folder, nothing private is read or copied."""
import gc
import json
import os
import tempfile
import time
import unittest
from pathlib import Path
from unittest import mock

import bzztest  # noqa: F401  (redirects the real folders)
from bzztest import provider
from haivequeen import agent_runner as ar
from haivequeen import db, profile, runtime, usage
from haivequeen import scheduler as sch
from test_agent_runner import FAKE_CLI, RunnerBase, claude_stream, codex_stream

ENV_FAKE_CLI = FAKE_CLI.replace('"bzz_agent": os.environ.get("BZZ_AGENT")', '"bzz_agent": os.environ.get("BZZ_AGENT"), "env": dict(os.environ)')


class ProfileCase(unittest.TestCase):
    """Own data folder, login check switched on (the suite switches it off for all other tests)."""

    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.data = Path(self.tmp.name) / "data"
        patch = mock.patch.dict(os.environ, {"BZZ_DATA_DIR": str(self.data), "BZZ_CONFIG_DIR": str(Path(self.tmp.name) / "cfg")})
        patch.start()
        self.addCleanup(patch.stop)
        saved = os.environ.pop(profile.NO_CHECK_ENV, None)
        if saved is not None:
            self.addCleanup(os.environ.__setitem__, profile.NO_CHECK_ENV, saved)


class EnvironmentTest(ProfileCase):
    def test_every_provider_points_to_the_profile(self):
        """Claude, Codex and OpenCode get their config-dir variables inside <data>/profile, whatever the parent environment says."""
        private = {"CLAUDE_CONFIG_DIR": "/private/c", "CODEX_HOME": "/private/x", "XDG_DATA_HOME": "/private/d", "XDG_CONFIG_HOME": "/private/e",
                   "OPENCODE_CONFIG_DIR": "/private/o"}
        root = self.data / "profile"
        for proto, keys in (("claude", ["CLAUDE_CONFIG_DIR"]), ("codex", ["CODEX_HOME"]),
                            ("opencode", ["OPENCODE_CONFIG_DIR", "XDG_DATA_HOME", "XDG_CONFIG_HOME"])):
            env = profile.environment({"name": proto, "protocol": proto}, private)
            for key in keys:
                self.assertTrue(Path(env[key]).is_relative_to(root), (proto, key, env[key]))
            self.assertNotIn("/private", " ".join(env[k] for k in keys))
        self.assertEqual(profile.environment({"protocol": "claude"}, {})["CLAUDE_CODE_DISABLE_AUTO_MEMORY"], "1")
        self.assertEqual(profile.environment({"protocol": "opencode"}, {})["OPENCODE_DISABLE_CLAUDE_CODE"], "1")

    def test_usage_check_uses_profile(self):
        """The Claude usage check runs with the profile environment, not with the private one."""
        seen = {}

        def fake_run(argv, **kw):
            seen.update(kw["env"])
            raise OSError("stop")
        conn = db.connect(self.data / "haivequeen.db")
        with mock.patch.dict(os.environ, {"CLAUDE_CONFIG_DIR": "/private/c"}), mock.patch("shutil.which", return_value="claude"):
            usage.refresh_claude_usage(conn, [provider("claude", 1, limit="claude")], force=True, run=fake_run)
        conn.close()
        self.assertTrue(seen)
        self.assertEqual(Path(seen["CLAUDE_CONFIG_DIR"]), self.data / "profile" / "claude")

    def test_log_readers_use_profile(self):
        """Session logs and settings for usage and Codex limits are read from the profile, never from the user's home folder."""
        home = Path(self.tmp.name) / "home"
        with mock.patch.dict(os.environ, {"HOME": str(home), "USERPROFILE": str(home)}):
            for path in (usage.codex_root(), ar.codex_sessions_dir(), profile.sessions_dir("codex")):
                self.assertTrue(path.is_relative_to(self.data / "profile"), path)
                self.assertFalse(path.is_relative_to(home))


class ContentTest(ProfileCase):
    def test_claude_integration_permissions_are_restored_on_prepare(self):
        claude = provider("claude", 1)
        self.assertTrue(profile.prepare(claude))
        settings = self.data / "profile/claude/settings.json"
        self.assertFalse(json.loads(settings.read_text(encoding="utf-8")).get("permissions", {}).get("allow"))
        settings.write_text('{}', encoding="utf-8")
        self.assertTrue(profile.prepare(claude))
        restored = json.loads(settings.read_text(encoding="utf-8"))
        self.assertFalse(restored["autoMemoryEnabled"])
        self.assertFalse(restored.get("permissions", {}).get("allow"))

    def test_integration_permissions_are_exact_and_specific_to_run(self):
        rules = profile.claude_integration_permissions("Hive", "plans/ONE-PLAN.md", "upstream", "release/next", "python", "/opt/hq/cli.py")
        for tool in ("Bash", "PowerShell"):
            self.assertIn(f"{tool}(git push upstream HEAD:release/next)", rules)
            self.assertIn(f"{tool}(bzz integration acquire --project Hive --plan plans/ONE-PLAN.md)", rules)
            self.assertIn(f"{tool}('python' -I '/opt/hq/cli.py' integration release --project Hive --plan plans/ONE-PLAN.md)", rules)
        self.assertTrue(all("*" not in rule for rule in rules))
        self.assertFalse(any("Other" in rule or "HEAD:main" in rule for rule in rules))
        bash = {rule[5:-1] for rule in rules if rule.startswith("Bash(")}
        self.assertIn("bzz integration acquire --project Hive --plan plans/ONE-PLAN.md", bash)
        for command in ("bzz integration acquire --project Other --plan plans/ONE-PLAN.md",
                        "bzz integration acquire --project Hive --plan plans/OTHER.md",
                        "git push upstream HEAD:main", "git push upstream HEAD:release/next --force",
                        "python -I /tmp/cli.py integration acquire --project Hive --plan plans/ONE-PLAN.md"):
            self.assertNotIn(command, bash)
        self.assertEqual(profile.claude_integration_permissions("Hive*", "plan", "upstream", "main", "python", "cli.py"),
                         ["Bash(git push upstream HEAD:main)", "PowerShell(git push upstream HEAD:main)"])

    def test_prepare_writes_only_settings_and_copies_nothing(self):
        """The profile gets the settings of HaiveQueen only; private instruction and login files of the home folder stay out."""
        home = Path(self.tmp.name) / "home"
        (home / ".claude").mkdir(parents=True)
        (home / ".codex").mkdir()
        for rel in (".claude/CLAUDE.md", ".claude/.credentials.json", ".codex/AGENTS.md", ".codex/auth.json"):
            (home / rel).write_text("private", encoding="utf-8")
        with mock.patch.dict(os.environ, {"HOME": str(home), "USERPROFILE": str(home)}):
            self.assertTrue(profile.prepare(provider("claude", 1)))
            self.assertTrue(profile.prepare(provider("codex", 1, proto="codex")))
        files = sorted(str(p.relative_to(self.data / "profile")).replace("\\", "/") for p in (self.data / "profile").rglob("*") if p.is_file())
        self.assertEqual(files, ["claude/settings.json", "codex/config.toml"])
        self.assertNotIn("private", " ".join((self.data / "profile" / f).read_text(encoding="utf-8") for f in files))
        self.assertIn('"autoMemoryEnabled": false', (self.data / "profile/claude/settings.json").read_text(encoding="utf-8"))
        self.assertIn("memories = false", (self.data / "profile/codex/config.toml").read_text(encoding="utf-8"))

    def test_old_session_logs_removed(self):
        """Session history is not kept: logs older than the keep time go, newer ones stay."""
        old, new = self.data / "profile/codex/sessions/old.jsonl", self.data / "profile/codex/sessions/new.jsonl"
        old.parent.mkdir(parents=True)
        old.write_text("x", encoding="utf-8")
        new.write_text("x", encoding="utf-8")
        past = time.time() - (profile.KEEP_DAYS + 1) * 86400
        os.utime(old, (past, past))
        profile.prepare(provider("codex", 1, proto="codex"))
        self.assertFalse(old.exists())
        self.assertTrue(new.exists())


class LoginTest(ProfileCase):
    def test_provider_without_login_is_unavailable(self):
        """No login file in the profile: no free slot (packages wait), the others go on; with the login the slots return."""
        cfg = {"providers": [provider("claude", 2), provider("codex", 2, proto="codex")]}
        providers = sch.get_providers(cfg)
        self.assertEqual([p["login"] for p in providers], [False, False])
        self.assertEqual(sch.provider_free(providers, []), {"claude": 0, "codex": 0})
        (self.data / "profile/codex").mkdir(parents=True)
        (self.data / "profile/codex/auth.json").write_text("{}", encoding="utf-8")
        providers = sch.get_providers(cfg)
        free = sch.provider_free(providers, [])
        self.assertEqual(free, {"claude": 0, "codex": 2})
        self.assertEqual(sch.select_provider(providers, "medium", [], free, limits={})["name"], "codex")

    def test_owner_is_told_once(self):
        """The window event names the provider and the login command once; a later login clears the mark."""
        conn = db.connect(self.data / "haivequeen.db")
        cfg = {"providers": [provider("claude", 2)]}
        self.assertEqual(profile.notify_missing(conn, sch.get_providers(cfg)), ["claude"])
        self.assertEqual(profile.notify_missing(conn, sch.get_providers(cfg)), [])
        events = [e[0] for e in conn.execute("SELECT text FROM events").fetchall() if "login" in e[0]]
        self.assertEqual(len(events), 1)
        self.assertIn("claude auth login", events[0])
        self.assertIn(str(self.data / "profile" / "claude"), events[0])
        (self.data / "profile/claude").mkdir(parents=True)
        (self.data / "profile/claude/.credentials.json").write_text("{}", encoding="utf-8")
        self.assertEqual(profile.notify_missing(conn, sch.get_providers(cfg)), [])
        (self.data / "profile/claude/.credentials.json").unlink()
        self.assertEqual(profile.notify_missing(conn, sch.get_providers(cfg)), ["claude"])   # logged out again: told again
        conn.close()

    def test_unconfinable_provider(self):
        """A CLI the profile cannot confine has no profile, no login and is not prepared."""
        odd = {"name": "odd", "protocol": "odd", "slots": 2}
        self.assertFalse(profile.confined(odd))
        self.assertFalse(profile.prepare(odd))
        self.assertFalse(profile.has_login(odd))


CUSTOM = {"name": "mycli", "protocol": "mycli", "command": "mycli", "slots": 1, "profileEnv": {"MYCLI_HOME": "{home}/cfg"},
          "loginCommand": "mycli signin", "loginFiles": ["cfg/token.json"]}


class CustomCliTest(ProfileCase):
    """A CLI HaiveQueen does not know brings its profile variables, login command and login files from the provider entry."""

    def test_custom_cli_is_kept_to_the_profile(self):
        """Profile variable, login status and login text of an unknown CLI come from the entry's own values."""
        self.assertTrue(profile.confined(CUSTOM))
        home = self.data / "profile" / "mycli"
        self.assertEqual(Path(profile.environment(CUSTOM, {})["MYCLI_HOME"]), home / "cfg")
        self.assertFalse(profile.has_login(CUSTOM))
        (home / "cfg").mkdir(parents=True)
        (home / "cfg" / "token.json").write_text("{}", encoding="utf-8")
        self.assertTrue(profile.has_login(CUSTOM))
        self.assertTrue(profile.prepare(CUSTOM))
        self.assertIn("mycli signin", profile.login_text(CUSTOM))
        self.assertEqual(profile.login_command(CUSTOM), "mycli signin")

    def test_missing_values_keep_todays_rule(self):
        """Without the variables or without the login files an unknown CLI cannot be kept to the profile."""
        for drop in ("profileEnv", "loginFiles"):
            odd = {k: v for k, v in CUSTOM.items() if k != drop}
            self.assertFalse(profile.confined(odd), drop)
            self.assertFalse(profile.has_login(odd), drop)
            self.assertFalse(profile.prepare(odd), drop)

    def test_known_cli_ignores_custom_values(self):
        """Claude keeps its built-in values even when the entry carries own ones."""
        prov = {"name": "claude", "protocol": "claude", "profileEnv": {"X": "{home}"}, "loginFiles": ["x"], "loginCommand": "x"}
        self.assertIn("CLAUDE_CONFIG_DIR", profile.environment(prov, {}))
        self.assertNotIn("X", profile.environment(prov, {}))
        self.assertEqual(profile.login_command(prov), "claude auth login")

    def test_scheduler_passes_the_values_on(self):
        """get_providers keeps the three values so that the profile and the window see them."""
        cfg = {"providers": [dict(provider("claude", 1), profileEnv={"A": "{home}"}, loginCommand="c", loginFiles=["f"])]}
        got = sch.get_providers(cfg)[0]
        self.assertEqual((got["profileEnv"], got["loginCommand"], got["loginFiles"]), ({"A": "{home}"}, "c", ["f"]))


class LoginLaunchTest(ProfileCase):
    """The login button opens a visible terminal with the profile variables set and the login command running."""

    def run_login(self, prov, windows):
        """Start the login with a fake Popen and return its (process, seen arguments)."""
        seen = {}

        def fake(argv, **kw):
            seen.update(argv=argv, kw=kw)
            return mock.Mock()
        with mock.patch("haivequeen.platform.is_windows", return_value=windows), mock.patch("shutil.which", side_effect=lambda n: "/usr/bin/" + n):
            proc = profile.login_run(prov, popen=fake)
        return proc, seen

    def test_windows_terminal(self):
        """On Windows a new console runs cmd /k with the login command; the environment holds the profile variable."""
        proc, seen = self.run_login({"name": "claude", "protocol": "claude", "command": "claude"}, True)
        self.assertIsNotNone(proc)
        self.assertEqual(seen["argv"][:2], ["cmd.exe", "/k"])
        self.assertIn("claude auth login", seen["argv"][-1])
        self.assertEqual(seen["kw"]["creationflags"], 0x00000010)
        self.assertEqual(Path(seen["kw"]["env"]["CLAUDE_CONFIG_DIR"]), self.data / "profile" / "claude")
        self.assertTrue((self.data / "profile" / "claude").is_dir())

    def test_linux_terminal(self):
        """On Linux a terminal emulator runs the login command in bash and stays open afterwards."""
        _proc, seen = self.run_login({"name": "codex", "protocol": "codex", "command": "codex"}, False)
        self.assertIn("codex login", " ".join(seen["argv"]))
        self.assertEqual(Path(seen["kw"]["env"]["CODEX_HOME"]), self.data / "profile" / "codex")

    def test_no_login_without_command_or_profile(self):
        """An unknown CLI without a login command or without profile values gets no terminal."""
        called = []
        odd = {"name": "odd", "protocol": "odd"}
        nocmd = {k: v for k, v in CUSTOM.items() if k != "loginCommand"}
        self.assertIsNone(profile.login_run(odd, popen=lambda *a, **k: called.append(1)))
        self.assertIsNone(profile.login_run(nocmd, popen=lambda *a, **k: called.append(1)))
        self.assertEqual(called, [])


class RunnerTest(RunnerBase):
    """The runner starts the fake CLIs with the profile environment."""

    def setUp(self):
        super().setUp()
        (self.base / "fake_cli.py").write_text(ENV_FAKE_CLI, encoding="utf-8")
        self.home = self.base / "home"
        (self.home / ".claude").mkdir(parents=True)
        (self.home / ".claude" / "CLAUDE.md").write_text("private", encoding="utf-8")
        self.env.update({"HOME": str(self.home), "USERPROFILE": str(self.home), "CLAUDE_CONFIG_DIR": str(self.home / ".claude"),
                         "CODEX_HOME": str(self.home / ".codex")})
        self.profile = self.base / "data" / "profile"

    def test_claude_and_codex_start_in_profile(self):
        """Both CLIs get the profile folder as config dir, memory is off, the private folders are not in their environment."""
        self.prepare("PR-1")
        r = self.run_runner("PR-1", "claude", claude_stream())
        self.assertEqual(r.returncode, 0, r.stderr)
        env = self.record()["env"]
        self.assertEqual(Path(env["CLAUDE_CONFIG_DIR"]), self.profile / "claude")
        self.assertEqual(env["CLAUDE_CODE_DISABLE_AUTO_MEMORY"], "1")
        self.prepare("PR-2", provider="codex")
        r = self.run_runner("PR-2", "codex", codex_stream())
        self.assertEqual(r.returncode, 0, r.stderr)
        env = self.record()["env"]
        self.assertEqual(Path(env["CODEX_HOME"]), self.profile / "codex")
        files = sorted(str(p.relative_to(self.profile)).replace("\\", "/") for p in self.profile.rglob("*") if p.is_file())
        self.assertEqual(files, ["claude/settings.json", "codex/config.toml"])

    def test_unconfinable_provider_stops_and_asks(self):
        """A provider that cannot be kept to the profile is not started; the package fails and one question is filed."""
        self.prepare("PR-3")
        saved = profile.SPECS.pop("claude")
        self.addCleanup(profile.SPECS.__setitem__, "claude", saved)
        with mock.patch.dict(os.environ, {k: self.env[k] for k in ("BZZ_CONFIG_DIR", "BZZ_DATA_DIR", "BZZ_LOG_DIR")}):
            a = ar.parse_args(["--project", "demo", "--name", "PR-3", "--provider", "claude", "--plan", "plans/Demo-PLAN.md", "--package", "P1",
                               "--phase", "work", "--cwd", str(self.ws), "--prompt-file", str(self.prompt), "--runs-dir", str(self.runs)])
            with open(os.devnull, "w") as out:
                ar.run_agent(a, out)
        gc.collect()   # the runner leaves its database connection to the garbage collector
        self.assertEqual(self.row("PR-3")["status"], "failed")
        self.assertFalse((self.base / "record.json").exists())
        qs = runtime.list_questions(self.conn, "demo", status="open")
        self.assertEqual(len(qs), 1)
        self.assertIn("cannot be kept to the HaiveQueen profile", qs[0]["question"])


if __name__ == "__main__":
    unittest.main()

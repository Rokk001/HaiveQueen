"""CLIs that run out of the box: the choice list with its presets, the OpenCode-style JSONL output (OpenCode and Kilo Code), and the rule that a
custom CLI is accepted only when it writes an output format HaiveQueen understands."""
import gc
import json
import unittest
from pathlib import Path

import bzztest  # noqa: F401  (redirects the real folders)
from haivequeen import cli_presets, profile
from haivequeen import scheduler as sch
from test_agent_runner import FAKE_CLI, RunnerBase, toml_str
from test_profile import ProfileCase

ENV_FAKE_CLI = FAKE_CLI.replace('"bzz_agent": os.environ.get("BZZ_AGENT")', '"bzz_agent": os.environ.get("BZZ_AGENT"), "env": dict(os.environ)')


def opencode_stream(error=False):
    """A fake `run --format json` stream: step, tool, text, step end with tokens, a second text and step end (events as in the official source)."""
    def line(kind, **data):
        return json.dumps({"type": kind, "timestamp": 1, "sessionID": "ses_1", **data})
    events = [
        line("step_start", part={"type": "step-start"}),
        line("tool_use", part={"type": "tool", "callID": "c1", "tool": "bash", "state": {"status": "completed", "input": {"command": "ls"}, "output": "x", "title": "ls", "metadata": {}, "time": {"start": 1, "end": 2}}}),
        line("text", part={"type": "text", "text": "Working on it", "time": {"start": 1, "end": 2}}),
        line("step_finish", part={"type": "step-finish", "reason": "tool-calls", "cost": 0, "tokens": {"input": 100, "output": 20, "reasoning": 0, "cache": {"read": 50, "write": 10}}}),
        line("text", part={"type": "text", "text": "Open requirements: 0", "time": {"start": 3, "end": 4}}),
        line("step_finish", part={"type": "step-finish", "reason": "stop", "cost": 0, "tokens": {"input": 30, "output": 5, "reasoning": 0, "cache": {"read": 200, "write": 0}}}),
    ]
    if error:
        events.append(line("error", error={"name": "APIError", "data": {"message": "boom"}}))
    return "\n".join(events) + "\n"


class PresetTest(ProfileCase):
    """Choosing a CLI sets every value HaiveQueen needs; login status and login command use the right environment."""

    def test_choice_list_holds_only_running_clis(self):
        """The list has the CLIs that run plus the custom entry, in this order."""
        self.assertEqual(cli_presets.labels(), ["Claude Code", "Codex CLI", "OpenCode", "Kilo Code", "Custom CLI"])

    def test_choice_sets_all_values(self):
        """Each choice sets protocol, command, arguments with the model and the directory, and the Bee mode; the profile knows the CLI afterwards."""
        for key, proto, command, bee in (("claude", "claude", "claude", "subagent"), ("codex", "codex", "codex", "process"),
                                         ("opencode", "opencode", "opencode", "process"), ("kilo", "kilo", "kilo", "process")):
            entry = {"name": "x", "slots": 1, "profileEnv": {"A": "b"}, "outputFormat": "claude"}
            cli_presets.apply(entry, key)
            self.assertEqual((entry["protocol"], entry["command"], entry["beeMode"]), (proto, command, bee), key)
            self.assertIn("{model}", entry["args"], key)
            self.assertNotIn("profileEnv", entry, key)   # a built-in CLI brings its own profile values
            self.assertNotIn("outputFormat", entry, key)
            self.assertTrue(profile.confined(entry), key)
            self.assertEqual(cli_presets.key_for(entry), key)
        entry = {"name": "x"}
        cli_presets.apply(entry, "opencode")
        self.assertEqual(entry["args"][:4], ["run", "--format", "json", "--auto"])
        cli_presets.apply(entry, "kilo")
        self.assertEqual(entry["args"][:4], ["run", "--format", "json", "--auto"])

    def test_custom_choice_keeps_values(self):
        """Custom CLI changes nothing and is the choice for an entry no preset matches."""
        entry = {"name": "x", "protocol": "mycli", "command": "mycli", "args": ["--a"]}
        before = dict(entry)
        cli_presets.apply(entry, "custom")
        self.assertEqual(entry, before)
        self.assertEqual(cli_presets.key_for(entry), "custom")

    def test_kilo_profile(self):
        """Kilo Code: XDG folders and the config folder point into the profile, login command and login file as in its official docs and source."""
        prov = {"name": "kilo", "protocol": "kilo", "command": "kilo"}
        home = self.data / "profile" / "kilo"
        env = profile.environment(prov, {})
        for key, sub in (("XDG_DATA_HOME", "data"), ("XDG_CONFIG_HOME", "config"), ("XDG_STATE_HOME", "state"), ("XDG_CACHE_HOME", "cache"), ("KILO_CONFIG_DIR", "config")):
            self.assertEqual(Path(env[key]), home / sub, key)
        self.assertEqual(profile.login_command(prov), "kilo auth login")
        self.assertFalse(profile.has_login(prov))
        (home / "data" / "kilo").mkdir(parents=True)
        (home / "data" / "kilo" / "auth.json").write_text("{}", encoding="utf-8")
        self.assertTrue(profile.has_login(prov))
        self.assertTrue(profile.prepare(prov))

    def test_opencode_login(self):
        """OpenCode: login command `opencode auth login`, login file data/opencode/auth.json in its profile folder."""
        prov = {"name": "oc", "protocol": "opencode", "command": "opencode"}
        self.assertEqual(profile.login_command(prov), "opencode auth login")
        self.assertFalse(profile.has_login(prov))
        (self.data / "profile" / "opencode" / "data" / "opencode").mkdir(parents=True)
        (self.data / "profile" / "opencode" / "data" / "opencode" / "auth.json").write_text("{}", encoding="utf-8")
        self.assertTrue(profile.has_login(prov))


class ValidationTest(unittest.TestCase):
    """Which providers the loop accepts."""

    def test_new_protocols_are_accepted(self):
        """opencode and kilo are protocols; they run as separate processes by default (the embedded Bee mode is a Claude Code feature)."""
        for proto in ("opencode", "kilo"):
            got = sch.get_providers({"providers": [{"name": proto, "protocol": proto, "slots": 1}]})[0]
            self.assertEqual((got["protocol"], got["beeMode"], got["format"]), (proto, "process", "opencode"))

    def test_custom_cli_needs_a_known_output_format(self):
        """An unknown protocol is refused with a clear message unless the entry names an output format HaiveQueen understands."""
        with self.assertRaises(ValueError) as cm:
            sch.get_providers({"providers": [{"name": "mycli", "protocol": "mycli", "slots": 1}]})
        self.assertIn("outputFormat", str(cm.exception))
        self.assertIn("claude", str(cm.exception))
        with self.assertRaises(ValueError):
            sch.get_providers({"providers": [{"name": "mycli", "protocol": "mycli", "outputFormat": "xml"}]})
        got = sch.get_providers({"providers": [{"name": "mycli", "protocol": "mycli", "outputFormat": "opencode", "slots": 1}]})[0]
        self.assertEqual(got["format"], "opencode")
        from haivequeen import agent_runner as ar
        with self.assertRaises(ValueError):
            ar.get_providers({"providers": [{"name": "mycli", "protocol": "mycli"}]})
        self.assertEqual(ar.get_providers({"providers": [{"name": "mycli", "protocol": "mycli", "outputFormat": "codex"}]})[0]["format"], "codex")


class FormatRunTest(RunnerBase):
    """The runner reads the OpenCode-style JSONL of OpenCode and Kilo Code (fake CLI)."""

    def write_config(self, extra=""):
        """The standard fake providers plus an OpenCode and a Kilo Code provider that start the same fake program."""
        super().write_config(extra)
        cfg = self.base / "cfg" / "config.toml"
        for name in ("opencode", "kilo"):
            cfg.write_text(cfg.read_text(encoding="utf-8") + "\n[[providers]]\nname = '%s'\nprotocol = '%s'\ncommand = %s\n"
                           "args = ['run', '--format', 'json', '--auto', '--model', '{model}', '--dir', '{cwd}']\n[providers.classes.medium]\nmodel = 'prov/model-x'\neffort = 'medium'\n"
                           % (name, name, toml_str(self.fake["claude"])), encoding="utf-8")

    def setUp(self):
        super().setUp()
        (self.base / "fake_cli.py").write_text(ENV_FAKE_CLI, encoding="utf-8")

    def record(self):
        """The record the fake CLI wrote."""
        return json.loads((self.base / "record.json").read_text(encoding="utf-8"))

    def test_opencode_and_kilo_runs(self):
        """Final answer, tokens and context come from the events; the command line has the prompt last; the CLI starts in the profile."""
        for name in ("opencode", "kilo"):
            agent = "F-" + name
            self.prepare(agent, provider=name)
            r = self.run_runner(agent, name, opencode_stream())
            gc.collect()
            self.assertEqual(r.returncode, 0, r.stderr)
            row = self.row(agent)
            self.assertEqual(row["status"], "done", (name, row.get("lastLine")))
            self.assertEqual((self.runs / agent / "final.txt").read_text(encoding="utf-8"), "Open requirements: 0")
            self.assertEqual(row["tokens_out"], 25)
            self.assertEqual(row["tokens_in"], 160 + 230)
            self.assertEqual(row["ctxTokens"], 230)   # the latest step: input + cache read + cache write
            rec = self.record()
            self.assertEqual(rec["argv"][:4], ["run", "--format", "json", "--auto"])
            self.assertIn("prov/model-x", rec["argv"])
            self.assertIn("prompt.md", rec["argv"][-1])   # the prompt is the last argument
            self.assertEqual(Path(rec["env"]["XDG_DATA_HOME"]), self.base / "data" / "profile" / name / "data")

    def test_error_event_fails_the_run(self):
        """An error event after the last step makes the run failed and is kept for the interruption check."""
        self.prepare("F-err", provider="opencode")
        r = self.run_runner("F-err", "opencode", opencode_stream(error=True))
        gc.collect()
        self.assertEqual(r.returncode, 0, r.stderr)
        self.assertEqual(self.row("F-err")["status"], "failed")

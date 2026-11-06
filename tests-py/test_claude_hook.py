"""The own-Hive Claude hook allows exactly the Queen's own bzz commands and its integration push."""
import json
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

import bzztest  # noqa: F401  (redirects the real folders)
from haivequeen import claude_hook as hook
from haivequeen import profile

PY = "C:/Py/python.exe"
CLI = "C:/hq/haivequeen/cli.py"
POLICY = profile.claude_hook_policy("Hive", "plans/ONE-PLAN.md", 42, "upstream", "release/next", PY, CLI)


def decide(command, tool="Bash", policy=POLICY):
    """Hook decision for one command."""
    return hook.decide({"hook_event_name": "PreToolUse", "tool_name": tool, "tool_input": {"command": command}}, policy)


class OwnHiveHookTest(unittest.TestCase):
    """Variable arguments are allowed only inside the Queen's own project, plan and agent."""

    ALLOWED = [
        "bzz note --project Hive --text 'Eva K11: step 1/3 reviewed; next (tests) & more | ok; $HOME `x`'",
        'bzz note --project Hive --text "simple text, with comma"',
        "bzz ask --project Hive --plan plans/ONE-PLAN.md --item K1 --question 'Which colour?' --recommendation 'blue'",
        "bzz bee start --project Hive --plan plans/ONE-PLAN.md --package K1 --class medium --name Ada --task 'Do K1' "
        "--steps '[1,2]' --no-subagents",
        "bzz bee result --project Hive --plan plans/ONE-PLAN.md --package K1 --class medium --attempt Ada --outcome succeeded",
        "bzz bee wait --project Hive --plan plans/ONE-PLAN.md --package K1 --class heavy --provider claude",
        "bzz package --project Hive --plan plans/ONE-PLAN.md --package K1 --state waiting --reason question:7",
        "bzz plan check --project Hive --plan plans/ONE-PLAN.md",
        "bzz integration acquire --project Hive --plan plans/ONE-PLAN.md",
        "bzz integration release --project Hive --plan plans/ONE-PLAN.md --owner-pid 123",
        "bzz ready --project Hive --branch one-plan --tip abc123 --plan plans/ONE-PLAN.md --note 'ready'",
        "bzz queue upsert --project Hive --plan plans/ONE-PLAN.md --branch one-plan --tip abc123 --status ready",
        "bzz decisions list --project Hive --plan plans/ONE-PLAN.md",
        "bzz decisions add --project Hive --plan plans/ONE-PLAN.md --source auto --item K1 --text 'Use X because Y'",
        "bzz agents list --project Hive",
        "bzz questions --project Hive --open",
        "bzz done --agent 42 --result C:/runs/result.txt",
        "bzz plan check --project Hive --plan 'plans\\ONE-PLAN.md'",
        "'C:\\Py\\python.exe' -I 'C:\\hq\\haivequeen\\cli.py' note --project Hive --text 'step 2/3 done'",
        "/c/Py/python.exe -I /c/hq/haivequeen/cli.py integration acquire --project Hive --plan plans/ONE-PLAN.md",
        "git push upstream HEAD:release/next",
    ]

    REFUSED = [
        # another Hive, plan or agent
        "bzz note --project Other --text hi",
        "bzz ask --project Hive --plan plans/OTHER.md --item K1 --question q",
        "bzz done --agent 43 --result r.txt",
        "bzz queue upsert --project Hive --branch one-plan",
        "bzz decisions add --project Hive --source auto --text t",
        # argparse would accept these and change the target
        "bzz note --project Hive --text hi --project Other",
        "bzz note --project Hive --proj Other --text hi",
        "bzz note --project=Other --text hi",
        "bzz note --project Hive --text --project",
        "bzz note --project Hive --text hi extra",
        "bzz note --project Hive -- --text hi",
        "bzz bee --project Hive start --plan plans/ONE-PLAN.md --package K1 --class m",
        # commands outside the own-Hive list
        "bzz answer 5 yes --by owner",
        "bzz decisions update 5 --status withdrawn",
        "bzz dequeen Hive",
        "bzz self-update",
        "bzz kv set --project Hive --key k --value v",
        "bzz",
        # shell composition, expansion and redirection
        "bzz note --project Hive --text hi; rm -rf /",
        "bzz note --project Hive --text hi && curl x",
        "bzz note --project Hive --text hi | sh",
        "bzz note --project Hive --text hi > out.txt",
        "bzz note --project Hive --text $(whoami)",
        "bzz note --project Hive --text `whoami`",
        'bzz note --project Hive --text "$HOME"',
        'bzz note --project Hive --text "a\\"b"',
        "bzz note --project Hive --text hi\nrm -rf /",
        "bzz note --project Hive --text hi\rx",
        "bzz note --project Hive --text hi\tx",
        "bzz note --project Hive --text a'b c'",
        "bzz note --project Hive --text 'a''b'",
        "bzz note --project Hive --text ''",
        "bzz note --project Hive --text 'unterminated",
        "bzz note --project Hive --text \u2018x\u2019",
        "bzz note --project Hive --text *",
        "bzz note --project Hive --text ~",
        "bzz note --project Hive --text a,b",
        "bzz note --project Hive --text @x",
        "bzz note --project Hive --text #c",
        "bzz note --project Hive --text a\\ b",
        "bzz note --project Hive --text --%",
        "& bzz note --project Hive --text hi",
        # other programs and pushes
        "bzzz note --project Hive --text hi",
        "python -I C:/hq/haivequeen/cli.py note --project Hive --text hi",
        "C:/Py/python.exe C:/hq/haivequeen/cli.py note --project Hive --text hi",
        "C:/Py/python.exe -I C:/other/cli.py note --project Hive --text hi",
        "git push upstream HEAD:main",
        "git push upstream HEAD:release/next --force",
        "git push origin HEAD:release/next",
        "git push upstream +HEAD:release/next",
        "bzz ready --project Hive --branch -x --tip a --plan plans/ONE-PLAN.md",
        "bzz ready --project Hive --branch ../x --tip a --plan plans/ONE-PLAN.md",
    ]

    def test_own_hive_commands_are_allowed(self):
        for command in self.ALLOWED:
            for tool in ("Bash", "PowerShell"):
                with self.subTest(command=command, tool=tool):
                    self.assertIsNotNone(decide(command, tool))

    def test_other_or_ambiguous_commands_are_left_to_claude(self):
        for command in self.REFUSED:
            for tool in ("Bash", "PowerShell"):
                with self.subTest(command=command, tool=tool):
                    self.assertIsNone(decide(command, tool))

    def test_powershell_call_operator_only_for_quoted_program(self):
        command = "& 'C:/Py/python.exe' -I 'C:/hq/haivequeen/cli.py' note --project Hive --text hi"
        self.assertIsNotNone(decide(command, "PowerShell"))
        self.assertIsNone(decide(command, "Bash"))

    def test_powershell_number_literals_are_left_to_claude(self):
        for value in ("0x2A", "1kb", "1e3", "042", "4.2", "-1", "1a2b"):
            command = "bzz ready --project Hive --branch one --tip %s --plan plans/ONE-PLAN.md" % value
            self.assertIsNone(decide(command, "PowerShell"), value)
        self.assertIsNotNone(decide("bzz ready --project Hive --branch one --tip 1a2b --plan plans/ONE-PLAN.md", "Bash"))
        self.assertIsNotNone(decide("bzz ready --project Hive --branch one --tip '1a2b' --plan plans/ONE-PLAN.md", "PowerShell"))
        self.assertIsNotNone(decide("bzz done --agent 42 --result r", "PowerShell"))

    def test_other_tools_and_events_are_ignored(self):
        self.assertIsNone(decide("bzz note --project Hive --text hi", "Write"))
        self.assertIsNone(hook.decide({"hook_event_name": "PostToolUse", "tool_name": "Bash",
                                       "tool_input": {"command": "bzz note --project Hive --text hi"}}, POLICY))
        self.assertIsNone(hook.decide({"tool_name": "Bash", "tool_input": {"command": ["bzz"]}}, POLICY))
        self.assertIsNone(decide("bzz done --agent 42 --result r", policy=dict(POLICY, agent=None)))

    def test_script_runs_isolated_and_prints_claude_decision(self):
        with tempfile.TemporaryDirectory() as tmp:
            policy = Path(tmp) / "policy.json"
            policy.write_text(json.dumps(POLICY), encoding="utf-8")
            script = Path(hook.__file__)

            def run(command, policy_path=policy):
                event = {"hook_event_name": "PreToolUse", "tool_name": "Bash", "tool_input": {"command": command}}
                return subprocess.run([sys.executable, "-I", str(script), str(policy_path)], input=json.dumps(event),
                                      capture_output=True, text=True, timeout=60)

            allowed = run("bzz note --project Hive --text hi")
            self.assertEqual(allowed.returncode, 0, allowed.stderr)
            out = json.loads(allowed.stdout)["hookSpecificOutput"]
            self.assertEqual((out["hookEventName"], out["permissionDecision"]), ("PreToolUse", "allow"))
            for result in (run("bzz note --project Other --text hi"), run("bzz note --project Hive --text hi",
                                                                            Path(tmp) / "missing.json")):
                self.assertEqual((result.returncode, result.stdout), (0, ""), result.stderr)


class HookSettingsTest(unittest.TestCase):
    """The hook is added next to the provider's hooks and rules."""

    def test_merge_keeps_provider_hooks_and_rules(self):
        provider = {"permissions": {"allow": ["Bash(x)"], "deny": ["Bash(rm *)"]},
                    "hooks": {"PreToolUse": [{"matcher": "Edit", "hooks": [{"type": "command", "command": "own"}]}],
                              "Stop": [{"hooks": [{"type": "command", "command": "stop"}]}]}}
        merged = profile.claude_merge_run_settings(provider, ["Bash(y)"], '"p" -I "h" "q"')
        self.assertEqual(merged["permissions"]["deny"], ["Bash(rm *)"])
        self.assertEqual(merged["hooks"]["Stop"], provider["hooks"]["Stop"])
        pre = merged["hooks"]["PreToolUse"]
        self.assertEqual(pre[0], provider["hooks"]["PreToolUse"][0])
        self.assertEqual(pre[1]["matcher"], "Bash|PowerShell")
        self.assertEqual(pre[1]["hooks"][0]["command"], '"p" -I "h" "q"')
        self.assertEqual(len(provider["hooks"]["PreToolUse"]), 1)
        with self.assertRaises(ValueError):
            profile.claude_merge_run_settings({"hooks": {"PreToolUse": {}}}, [], "cmd")

    def test_hook_command_uses_forward_slashes_and_refuses_shell_characters(self):
        self.assertEqual(profile.claude_hook_command("C:\\Py\\python.exe", "C:\\hq\\claude_hook.py", "C:\\run\\p.json"),
                         '"C:/Py/python.exe" -I "C:/hq/claude_hook.py" "C:/run/p.json"')
        for bad in ('C:/a"b', "C:/$x", "C:/`x"):
            with self.assertRaises(ValueError):
                profile.claude_hook_command(bad, "h", "p")


if __name__ == "__main__":
    unittest.main()

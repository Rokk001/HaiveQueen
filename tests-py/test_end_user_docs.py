"""Documentation contracts for the public command/settings reference."""
import argparse
import re
import unittest
from pathlib import Path

from haivequeen import cli, config, scheduler, settings, provider_labels, projsettings, plans

ROOT = Path(__file__).resolve().parents[1]


def command_paths(parser, prefix="bzz"):
    children = [a for a in parser._actions if isinstance(a, argparse._SubParsersAction)]
    if children:
        for action in children:
            for name, child in action.choices.items():
                yield from command_paths(child, prefix + " " + name)
    else:
        yield prefix
        for action in parser._actions:
            if action.dest == "action" and action.choices:
                for choice in action.choices:
                    yield prefix + " " + choice


class EndUserDocsTests(unittest.TestCase):
    def test_every_parser_command_has_reference_entry(self):
        text = (ROOT / "docs/COMMANDS.md").read_text(encoding="utf-8")
        for command in command_paths(cli.build_parser()):
            with self.subTest(command=command):
                self.assertIn("`" + command + "`", text)

    def test_current_settings_are_documented(self):
        text = (ROOT / "docs/SETTINGS.md").read_text(encoding="utf-8")
        keys = set(config.DEFAULT_CONFIG) | set(scheduler.DEFAULT_SETTINGS)
        keys |= set(settings.SETTING_LABELS) | set(settings.GENERAL_LABELS)
        keys |= set(provider_labels.TEXT)
        keys |= set(projsettings.KEPT_KEYS)
        for key in keys:
            with self.subTest(key=key):
                self.assertIn("`" + key + "`", text)

    def test_cut_plan_example_passes_product_validation(self):
        text = (ROOT / "docs/PLANS.md").read_text(encoding="utf-8")
        sample = re.search(r"```toml\n(.*?)\n```", text, re.S).group(1)
        plan = plans.parse_plan_text(sample, "docs/plans/example.md")
        requirements = plans.parse_requirements(sample)
        self.assertEqual([], plans.check_plan(plan, ["Coding", "Image generation"], requirements))

    def test_submission_and_profile_contract(self):
        plans = (ROOT / "docs/PLANS.md").read_text(encoding="utf-8")
        self.assertIn('status = "SENT"', plans)
        self.assertIn("already cut", plans)
        self.assertNotIn("there is no submit step", plans)
        profile = (ROOT / "docs/PROFILE.md").read_text(encoding="utf-8")
        self.assertIn("not a security sandbox", profile)

    def test_topic_links_resolve(self):
        names = ["GETTING-STARTED", "PLANS", "OPERATIONS", "PROVIDERS", "SETTINGS", "INBOX", "COMMANDS", "DEVELOPMENT", "DOCUMENTATION-MAP"]
        for name in names:
            path = ROOT / "docs" / (name + ".md")
            text = path.read_text(encoding="utf-8")
            for target in re.findall(r"\]\(([^)]+)\)", text):
                if "://" in target or target.startswith("#"):
                    continue
                with self.subTest(path=path.name, target=target):
                    self.assertTrue((path.parent / target.split("#")[0]).exists())

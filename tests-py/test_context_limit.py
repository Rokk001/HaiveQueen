"""The context limit is the current context size and is watched by the Beekeeper through the HANDOFF file, never by the Queen from summed input tokens."""
import unittest

import bzztest  # noqa: F401  (puts this checkout first on the import path)
from haivequeen import bees


class QueenContextRuleTests(unittest.TestCase):
    """A Queen ended after one or two minutes because she compared the summed input tokens of her agent row (millions, Codex counts the whole context at every step) with the limit."""

    def text(self):
        """The Queen instructions with the default limit."""
        return bees.queen_instructions("medium", 3, 3, 300000)

    def test_queen_does_not_measure_her_context_herself(self):
        """The instructions forbid measuring the context from token numbers of bzz."""
        text = self.text()
        self.assertNotIn("At 300000 context", text)
        self.assertIn("Never measure your context yourself", text)
        self.assertIn("summed", text)

    def test_queen_ends_only_when_the_handoff_file_exists(self):
        """The Beekeeper writes the HANDOFF file when the current context reaches the limit; only then the Queen ends."""
        text = self.text()
        self.assertIn("file HANDOFF", text)
        self.assertIn("300000", text)
        self.assertIn("current context", text)


class GlobalLimitTests(unittest.TestCase):
    """G1: contextLimitTokens is set once in config.toml and applies to every project; a project may override it."""

    def test_global_value_applies_to_a_project_without_its_own(self):
        """The machine value reaches the project settings."""
        from haivequeen import scheduler
        self.assertEqual(scheduler.build_settings({"contextLimitTokens": 123000}, {})["contextLimitTokens"], 123000)

    def test_project_value_still_wins(self):
        """project.toml over config.toml over the default."""
        from haivequeen import scheduler
        self.assertEqual(scheduler.build_settings({"contextLimitTokens": 123000}, {"contextLimitTokens": 99000})["contextLimitTokens"], 99000)
        self.assertEqual(scheduler.build_settings({}, {})["contextLimitTokens"], 300000)


class ModelLimitTests(unittest.TestCase):
    """G2: a model entry under [providers.classes] may carry its own contextLimitTokens that wins over the global value."""

    PROVIDERS = [{"name": "codex", "classes": {"light": {"model": "a"}, "medium": {"model": "b", "contextLimitTokens": 150000}}}]

    def test_model_value_wins(self):
        """The class entry of the provider decides for agents that run with it."""
        from haivequeen import scheduler
        self.assertEqual(scheduler.context_limit({"contextLimitTokens": 300000}, self.PROVIDERS, "codex", "medium"), 150000)

    def test_global_value_without_an_entry_value(self):
        """A class without its own value, an unknown class and an unknown provider use the global value."""
        from haivequeen import scheduler
        settings = {"contextLimitTokens": 280000}
        self.assertEqual(scheduler.context_limit(settings, self.PROVIDERS, "codex", "light"), 280000)
        self.assertEqual(scheduler.context_limit(settings, self.PROVIDERS, "codex", "heavy"), 280000)
        self.assertEqual(scheduler.context_limit(settings, self.PROVIDERS, "other", "medium"), 280000)
        self.assertEqual(scheduler.context_limit({}, [], None, None), 300000)

    def test_beekeeper_flags_a_queen_at_the_model_limit(self):
        """A Queen whose model has the lower limit gets the HANDOFF file at that limit, not at the global one."""
        from haivequeen import routine
        env = _ModelEnv(self.PROVIDERS)
        queen = {"name": "Ada", "phase": "queen", "status": "running", "ctxTokens": 160000, "provider": "codex", "class": "medium"}
        self.assertEqual([m["Name"] for m in routine.set_handoff_flags(env, [queen])], ["Ada"])
        other = {"name": "Bea", "phase": "queen", "status": "running", "ctxTokens": 160000, "provider": "codex", "class": "light"}
        self.assertEqual(routine.set_handoff_flags(env, [other]), [])


class _ModelEnv:
    """The few project fields the handoff flags read: settings, providers and the run folders in a temp folder."""

    def __init__(self, providers):
        """Keep the providers and a temp folder for the run directories."""
        import tempfile
        from pathlib import Path
        self.settings = {"contextLimitTokens": 300000}
        self.providers = providers
        self._tmp = tempfile.TemporaryDirectory()
        self._base = Path(self._tmp.name)

    def run_dir(self, name):
        """The run folder of an agent."""
        return self._base / name


if __name__ == "__main__":
    unittest.main()

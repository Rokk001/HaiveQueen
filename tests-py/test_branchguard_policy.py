"""Guard metadata must not be mistaken for Git hooks."""
import json
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest import mock

from haivequeen import branchguard


class GuardPolicyTests(unittest.TestCase):
    def test_nested_guard_keeps_its_policy_json(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            original = root / "outer"
            original.mkdir()
            (original / "policy.json").write_text("{}", encoding="utf-8")
            (original / "pre-commit").write_text("#!/bin/sh\n", encoding="utf-8")
            outputs = [str(original), str(root / "HEAD-log"), str(root / "git")]
            with mock.patch.object(branchguard.subprocess, "run", side_effect=[SimpleNamespace(stdout=s) for s in outputs]):
                branchguard.prepare(root / "inner", root, "assigned")
            policy = json.loads((root / "inner" / "policy.json").read_text(encoding="utf-8"))
            self.assertEqual(policy["branch"], "assigned")
            self.assertTrue((root / "inner" / "pre-commit").exists())

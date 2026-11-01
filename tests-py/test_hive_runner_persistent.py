"""Tests for starting a Queen in a persistent Hive pane."""
import json
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest import mock

from haivequeen import loop


# Check that the Hive pane stays in charge of its own lifetime.
class PersistentHiveRunnerTests(unittest.TestCase):
    """The Queen must not run the wrapper that closes an individual pane."""

    # Starting a Queen in a Hive removes only the close-pane wrapper.
    def test_hive_queen_does_not_close_hive_pane(self):
        """Keep the runner arguments and omit the pane-closing wrapper."""
        with tempfile.TemporaryDirectory() as temp:
            env = SimpleNamespace(run_dir=lambda name: Path(temp) / name,
                                  update_agent=mock.Mock(), conn=object())
            spec = {"name": "Queen", "plan": "plan"}
            command = ["python", "runner", "--wrap", "--name", "Queen"]
            with mock.patch.object(loop, "runner_argv", return_value=command.copy()), \
                 mock.patch.object(loop, "rebuild_small_hive_pane"), \
                 mock.patch.object(loop, "ensure_hive_pane", return_value="window.1"), \
                 mock.patch.object(loop.hivelayout, "apply"):
                loop.start_queen(env, object(), "window", spec)
            launch = json.loads((Path(temp) / "Queen" / "launch.json").read_text(encoding="utf-8"))
            self.assertEqual(launch["argv"], ["python", "runner", "--name", "Queen"])


if __name__ == "__main__":
    unittest.main()
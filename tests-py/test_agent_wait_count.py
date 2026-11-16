"""The runner counts native calls that only wait for a subagent, so a polling Queen is visible in her agent row."""
import json
import os
import tempfile
import unittest
from pathlib import Path

import bzztest  # noqa: F401  (redirects the real folders)
from haivequeen import agent_runner as ar, db, runtime


class AgentWaitCountTest(unittest.TestCase):
    def test_codex_wait_calls_are_counted_and_stored(self):
        with tempfile.TemporaryDirectory() as tmp:
            os.environ["BZZ_DATA_DIR"] = str(Path(tmp) / "data")
            try:
                conn = db.connect(Path(tmp) / "data" / "o.db")
                runtime.upsert_agent(conn, "p", "Q-1", {"status": "running"})
                run = ar.AgentRun(conn, "p", "Q-1", "codex", "codex", tmp, Path(tmp) / "run", sessions=tmp)
                (Path(tmp) / "run").mkdir()
                for tool in ("spawn_agent", "wait", "wait", "wait"):
                    run._codex_event({"type": "item.completed", "item": {"type": "collab_tool_call", "tool": tool}})
                self.assertEqual(run.agent_waits, 3)
                run.push_state(True)
                row = runtime.get_agent(conn, "p", "Q-1")
                self.assertEqual(row.get("agentWaits", json.loads(row.get("extra_json") or "{}").get("agentWaits")), 3)
                conn.close()
            finally:
                os.environ.pop("BZZ_DATA_DIR", None)


if __name__ == "__main__":
    unittest.main()

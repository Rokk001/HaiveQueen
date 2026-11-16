"""HaiveQueen's loop and web service must not end with the agent whose command started them (Windows job of the agent CLI)."""
import subprocess
import tempfile
import unittest
from pathlib import Path
from unittest import mock

import bzztest  # noqa: F401  (redirects the real folders)
from haivequeen import loop, platform as osplat


class OutsideAgentJobTest(unittest.TestCase):
    def test_breakaway_is_requested_and_a_refusing_job_falls_back(self):
        calls = []

        def popen(*args, creationflags=0, **kwargs):
            calls.append(creationflags)
            if creationflags & osplat.CREATE_BREAKAWAY_FROM_JOB:
                raise PermissionError("job forbids breakaway")
            return "started"
        with mock.patch.object(osplat, "is_windows", return_value=True):
            self.assertEqual(osplat.popen_outside_job(popen, ["x"], creationflags=0x200), "started")
        self.assertEqual(calls, [0x200 | osplat.CREATE_BREAKAWAY_FROM_JOB, 0x200])

    def test_loop_start_leaves_the_job_of_the_calling_agent(self):
        seen = {}

        def popen(argv, **kwargs):
            seen.update(kwargs)
        with tempfile.TemporaryDirectory() as tmp, mock.patch.object(osplat, "is_windows", return_value=True), \
                mock.patch.object(subprocess, "Popen", popen):
            loop.spawn_detached(["bzz", "start"], Path(tmp) / "x.log")
        self.assertTrue(seen["creationflags"] & osplat.CREATE_BREAKAWAY_FROM_JOB)


if __name__ == "__main__":
    unittest.main()

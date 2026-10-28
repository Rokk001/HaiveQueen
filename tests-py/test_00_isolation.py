"""Runs first (discover sorts the files): loads isolation.py before any test can touch the real data folder."""
import os
import unittest

import isolation


class IsolationActiveTest(unittest.TestCase):
    """The folders the tool falls back to are the temp ones."""

    def test_fallback_folders_are_redirected(self):
        """LOCALAPPDATA and APPDATA point into the temp folder of this session."""
        self.assertTrue(os.environ["LOCALAPPDATA"].startswith(str(isolation.ROOT)))
        self.assertTrue(os.environ["APPDATA"].startswith(str(isolation.ROOT)))


if __name__ == "__main__":
    unittest.main()

"""Runs last (discover sorts the files): the suite is red when a test wrote to the default data or settings folder."""
import unittest

import isolation


class NothingWrittenToDefaultFoldersTest(unittest.TestCase):
    """Every test uses its own BZZ_* folders."""

    def test_default_folders_stay_empty(self):
        """Nothing below the redirected %LOCALAPPDATA% / %APPDATA%: a test that fell back to the default folders would have changed the real ones."""
        self.assertEqual(isolation.leaked(), [])


if __name__ == "__main__":
    unittest.main()

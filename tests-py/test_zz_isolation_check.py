"""Runs last (discover sorts the files): the suite is red when a test wrote to the default data or settings folder."""
import unittest

import isolation


class NothingWrittenToDefaultFoldersTest(unittest.TestCase):
    """Every test uses its own BZZ_* folders."""

    def test_default_folders_stay_empty(self):
        """No payload or changed coordination fixture below redirected folders; accidental fallback writes would have changed the real ones."""
        self.assertEqual(isolation.leaked(), [])

    def test_no_write_reached_the_real_folders(self):
        """The audit guard saw no write, removal, rename or database connection of this session in the real folders, and no child process that
        would have used them. It also covers the profile settings files, which the running loop rewrites itself and the snapshot therefore leaves out."""
        self.assertEqual(isolation.guard_hits(), [])


if __name__ == "__main__":
    unittest.main()

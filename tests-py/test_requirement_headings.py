"""Reading requirements from a state file: headings with a note and lines without a colon."""
import unittest

import isolation  # noqa: F401
from haivequeen import plans


class RequirementHeadingTests(unittest.TestCase):
    def test_plain_heading_with_colon_lines(self):
        text = "# State\n\n## Requirements\n- S-1: Show the window\n- S-2: Save the file\n"
        self.assertEqual(plans.parse_requirements(text), [("S-1", "Show the window"), ("S-2", "Save the file")])

    def test_heading_with_a_note(self):
        text = "## Requirements (decisions of Mario with source)\n- C-1: Keep the cache\n"
        self.assertEqual(plans.parse_requirements(text), [("C-1", "Keep the cache")])

    def test_lines_without_a_colon(self):
        text = "## Requirements\n- B-1 Header fields: name and date\n- B-10 Ten\n"
        self.assertEqual(plans.parse_requirements(text), [("B-1", "Header fields: name and date"), ("B-10", "Ten")])

    def test_indented_sub_items_and_numbered_lines_are_not_requirements(self):
        text = "## Requirements\n- B-1 Top\n    - detail point\n      - deeper point: x\n1. Merge\n"
        self.assertEqual(plans.parse_requirements(text), [("B-1", "Top")])

    def test_other_headings_are_not_recognised(self):
        text = "## Requirements list\n- R-1: no\n\n## Requirement\n- R-2: no\n"
        self.assertEqual(plans.parse_requirements(text), [])


if __name__ == "__main__":
    unittest.main()

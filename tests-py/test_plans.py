"""Tests for orchlib.plans using invented plan files in a temp folder."""
import os
import sys
import tempfile
import unittest

sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), ".."))
from orchlib import plans  # noqa: E402


def plan_text(status="READY"):
    """Build an invented plan with two packages."""
    return (
        "+++\n"
        f'status = "{status}"\n'
        "[[packages]]\n"
        'id = "A1"\n'
        'title = "First"\n'
        'class = "heavy"\n'
        'agent = "claude"\n'
        'capability = "images"\n'
        "[[packages]]\n"
        'id = "A2"\n'
        'title = "Second"\n'
        'agent = "codex"\n'
        'depends_on = ["A1"]\n'
        "+++\n"
        "# Sample Plan\n\nText.\n"
    )


class FrontMatterTests(unittest.TestCase):
    """Header parsing."""

    def test_parse_header(self):
        """Fields of header and packages are read."""
        p = plans.parse_plan_text(plan_text(), "x.md")
        self.assertEqual(p.status, "READY")
        self.assertEqual(p.title, "Sample Plan")
        self.assertEqual([k.id for k in p.packages], ["A1", "A2"])
        self.assertEqual(p.packages[0].cls, "heavy")
        self.assertEqual(p.packages[0].capability, "images")
        self.assertEqual(p.packages[1].depends_on, ["A1"])

    def test_every_status(self):
        """All five statuses are accepted, case-insensitive."""
        for s in plans.STATUSES:
            self.assertEqual(plans.parse_plan_text(plan_text(s.lower())).status, s)

    def test_unknown_status_is_draft(self):
        """Unknown status falls back to DRAFT."""
        self.assertEqual(plans.parse_plan_text(plan_text("BOGUS")).status, "DRAFT")

    def test_missing_header_is_draft(self):
        """No header: DRAFT, no packages, title still found."""
        p = plans.parse_plan_text("# Only Text\n\nbody\n")
        self.assertEqual((p.status, p.packages, p.title), ("DRAFT", [], "Only Text"))

    def test_class_default(self):
        """Missing, empty and unknown class become medium."""
        text = ('+++\nstatus = "READY"\n[[packages]]\nid = "B1"\n'
                '[[packages]]\nid = "B2"\nclass = ""\n[[packages]]\nid = "B3"\nclass = "huge"\n+++\n# T\n')
        p = plans.parse_plan_text(text)
        self.assertEqual([k.cls for k in p.packages], ["medium"] * 3)

    def test_broken_toml_is_draft(self):
        """Invalid TOML does not raise."""
        self.assertEqual(plans.parse_plan_text("+++\nstatus = \n+++\n# T\n").status, "DRAFT")


class DiscoveryTests(unittest.TestCase):
    """Glob discovery."""

    def test_default_and_custom_glob(self):
        """Default glob finds docs/plans, custom list overrides it."""
        with tempfile.TemporaryDirectory() as d:
            os.makedirs(os.path.join(d, "docs", "plans"))
            os.makedirs(os.path.join(d, "other"))
            with open(os.path.join(d, "docs", "plans", "one.md"), "w", encoding="utf-8") as f:
                f.write(plan_text())
            with open(os.path.join(d, "other", "two.md"), "w", encoding="utf-8") as f:
                f.write(plan_text("DONE"))
            found = plans.discover_plans(d)
            self.assertEqual([p.status for p in found], ["READY"])
            self.assertTrue(found[0].path.endswith("one.md"))
            found = plans.discover_plans(d, ["other/*.md"])
            self.assertEqual([p.status for p in found], ["DONE"])

    def make_tree(self, d, names):
        """Create invented plan files at the given relative paths below d."""
        for rel in names:
            full = os.path.join(d, *rel.split("/"))
            os.makedirs(os.path.dirname(full), exist_ok=True)
            with open(full, "w", encoding="utf-8") as f:
                f.write(plan_text())

    def rels(self, d, found):
        """Workspace-relative, slash-normalised paths of the found plans."""
        return [os.path.relpath(p.path, d).replace("\\", "/") for p in found]

    def test_exclude_folder(self):
        """An exclude on a folder with ** removes every file below it."""
        with tempfile.TemporaryDirectory() as d:
            self.make_tree(d, ["docs/a.md", "docs/x/orchestrator/b.md", "docs/x/orchestrator/deep/c.md"])
            found = plans.discover_plans(d, ["docs/**/*.md"], ["docs/x/orchestrator/**"])
            self.assertEqual(self.rels(d, found), ["docs/a.md"])

    def test_exclude_file_pattern(self):
        """An exclude with a file pattern removes matching names only."""
        with tempfile.TemporaryDirectory() as d:
            self.make_tree(d, ["docs/plans/one.md", "docs/plans/one-STATE.md"])
            found = plans.discover_plans(d, ["docs/plans/*.md"], ["docs/plans/*-STATE.md"])
            self.assertEqual(self.rels(d, found), ["docs/plans/one.md"])

    def test_no_exclude_keeps_old_behaviour(self):
        """No excludes (None or empty list) returns all matches."""
        with tempfile.TemporaryDirectory() as d:
            self.make_tree(d, ["docs/plans/one.md", "docs/plans/one-STATE.md"])
            self.assertEqual(len(plans.discover_plans(d)), 2)
            self.assertEqual(len(plans.discover_plans(d, None, [])), 2)

    def test_exclude_with_broad_glob(self):
        """A broad docs/** glob combined with several excludes keeps only the rest."""
        with tempfile.TemporaryDirectory() as d:
            self.make_tree(d, ["docs/keep.md", "docs/sub/keep2.md", "docs/x/orchestrator/no.md", "docs/sub/no-STATE.md"])
            found = plans.discover_plans(d, ["docs/**"], ["docs/x/orchestrator/**", "docs/**/*-STATE.md"])
            self.assertEqual(self.rels(d, found), ["docs/keep.md", "docs/sub/keep2.md"])


class Rule11Tests(unittest.TestCase):
    """startable_packages."""

    def ids(self, text, done=()):
        """Startable ids of one plan text."""
        return [k.id for _, k in plans.startable_packages([plans.parse_plan_text(text)], set(done))]

    def test_ready_and_in_progress_start(self):
        """READY and IN_PROGRESS plans start packages without open dependencies."""
        self.assertEqual(self.ids(plan_text("READY")), ["A1"])
        self.assertEqual(self.ids(plan_text("IN_PROGRESS")), ["A1"])

    def test_other_states_start_nothing(self):
        """DRAFT, PAUSED, DONE and no header start nothing."""
        for s in ("DRAFT", "PAUSED", "DONE"):
            self.assertEqual(self.ids(plan_text(s)), [])
        self.assertEqual(self.ids("# No header\n"), [])

    def test_depends_on_done(self):
        """A dependent package starts once its dependency is done; done packages are skipped."""
        self.assertEqual(self.ids(plan_text(), ["A1"]), ["A2"])

    def test_depends_on_all(self):
        """'all' waits for every other package of the plan."""
        text = ('+++\nstatus = "READY"\n[[packages]]\nid = "C1"\n[[packages]]\nid = "C2"\n'
                '[[packages]]\nid = "C3"\ndepends_on = ["all"]\n+++\n# T\n')
        self.assertEqual(self.ids(text), ["C1", "C2"])
        self.assertEqual(self.ids(text, ["C1"]), ["C2"])
        self.assertEqual(self.ids(text, ["C1", "C2"]), ["C3"])


class LegacyTests(unittest.TestCase):
    """v1 reader."""

    TEXT = (
        "# Old Plan\nStatus: IN_PROGRESS\n\n"
        "| Package | Content | Class | Depends on |\n"
        "|---|---|---|---|\n"
        "| P1 | First | light | none |\n"
        "| P2 | Second | heavy | P1 |\n"
        "| P3 | Third | | P1-P2 |\n"
        "| P4 | Last | medium | all |\n"
    )

    def test_legacy_table(self):
        """Status line, classes, ranges and 'all' are read."""
        with tempfile.TemporaryDirectory() as d:
            path = os.path.join(d, "old.md")
            with open(path, "w", encoding="utf-8") as f:
                f.write(self.TEXT)
            p = plans.read_legacy_plan(path)
        self.assertEqual(p.status, "IN_PROGRESS")
        self.assertEqual(p.title, "Old Plan")
        self.assertEqual([k.id for k in p.packages], ["P1", "P2", "P3", "P4"])
        self.assertEqual([k.cls for k in p.packages], ["light", "heavy", "medium", "medium"])
        self.assertEqual(p.packages[2].depends_on, ["P1", "P2"])
        self.assertEqual(plans.package_dependencies(p, p.packages[3]), ["P1", "P2", "P3"])

    def test_legacy_no_status_line(self):
        """Without a Status line in line 2 the plan is DRAFT."""
        self.assertEqual(plans.legacy_status(["# T", "text"]), "DRAFT")


if __name__ == "__main__":
    unittest.main()

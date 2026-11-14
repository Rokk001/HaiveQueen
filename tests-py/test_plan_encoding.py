"""Plan mutations preserve UTF-8 encoding and untouched content."""
import codecs
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch
from haivequeen import config, db, runtime, plans


class PlanEncodingTests(unittest.TestCase):
    def setUp(self):
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        self.root = Path(tmp.name)
        p = patch.object(config, "data_dir", return_value=self.root / "data")
        p.start()
        self.addCleanup(p.stop)
        self.conn = db.connect(self.root / "test.db")
        self.addCleanup(self.conn.close)
        self.path = self.root / "plan.md"
        self.conn.execute("INSERT INTO plans(project,path,status) VALUES ('test','plan.md','DRAFT')")

    def fixture(self, bom, newline):
        text = '+++\nstatus = "DRAFT"\n[[packages]]\nid = "P1"\ntitle = "First"\n+++\n# Plan\n\nKeep caf\u00e9 and indentation:\n  unchanged\n'
        raw = text.replace("\n", newline).encode("utf-8")
        self.path.write_bytes((codecs.BOM_UTF8 if bom else b"") + raw)
        return self.path.read_bytes()

    def test_status_preserves_encoding_and_body(self):
        for bom in (True, False):
            for newline in ("\r\n", "\n"):
                with self.subTest(bom=bom, newline=repr(newline)):
                    before = self.fixture(bom, newline)
                    runtime.set_plan_status(self.conn, "test", "plan.md", "RUNNING", self.root)
                    self.assertEqual(self.path.read_bytes(), before.replace(b'status = "DRAFT"', b'status = "RUNNING"'))
                    self.assertEqual(self.conn.execute("SELECT status FROM plans").fetchone()[0], "RUNNING")
                    self.assertEqual(plans.load_plan(self.path).status, "RUNNING")

    def test_header_insertion_and_replacement_preserve_body(self):
        for bom in (True, False):
            for newline in ("\r\n", "\n"):
                with self.subTest(bom=bom, newline=repr(newline)):
                    before = self.fixture(bom, newline)
                    runtime.set_plan_header(self.conn, "test", "plan.md", "fix/one", "work-one", self.root)
                    expected = before.replace(b'status = "DRAFT"', ('status = "DRAFT"' + newline + 'worktree = "work-one"' + newline + 'branch = "fix/one"').encode())
                    self.assertEqual(self.path.read_bytes(), expected)
                    runtime.set_plan_header(self.conn, "test", "plan.md", "fix/two", "work-two", self.root)
                    self.assertEqual(self.path.read_bytes(), expected.replace(b'fix/one', b'fix/two').replace(b'work-one', b'work-two'))
                    self.assertEqual(plans.load_plan(self.path).branch, "fix/two")

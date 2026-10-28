"""Tests of orch init and orch register."""
import json
import os
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
from orchlib import db  # noqa: E402


class InitTest(unittest.TestCase):
    """init never overwrites; register is idempotent."""

    def setUp(self):
        """Create temp folders and the environment."""
        self.tmp = tempfile.TemporaryDirectory()
        root = Path(self.tmp.name)
        self.ws = root / "myproj"
        self.env = dict(os.environ, ORCH_CONFIG_DIR=str(root / "cfg"), ORCH_DATA_DIR=str(root / "data"))

    def tearDown(self):
        """Remove the temp folder."""
        self.tmp.cleanup()

    def orch(self, *args):
        """Run orch.py; return the parsed JSON output."""
        r = subprocess.run([sys.executable, "-I", str(ROOT / "orch.py"), *args], env=self.env,
                           capture_output=True, text=True, encoding="utf-8")
        self.assertEqual(r.returncode, 0, r.stdout + r.stderr)
        return json.loads(r.stdout)

    def test_init_creates_files(self):
        """A fresh folder gets all files and the folder name as project name."""
        res = self.orch("init", str(self.ws))
        self.assertTrue(all(f["result"] == "created" for f in res["files"]))
        self.assertIn('name = "myproj"', (self.ws / ".orchestrator" / "project.toml").read_text(encoding="utf-8"))
        self.assertIn("AGENTS.md", (self.ws / "CLAUDE.md").read_text(encoding="utf-8"))
        self.assertTrue((self.ws / "AGENTS.md").is_file())
        self.assertTrue((self.ws / "docs" / "plans").is_dir())

    def test_init_overwrites_nothing(self):
        """Existing files stay byte for byte, and are reported as kept."""
        self.ws.mkdir()
        (self.ws / "CLAUDE.md").write_text("mine\n", encoding="utf-8")
        (self.ws / "AGENTS.md").write_text("my rules\n", encoding="utf-8")
        res = self.orch("init", str(self.ws))
        kept = {Path(f["path"]).name for f in res["files"] if f["result"] == "exists, kept"}
        self.assertEqual(kept, {"CLAUDE.md", "AGENTS.md"})
        self.assertEqual((self.ws / "CLAUDE.md").read_text(encoding="utf-8"), "mine\n")
        self.assertEqual((self.ws / "AGENTS.md").read_text(encoding="utf-8"), "my rules\n")
        (self.ws / ".orchestrator" / "project.toml").write_text('name = "changed"\n', encoding="utf-8")
        res = self.orch("init", str(self.ws))
        self.assertTrue(all(f["result"] == "exists, kept" for f in res["files"]))
        self.assertEqual((self.ws / ".orchestrator" / "project.toml").read_text(encoding="utf-8"),
                         'name = "changed"\n')

    def test_register_idempotent(self):
        """Registering twice gives one config entry and one projects row."""
        self.orch("init", str(self.ws))
        first = self.orch("register", str(self.ws))
        second = self.orch("register", str(self.ws))
        self.assertEqual((first["config"], second["config"]), ("added", "kept"))
        cfg = (Path(self.env["ORCH_CONFIG_DIR"]) / "config.toml").read_text(encoding="utf-8")
        self.assertEqual(cfg.count("[[projects]]"), 1)
        conn = db.connect(Path(self.env["ORCH_DATA_DIR"]) / "orchestrator.db")
        rows = db.rows(conn, "SELECT * FROM projects")
        conn.close()
        self.assertEqual([r["name"] for r in rows], ["myproj"])
        self.assertEqual(rows[0]["colour"], "cyan")


if __name__ == "__main__":
    unittest.main()

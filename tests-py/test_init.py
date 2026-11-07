"""Tests of bzz init and bzz register."""
import json
import os
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
from haivequeen import db  # noqa: E402


class InitTest(unittest.TestCase):
    """init never overwrites; register is idempotent."""

    def setUp(self):
        """Create temp folders and the environment."""
        self.tmp = tempfile.TemporaryDirectory()
        root = Path(self.tmp.name)
        self.ws = root / "myproj"
        self.env = dict(os.environ, BZZ_CONFIG_DIR=str(root / "cfg"), BZZ_DATA_DIR=str(root / "data"))

    def tearDown(self):
        """Remove the temp folder."""
        self.tmp.cleanup()

    def orch(self, *args):
        """Run haivequeen/cli.py; return the parsed JSON output."""
        r = subprocess.run([sys.executable, "-I", str(ROOT / "haivequeen" / "cli.py"), *args], env=self.env,
                           capture_output=True, text=True, encoding="utf-8")
        self.assertEqual(r.returncode, 0, r.stdout + r.stderr)
        return json.loads(r.stdout)

    def test_init_creates_files(self):
        """A fresh folder gets all files and the folder name as project name."""
        res = self.orch("init", str(self.ws))
        self.assertTrue(all(f["result"] == "created" for f in res["files"]))
        self.assertIn('name = "myproj"', (self.ws / ".haivequeen" / "project.toml").read_text(encoding="utf-8"))
        self.assertIn("AGENTS.md", (self.ws / "CLAUDE.md").read_text(encoding="utf-8"))
        self.assertTrue((self.ws / "AGENTS.md").is_file())
        self.assertTrue((self.ws / "docs" / "plans").is_dir())
        from haivequeen import plans
        example = self.ws / "docs" / "plans" / "EXAMPLE-PLAN.md"
        text = example.read_text(encoding="utf-8")
        plan = plans.parse_plan_text(text)
        self.assertEqual(plan.status, "DRAFT")
        self.assertEqual(len(plan.packages), 1)
        self.assertEqual([rid for rid, _ in plans.parse_requirements(text)], ["E-1", "E-2"])
        self.assertIn("Feature branch:", text)

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
        example = self.ws / "docs" / "plans" / "EXAMPLE-PLAN.md"
        example.write_text("my plan\n", encoding="utf-8")
        (self.ws / ".haivequeen" / "project.toml").write_text('name = "changed"\n', encoding="utf-8")
        res = self.orch("init", str(self.ws))
        self.assertTrue(all(f["result"] == "exists, kept" for f in res["files"]))
        self.assertEqual(example.read_text(encoding="utf-8"), "my plan\n")
        self.assertEqual((self.ws / ".haivequeen" / "project.toml").read_text(encoding="utf-8"),
                         'name = "changed"\n')

    def test_gitignore_missing_and_existing_contents(self):
        """Init creates the ignore file or preserves its existing bytes when appending."""
        result = self.orch("init", str(self.ws))
        ignore = self.ws / ".gitignore"
        self.assertEqual(ignore.read_bytes(), b".haivequeen/\n")
        self.assertIn(".haivequeen/", json.dumps(result))
        ignore.write_bytes(b"# keep\r\n*.tmp")
        result = self.orch("init", str(self.ws))
        self.assertEqual(ignore.read_bytes(), b"# keep\r\n*.tmp\n.haivequeen/\n")
        self.assertIn(".haivequeen/", json.dumps(result))

    def test_gitignore_existing_line_unchanged(self):
        """An existing exact entry is kept byte for byte, including line endings."""
        self.ws.mkdir()
        ignore = self.ws / ".gitignore"
        original = b"# keep\r\n.haivequeen/\r\n*.tmp"
        ignore.write_bytes(original)
        self.orch("init", str(self.ws))
        self.assertEqual(ignore.read_bytes(), original)

    def test_register_adds_gitignore_for_existing_project(self):
        """Register adds the entry and reports it, and repeating it does not duplicate it."""
        self.ws.mkdir()
        ignore = self.ws / ".gitignore"
        ignore.write_bytes(b"other\n")
        result = self.orch("register", str(self.ws))
        self.assertIn(".haivequeen/", json.dumps(result))
        self.orch("register", str(self.ws))
        self.assertEqual(ignore.read_bytes(), b"other\n.haivequeen/\n")

    def test_tracked_folder_warns_without_changing_index(self):
        """Both commands warn for tracked files and leave the index unchanged."""
        self.ws.mkdir()
        subprocess.run(["git", "init", "-q", str(self.ws)], check=True)
        folder = self.ws / ".haivequeen"
        folder.mkdir()
        (folder / "project.toml").write_text('name = "myproj"\n', encoding="utf-8")
        subprocess.run(["git", "-C", str(self.ws), "add", ".haivequeen/project.toml"], check=True)
        before = subprocess.check_output(["git", "-C", str(self.ws), "ls-files", "--stage"])
        for command in ("init", "register"):
            result = self.orch(command, str(self.ws))
            message = json.dumps(result).lower()
            self.assertIn(".haivequeen", message)
            self.assertIn("remove", message)
            self.assertIn("git", message)
            self.assertEqual(subprocess.check_output(["git", "-C", str(self.ws), "ls-files", "--stage"]), before)

    def test_register_idempotent(self):
        """Registering twice gives one config entry and one projects row."""
        self.orch("init", str(self.ws))
        first = self.orch("register", str(self.ws))
        second = self.orch("register", str(self.ws))
        self.assertEqual((first["config"], second["config"]), ("added", "kept"))
        cfg = (Path(self.env["BZZ_CONFIG_DIR"]) / "config.toml").read_text(encoding="utf-8")
        self.assertEqual(cfg.count("[[projects]]"), 1)
        conn = db.connect(Path(self.env["BZZ_DATA_DIR"]) / "haivequeen.db")
        rows = db.rows(conn, "SELECT * FROM projects")
        conn.close()
        self.assertEqual([r["name"] for r in rows], ["myproj"])
        self.assertEqual(rows[0]["colour"], "cyan")




class ExampleProjectFileTest(unittest.TestCase):
    """examples/project.example.toml documents the compiler process names (C8)."""

    def test_example_has_build_process_names(self):
        """The example parses and lists cl.exe in buildProcessNames."""
        import tomllib
        path = Path(__file__).resolve().parent.parent / "examples" / "project.example.toml"
        data = tomllib.loads(path.read_text(encoding="utf-8"))
        self.assertEqual(data["buildProcessNames"], ["cl.exe"])


if __name__ == "__main__":
    unittest.main()

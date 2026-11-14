import io
import tempfile
import unittest
import zipfile
from pathlib import Path

from haivequeen import release_staging


def archive(entries):
    stream = io.BytesIO()
    with zipfile.ZipFile(stream, "w") as out:
        for name, contents in entries:
            out.writestr(name, contents)
    return stream.getvalue()


class ReleaseStagingTests(unittest.TestCase):
    def test_stages_release_without_changing_active_version(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            (root / "active-version").write_text("old\n", encoding="utf-8")
            urls = []
            def fetch(url):
                urls.append(url)
                return archive([("project-tag/haivequeen/cli.py", "new code")])
            installed = release_staging.stage_release(root, "example/project", "2.0.0", fetch)
            self.assertEqual((installed / "haivequeen/cli.py").read_text(), "new code")
            self.assertEqual((root / "active-version").read_text(), "old\n")
            self.assertTrue(urls[0].endswith("/example/project/zipball/v2.0.0"))

    def test_rejects_archive_path_traversal(self):
        with tempfile.TemporaryDirectory() as temp:
            data = archive([("project/haivequeen/cli.py", "code"), ("project/../../escape", "bad")])
            with self.assertRaises(ValueError):
                release_staging.stage_release(temp, "example/project", "2.0.0", lambda _: data)
            self.assertFalse((Path(temp) / "versions/2.0.0").exists())

    def test_existing_version_is_never_overwritten(self):
        with tempfile.TemporaryDirectory() as temp:
            data = archive([("project/haivequeen/cli.py", "code")])
            release_staging.stage_release(temp, "example/project", "2.0.0", lambda _: data)
            with self.assertRaises(FileExistsError):
                release_staging.stage_release(temp, "example/project", "2.0.0", lambda _: data)

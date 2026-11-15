import io
import tempfile
import unittest
import zipfile
from pathlib import Path
from unittest.mock import patch

from haivequeen import dashboard_data, release_staging, releases, selfupdate, updates, version_layout


def archive(entries):
    stream = io.BytesIO()
    with zipfile.ZipFile(stream, "w") as out:
        for name, contents in entries:
            out.writestr(name, contents)
    return stream.getvalue()


class ReleaseStagingTests(unittest.TestCase):
    def test_running_identity_drives_display_and_release_comparison_across_rollback(self):
        with tempfile.TemporaryDirectory() as temp:
            data = archive([('project/haivequeen/cli.py', 'code'),
                            ('project/pyproject.toml', '[project]\nversion = "2.0.0"\n')])
            old = release_staging.stage_release(temp, 'example/project', '2.1.0', lambda _: data)
            new = release_staging.stage_release(temp, 'example/project', '2.2.0', lambda _: data)

            class Response(io.BytesIO):
                pass

            def latest(_request, timeout):
                return Response(b'{"tag_name":"v2.2.0","html_url":"release"}')

            with patch.object(releases, 'urlopen', latest), \
                 patch.object(updates, 'state_path', return_value=Path(temp) / 'check.json'), \
                 patch.object(selfupdate, 'live_folder', return_value=old):
                for active in ('2.2.0', '2.1.0'):
                    version_layout.set_active(temp, active)
                    dashboard_data._VERSION_CACHE.clear()
                    self.assertEqual(dashboard_data._version(), '2.1.0')
                    state = updates.check({'update': {'repository': 'example/project'}}, manual=True)
                    self.assertEqual(state['available']['version'], '2.2.0')
                with patch.object(selfupdate, 'live_folder', return_value=new):
                    dashboard_data._VERSION_CACHE.clear()
                    self.assertEqual(dashboard_data._version(), '2.2.0')
                    state = updates.check({'update': {'repository': 'example/project'}}, manual=True)
                    self.assertIsNone(state['available'])
            dashboard_data._VERSION_CACHE.clear()

    def test_staged_identity_survives_activation_and_rollback(self):
        with tempfile.TemporaryDirectory() as temp:
            data = archive([('project/haivequeen/cli.py', 'code'),
                            ('project/pyproject.toml', '[project]\nversion = "2.0.0"\n'),
                            ('project/.haivequeen-release-version', '9.9.9')])
            fetch = lambda _: data
            old = release_staging.stage_release(temp, 'example/project', '2.1.0', fetch)
            new = release_staging.stage_release(temp, 'example/project', '2.2.0', fetch)
            no_git = lambda *args: (1, '')
            version_layout.set_active(temp, '2.2.0')
            self.assertEqual(updates.installed_version(old, no_git), '2.1.0')
            self.assertEqual(updates.installed_version(new, no_git), '2.2.0')
            version_layout.set_active(temp, '2.1.0')
            self.assertEqual(updates.installed_version(old, no_git), '2.1.0')
            self.assertEqual(updates.installed_version(new, no_git), '2.2.0')

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

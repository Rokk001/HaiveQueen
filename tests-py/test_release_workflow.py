"""Focused checks for tag releases and stable update selection."""
import json
import os
from io import BytesIO
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest
from unittest import mock

from haivequeen import releases


WORKFLOW = Path(__file__).resolve().parents[1] / '.github' / 'workflows' / 'release.yml'


class ReleaseWorkflowTests(unittest.TestCase):
    def setUp(self):
        self.workflow = WORKFLOW.read_text(encoding='utf-8-sig')

    def test_build_and_publish_configuration(self):
        for part in ("tags:\n      - 'v*'", 'contents: write', 'actions/checkout@v4',
                     'setuptools.build_meta.build_sdist', 'python -m pip wheel --no-deps', 'gh release view "$RELEASE_TAG"',
                     'gh release create "$RELEASE_TAG" --verify-tag',
                     'prerelease=(--prerelease)', 'gh release upload "$RELEASE_TAG" dist/* --clobber'):
            self.assertIn(part, self.workflow)

    def test_tag_version_is_used_for_package_and_invalid_tags_fail(self):
        block = self.workflow.split("          python - <<'PY'\n", 1)[1].split('\n          PY', 1)[0]
        script = '\n'.join(line[10:] for line in block.splitlines())
        for tag, expected in [('v2.3.4', '2.3.4'), ('v2.3.5-rc.1', '2.3.5-rc.1'),
                              ('v2.3.5-beta.2', '2.3.5-beta.2'), ('v2.3.5-alpha.1', '2.3.5-alpha.1'),
                              ('v2.3.5-dev.1', '2.3.5-dev.1'),
                              ('v2.3.5-nightly.2', '2.3.5.dev2+nightly'),
                              ('v2.3', None), ('v2.3.5-!.1', None)]:
            with self.subTest(tag=tag), tempfile.TemporaryDirectory() as folder:
                project = Path(folder) / 'pyproject.toml'
                project.write_text('[project]\nversion = "2.0.0"\n', encoding='utf-8')
                env = dict(os.environ, RELEASE_TAG=tag)
                result = subprocess.run([sys.executable, '-c', script], cwd=folder, env=env, capture_output=True)
                self.assertEqual(result.returncode == 0, expected is not None, result.stderr)
                if expected:
                    self.assertIn('version = "' + expected + '"', project.read_text(encoding='utf-8'))


class StableUpdateSelectionTests(unittest.TestCase):
    def test_latest_endpoint_and_prerelease_tag_not_offered(self):
        for tag, expected in [('v2.1.0-rc.1', None), ('v2.1.0', '2.1.0')]:
            response = BytesIO(json.dumps({'tag_name': tag}).encode())
            with self.subTest(tag=tag), mock.patch('haivequeen.releases.urlopen', return_value=response) as fetch:
                found = releases.check_latest('example/app', '2.0.0')
                self.assertEqual(found and found['version'], expected)
                self.assertEqual(fetch.call_args.args[0].full_url,
                                 'https://api.github.com/repos/example/app/releases/latest')


if __name__ == '__main__':
    unittest.main()

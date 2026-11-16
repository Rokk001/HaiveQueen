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
        for part in ("tags:\n      - 'v*'", 'contents: write', 'actions/checkout@11d5960a326750d5838078e36cf38b85af677262',
                     'setuptools.build_meta.build_sdist', 'python -m pip wheel --no-deps', 'gh release view "$RELEASE_TAG"',
                     'gh release create "$RELEASE_TAG" --verify-tag',
                     'prerelease=(--prerelease)', 'gh release upload "$RELEASE_TAG" dist/* --clobber'):
            self.assertIn(part, self.workflow)

    def test_full_release_job_does_not_run_distribution_build(self):
        prerelease, full = self.workflow.split('  full-release:', 1)
        self.assertIn("if: github.event_name == 'push' && contains(github.ref_name, '-')", prerelease)
        self.assertIn("if: ${{ github.event_name == 'workflow_dispatch' || !contains(github.ref_name, '-') }}", full)
        self.assertNotIn('Build distributions', full)
        self.assertNotIn('pip wheel', full)
        self.assertIn('gh release create "$RELEASE_TAG" --verify-tag', full)
        self.assertIn('gh release download "$source" --dir dist', full)
        self.assertIn('Pre-release and full release commits differ', full)

    def test_maintenance_tags_skip_publishing_but_annotated_releases_continue(self):
        full = self.workflow.split('  full-release:', 1)[1]
        block = full.split("          python - <<'PY'\n", 1)[1].split('\n          PY', 1)[0]
        script = '\n'.join(line[10:] for line in block.splitlines())
        self.assertIn("if: steps.tag-kind.outputs.publish == 'true'", full)
        with tempfile.TemporaryDirectory() as folder:
            def git(*args):
                subprocess.run(['git', *args], cwd=folder, check=True, capture_output=True)
            git('init')
            git('-c', 'user.name=Test', '-c', 'user.email=test@example.invalid',
                'commit', '--allow-empty', '-m', 'Fixture')
            git('tag', 'v0.2.24')
            git('-c', 'user.name=Test', '-c', 'user.email=test@example.invalid',
                'tag', '-a', 'v0.2.25', '-m', 'HaiveQueen prerelease: v0.2.25-rc.1')
            output = Path(folder) / 'output'
            summary = Path(folder) / 'summary'
            for tag, explicit, expected in [('v0.2.24', 'false', 'false'), ('v0.2.25', 'false', 'true'), ('v0.2.24', 'true', 'true')]:
                output.write_text('')
                result = subprocess.run([sys.executable, '-c', script], cwd=folder,
                    env=dict(os.environ, RELEASE_TAG=tag, EXPLICIT_PROMOTION=explicit, GITHUB_OUTPUT=str(output),
                             GITHUB_STEP_SUMMARY=str(summary)), capture_output=True)
                self.assertEqual(result.returncode, 0, result.stderr)
                self.assertEqual(output.read_text().strip(), 'publish=' + expected)
            self.assertIn('Maintenance tag', summary.read_text())

    def test_explicit_promotion_checks_source_and_commit_before_copying(self):
        import shutil
        git_path = Path(shutil.which('git'))
        bash = git_path.parent.parent / 'bin' / 'bash.exe' if os.name == 'nt' else Path(shutil.which('bash') or '/missing')
        if not bash.is_file(): self.skipTest('Bash required for workflow regression')
        full = self.workflow.split('      - name: Publish full release without rebuilding', 1)[1]
        block = full.split('        run: |\n', 1)[1]
        script = '\n'.join(line[10:] for line in block.splitlines())
        for mismatch in (False, True):
            with self.subTest(mismatch=mismatch), tempfile.TemporaryDirectory() as folder:
                def git(*args):
                    subprocess.run(['git', *args], cwd=folder, check=True, capture_output=True)
                git('init')
                git('-c','user.name=Test','-c','user.email=test@example.invalid','commit','--allow-empty','-m','fixture')
                git('tag','v1.2.3')
                if mismatch:
                    git('-c','user.name=Test','-c','user.email=test@example.invalid','commit','--allow-empty','-m','other')
                git('tag','v1.2.3-alpha.1')
                stub = """set -e
 gh() {
   if [[ "$1 $2" == 'release view' ]]; then
     if [[ "$3" == "$SOURCE_TAG" ]]; then echo true; return 0; fi
     return 1
   fi
   echo "$1 $2" >> calls
   if [[ "$2" == download ]]; then echo fixture > dist/build.zip; fi
 }
"""
                path = Path(folder)/'workflow.sh';path.write_text(stub+script,encoding='utf-8')
                result = subprocess.run([str(bash),str(path)],cwd=folder,env=dict(os.environ,RELEASE_TAG='v1.2.3',SOURCE_TAG='v1.2.3-alpha.1'),capture_output=True,text=True)
                calls=Path(folder)/'calls'
                if mismatch:
                    self.assertNotEqual(result.returncode,0)
                    self.assertIn('commits differ',result.stderr)
                    self.assertFalse(calls.exists())
                else:
                    self.assertEqual(result.returncode,0,result.stderr)
                    self.assertEqual(calls.read_text().splitlines(),['release download','release create','release upload'])

    def test_tag_version_is_used_for_package_and_invalid_tags_fail(self):
        block = self.workflow.split("          python - <<'PY'\n", 1)[1].split('\n          PY', 1)[0]
        script = '\n'.join(line[10:] for line in block.splitlines())
        for tag, expected in [('v2.3.4', '2.3.4'), ('v2.3.5-rc.1', '2.3.5-rc.1'),
                              ('v2.3.5-beta.2', '2.3.5-beta.2'), ('v2.3.5-alpha.1', '2.3.5-alpha.1'),
                              ('v2.3.5-dev.1', '2.3.5-dev.1'),
                              ('v2.3.5-nightly.2', '2.3.5.dev2+nightly'),
                              ('v2.3.5-1preview.3', '2.3.5.dev3+1preview'),
                              ('v2.3.5-pre-release.4', '2.3.5.dev4+pre-release'),
                              ('v2.3.5-1-preview.5', '2.3.5.dev5+1-preview'),
                              ('v2.3.5-pre--release.1', None), ('v2.3.5-pre-.1', None),
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

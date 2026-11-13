import importlib
import sys
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest import mock

from haivequeen import git_provider


class ProviderDiscoveryTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.root = Path(self.tmp.name)
        self.package_dir = self.root / 'test_git_modules'
        self.package_dir.mkdir()
        (self.package_dir / '__init__.py').write_text('', encoding='utf-8')
        sys.path.insert(0, str(self.root))
        self.package = importlib.import_module('test_git_modules')

    def tearDown(self):
        sys.path.remove(str(self.root))
        for name in list(sys.modules):
            if name == 'test_git_modules' or name.startswith('test_git_modules.'):
                del sys.modules[name]
        self.tmp.cleanup()

    def module(self, name, text):
        (self.package_dir / (name + '.py')).write_text(text, encoding='utf-8')

    def test_added_module_is_discovered_and_called(self):
        self.assertEqual(git_provider.discover('example:team/repo', self.package), (None, []))
        self.module('example', "CAPABILITIES = {'release_list'}\ndef detect(remote):\n    return remote[8:] if remote.startswith('example:') else None\ndef release_list(repo):\n    return [repo]\n")
        provider, errors = git_provider.discover('example:team/repo', self.package)
        self.assertEqual(errors, [])
        self.assertEqual(provider.name, 'example')
        self.assertEqual(provider.capabilities, {'release_list'})
        self.assertEqual(provider.call('release_list'), ['team/repo'])
        with self.assertRaisesRegex(git_provider.ProviderError, 'does not support pipeline'):
            provider.call('pipeline')

    def test_faulty_modules_report_errors_and_others_still_work(self):
        self.module('broken_import', "raise RuntimeError('bad import')\n")
        self.module('broken_contract', "CAPABILITIES = {'missing'}\ndef detect(remote): return None\n")
        self.module('broken_detect', "CAPABILITIES = set()\ndef detect(remote): raise ValueError('bad remote')\n")
        self.module('working', "CAPABILITIES = {'release'}\ndef detect(remote): return 'repo'\ndef release(repo): raise ValueError('bad release')\n")
        provider, errors = git_provider.discover('remote', self.package)
        self.assertEqual(provider.name, 'working')
        self.assertEqual(len(errors), 3)
        self.assertTrue(any('broken_import' in e and 'bad import' in e for e in errors))
        self.assertTrue(any('broken_contract' in e and 'missing' in e for e in errors))
        self.assertTrue(any('broken_detect' in e and 'bad remote' in e for e in errors))
        with self.assertRaisesRegex(git_provider.ProviderError, 'working: release failed: bad release'):
            provider.call('release')
        self.assertEqual(git_provider.discover('remote', self.package)[0].name, 'working')

    def test_project_uses_code_repository_and_configured_remote(self):
        env = SimpleNamespace(workspace=self.root, code_repo=self.root / 'code', settings={'remoteName': 'upstream'})
        run = mock.Mock(return_value=(0, 'example:team/repo\n'))
        with mock.patch.object(git_provider, 'discover', return_value=('provider', [])) as discover:
            self.assertEqual(git_provider.for_project(env, run), ('provider', []))
        self.assertEqual(run.call_args.args[0][-1], 'upstream')
        self.assertEqual(run.call_args.args[1], self.root / 'code')
        discover.assert_called_once_with('example:team/repo')
        self.assertIn('cannot read Git remote upstream', git_provider.for_project(env, lambda *a: (1, 'missing'))[1][0])


if __name__ == '__main__':
    unittest.main()

import hashlib
from pathlib import Path
from tempfile import TemporaryDirectory
from types import SimpleNamespace
from unittest import TestCase, mock
from haivequeen import publish


class ReleaseFiles(TestCase):
    def test_empty_folder_makes_no_release(self):
        with TemporaryDirectory() as tmp:
            result = publish.pipeline_release(None, SimpleNamespace(settings={}), 'plan', {}, tmp,
                                              {'releaseMode': 'local', 'releaseFolder': 'out'})
        self.assertIn('no release', result)

    def test_uploads_unchanged_and_cleans_after_success(self):
        with TemporaryDirectory() as tmp:
            path = Path(tmp) / 'artifact.zip'
            path.write_bytes(b'original bytes')
            env = SimpleNamespace(name='project', settings={})
            state = {'merged': 'a' * 40}
            provider = mock.Mock(name='provider')
            provider.name = 'github'
            provider.capabilities = {'release_prepare', 'release_upload', 'release_asset_digest'}
            provider.repository = 'team/repo'
            provider.call.side_effect = lambda op, *args: ({'id': 1, 'assets': []} if op == 'release_prepare' else
                hashlib.sha256(b'original bytes').hexdigest() if op == 'release_asset_digest' else {})
            settings = {'releaseMode': 'local', 'releaseFolder': tmp}
            with mock.patch('haivequeen.git_provider.for_project', return_value=(provider, [])), \
                 mock.patch('haivequeen.orchestrator.save'), \
                 mock.patch.object(publish, '_release_tag', return_value='v1.0.0-dev.1'):
                self.assertIn('published', publish.pipeline_release(None, env, 'plan', state, tmp, settings))
            self.assertFalse(path.exists())
            self.assertEqual(provider.call.call_args_list[1].args[2:4], ('artifact.zip', b'original bytes'))
    def test_provider_waits_for_tag_workflow_and_failure(self):
        env = SimpleNamespace(name='project', settings={})
        state = {'merged': 'a' * 40, 'release': {'tag': 'v1.0.0-dev.1', 'commit': 'a' * 40, 'tagged': True}}
        provider = mock.Mock()
        provider.name = 'github'
        provider.repository = 'team/repo'
        provider.capabilities = {'release_tag', 'release_state'}
        run = {'head_sha': 'a' * 40, 'head_branch': 'v1.0.0-dev.1', 'event': 'push', 'status': 'in_progress'}
        provider.call.side_effect = lambda *args: 'pending' if run['status'] != 'completed' else 'success' if run.get('conclusion') == 'success' else 'failure'
        with mock.patch('haivequeen.git_provider.for_project', return_value=(provider, [])):
            self.assertIsNone(publish.pipeline_release(None, env, 'plan', state, '.', {'releaseMode': 'git'}))
            run.update(status='completed', conclusion='failure')
            with self.assertRaisesRegex(ValueError, 'failed'):
                publish.pipeline_release(None, env, 'plan', state, '.', {'releaseMode': 'git'})
            run['conclusion'] = 'success'
            self.assertIn('completed', publish.pipeline_release(None, env, 'plan', state, '.', {'releaseMode': 'git'}))

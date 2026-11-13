"""Release adapter regression tests with provider-specific response shapes."""
import hashlib
from types import SimpleNamespace
from unittest import TestCase, mock

from haivequeen import git_provider, publish
from haivequeen.git_providers import azure, bitbucket, gitea, github, gitlab


COMMIT = 'a' * 40
TAG = 'v1.2.3-dev.1'


class ReleaseAdapters(TestCase):
    def test_all_capable_modules_advertise_normalized_release_operations(self):
        for module in (github, gitlab, azure, gitea):
            with self.subTest(provider=module.__name__):
                self.assertTrue({'release_tag', 'release_state'} <= module.CAPABILITIES)
        for module in (github, gitlab, gitea):
            with self.subTest(provider=module.__name__):
                self.assertTrue({'release_prepare', 'release_upload', 'release_asset_digest'} <= module.CAPABILITIES)
        self.assertNotIn('release_state', bitbucket.CAPABILITIES)
        self.assertNotIn('release_prepare', azure.CAPABILITIES)

    def test_github_paginates_filters_branch_runs_and_uses_latest_attempt(self):
        unrelated = {'head_sha': COMMIT, 'head_branch': 'main', 'event': 'push',
                     'status': 'completed', 'conclusion': 'success', 'id': 1, 'workflow_id': 1}
        failed = dict(unrelated, head_branch=TAG, id=2, conclusion='failure')
        green = dict(failed, id=3, conclusion='success')
        with mock.patch.object(github, '_request', side_effect=[{'workflow_runs': [unrelated] * 99 + [failed]},
                                                               {'workflow_runs': [green]}]) as request:
            self.assertEqual(github.release_state('example/repo', TAG, COMMIT, 'secret'), 'success')
        self.assertIn('page=2', request.call_args.args[1])
        with mock.patch.object(github, '_request', return_value={'workflow_runs': [unrelated]}):
            self.assertEqual(github.release_state('example/repo', TAG, COMMIT, 'secret'), 'pending')

    def test_gitea_does_not_accept_commit_status_for_tag_workflow(self):
        unrelated = {'head_sha': COMMIT, 'head_branch': 'main', 'event': 'push',
                     'status': 'completed', 'conclusion': 'success'}
        with mock.patch.object(gitea, '_request', return_value={'workflow_runs': [unrelated]}):
            self.assertEqual(gitea.release_state(('https://gitea.example', 'team', 'repo'), TAG, COMMIT), 'pending')
        for conclusion, expected in [('failure', 'failure'), ('success', 'success'), (None, 'pending')]:
            with self.subTest(conclusion=conclusion), mock.patch.object(gitea, '_request', return_value={
                'workflow_runs': [dict(unrelated, head_branch='refs/tags/' + TAG, conclusion=conclusion)]}):
                self.assertEqual(gitea.release_state(('https://gitea.example', 'team', 'repo'), TAG, COMMIT), expected)

    def test_gitlab_filters_exact_tag_sha_and_push_source(self):
        for row, expected in [({'sha': COMMIT, 'ref': 'main', 'source': 'push', 'status': 'success'}, 'pending'),
                              ({'sha': COMMIT, 'ref': TAG, 'source': 'web', 'status': 'success'}, 'pending'),
                              ({'sha': 'b' * 40, 'ref': TAG, 'source': 'push', 'status': 'success'}, 'pending'),
                              ({'sha': COMMIT, 'ref': TAG, 'source': 'push', 'status': 'running'}, 'pending'),
                              ({'sha': COMMIT, 'ref': TAG, 'source': 'push', 'status': 'failed'}, 'failure'),
                              ({'sha': COMMIT, 'ref': TAG, 'source': 'push', 'status': 'success'}, 'success')]:
            with self.subTest(row=row), mock.patch.object(gitlab, '_request', return_value=([row], '')) as request:
                self.assertEqual(gitlab.release_state('example/repo', TAG, COMMIT), expected)
                self.assertEqual(request.call_args.kwargs['query']['ref'], TAG)

    def test_azure_requires_exact_tag_commit_and_all_latest_definitions(self):
        base = {'id': 1, 'definition': {'id': 1}, 'sourceVersion': COMMIT, 'sourceBranch': 'refs/tags/' + TAG,
                'status': 'completed', 'result': 'succeeded'}
        unrelated = dict(base, sourceBranch='refs/heads/main')
        with mock.patch.object(azure, '_request', return_value=({'value': [unrelated]}, None)):
            self.assertEqual(azure.release_state(('org', 'project', 'repo'), TAG, COMMIT), 'pending')
        with mock.patch.object(azure, '_request', side_effect=[
            ({'value': [dict(base, result='failed'), dict(base, id=2)]}, 'next'),
            ({'value': [dict(base, id=3, definition={'id': 2}, status='inProgress', result=None)]}, None)]):
            self.assertEqual(azure.release_state(('org', 'project', 'repo'), TAG, COMMIT), 'pending')
        with mock.patch.object(azure, '_request', return_value=({'value': [base]}, None)):
            self.assertEqual(azure.release_state(('org', 'project', 'repo'), TAG, COMMIT), 'success')

    def test_idempotent_tag_creation_and_wrong_commit_rejection(self):
        cases = [(github, 'example/repo', {'object': {'type': 'commit', 'sha': COMMIT}}),
                 (gitlab, 'example/repo', ({'commit': {'id': COMMIT}}, '')),
                 (gitea, ('https://gitea.example', 'team', 'repo'), {'commit': {'sha': COMMIT}}),
                 (azure, ('org', 'project', 'repo'), {'value': [{'name': 'refs/tags/' + TAG, 'objectId': COMMIT}]})]
        for module, repo, existing in cases:
            with self.subTest(provider=module.__name__), mock.patch.object(module, '_request', return_value=existing), \
                 mock.patch.object(module, 'tag') as tag:
                module.release_tag(repo, TAG, COMMIT, 'secret')
                tag.assert_not_called()
                with self.assertRaisesRegex(ValueError, 'different commit'):
                    module.release_tag(repo, TAG, 'b' * 40, 'secret')

    def test_github_verifies_legacy_assets_without_digest_by_downloading(self):
        with mock.patch.object(github, '_request', return_value=b'original') as request:
            self.assertEqual(github.release_asset_digest('example/repo', {}, {'id': 2, 'state': 'uploaded'}),
                             hashlib.sha256(b'original').hexdigest())
            self.assertTrue(request.call_args.kwargs['raw'])

    def test_github_removes_failed_upload_placeholders_but_keeps_completed_assets(self):
        ready = {'id': 3, 'name': 'ready.zip', 'state': 'uploaded'}
        starter = {'id': 4, 'name': 'retry.zip', 'state': 'starter'}
        with mock.patch.object(github, 'publish_release', return_value={'id': 1}), \
             mock.patch.object(github, '_request', side_effect=[[ready, starter], None]) as request:
            result = github.release_prepare('example/repo', TAG, COMMIT, 'secret')
        self.assertEqual(result['assets'], [ready])
        self.assertEqual(request.call_args.args[1], '/releases/assets/4')
        self.assertEqual(request.call_args.kwargs['method'], 'DELETE')

    def test_github_lists_all_asset_pages_before_deleting_placeholders(self):
        starter = {'id': 4, 'name': 'retry.zip', 'state': 'starter'}
        ready = {'id': 5, 'name': 'ready.zip', 'state': 'uploaded'}
        first = [starter] + [{'id': i + 10, 'name': str(i), 'state': 'uploaded'} for i in range(99)]
        with mock.patch.object(github, 'publish_release', return_value={'id': 1}), \
             mock.patch.object(github, '_request', side_effect=[first, [ready], None]) as request:
            result = github.release_prepare('example/repo', TAG, COMMIT, 'secret')
        self.assertIn(ready, result['assets'])
        self.assertEqual(len(result['assets']), 100)
        self.assertIn('page=2', request.call_args_list[1].args[1])
        self.assertEqual(request.call_args_list[2].kwargs['method'], 'DELETE')

    def test_unsupported_provider_is_skipped_before_tag_or_poll(self):
        service = git_provider.Provider('bitbucket', bitbucket, 'team/repo')
        state = {'merged': COMMIT}
        with mock.patch.object(git_provider, 'for_project', return_value=(service, [])), \
             mock.patch.object(bitbucket, 'tag') as tag:
            text = publish.pipeline_release(None, SimpleNamespace(settings={}), 'plan', state, '.', {'releaseMode': 'git'})
        self.assertIn('does not support', text)
        self.assertIn('skipped', text)
        self.assertNotIn('tag', state['release'])
        tag.assert_not_called()

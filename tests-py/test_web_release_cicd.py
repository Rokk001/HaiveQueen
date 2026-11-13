"""Release history and owner promotion use exact commits and original bytes."""
import isolation  # noqa: F401 -- never read or write the active profile
import hashlib
from types import SimpleNamespace
from unittest import TestCase, mock

from haivequeen import git_provider, web_api, web_releases
from haivequeen.git_providers import github, gitlab, gitea, azure

SHA = 'a' * 40
PRE = 'v2.0.1-preview.3'
FULL = 'v2.0.1'


class PromotionTests(TestCase):
    def setUp(self):
        self.calls = []
        self.source = {'id': 1, 'assets': [{'id': 4, 'name': 'original build.zip'}]}
        self.target = {'id': 2, 'assets': []}
        self.content = b'\x00original\xff\n'
        def prepare(repo, tag, commit, token):
            self.calls.append(('prepare', tag, commit))
            return self.source if tag == PRE else self.target
        def snapshot(repo, tag, commit, token):
            self.calls.append(('snapshot', tag, commit))
            return self.source
        def upload(repo, release, name, content, token):
            self.calls.append(('upload', release['id'], name, content))
            return {'name': name}
        self.module = SimpleNamespace(
            CAPABILITIES={'release_history', 'release_tag', 'release_prepare', 'release_snapshot', 'release_download', 'release_upload', 'release_asset_digest'},
            release_history=mock.Mock(return_value=[{'tag': PRE, 'version': PRE, 'date': '2026-01-02', 'commit': SHA, 'prerelease': True, 'full_tag': FULL}]),
            release_prepare=prepare, release_snapshot=snapshot, release_download=mock.Mock(return_value=self.content), release_upload=upload,
            release_asset_digest=mock.Mock(return_value=hashlib.sha256(self.content).hexdigest()),
            release_tag=mock.Mock())
        self.provider = git_provider.Provider('sample', self.module, 'team/repo')
        patcher = mock.patch.object(git_provider.Provider, 'credential', return_value=None)
        patcher.start()
        self.addCleanup(patcher.stop)

    def test_local_copies_same_names_bytes_and_exact_commit_without_build(self):
        with mock.patch('haivequeen.publish._run', side_effect=AssertionError('No build or Git command')):
            result = web_releases.promote(self.provider, 'local', PRE)
        self.assertEqual(result, {'tag': FULL, 'commit': SHA, 'files': ['original build.zip']})
        self.assertEqual(self.calls, [('snapshot', PRE, SHA), ('prepare', FULL, SHA),
                                     ('upload', 2, 'original build.zip', self.content)])
        self.module.release_tag.assert_not_called()

    def test_git_sets_only_tag_at_selected_commit(self):
        result = web_releases.promote(self.provider, 'git', PRE)
        self.module.release_tag.assert_called_once_with('team/repo', FULL, SHA, None)
        self.assertEqual(result['commit'], SHA)
        self.assertEqual(self.calls, [])
        self.module.release_download.assert_not_called()

    def test_retry_verifies_existing_files_without_duplicate_upload(self):
        self.target['assets'] = [{'name': 'original build.zip'}]
        web_releases.promote(self.provider, 'local', PRE)
        self.assertFalse(any(call[0] == 'upload' for call in self.calls))
        self.module.release_asset_digest.return_value = 'wrong'
        with self.assertRaisesRegex(ValueError, 'differs'):
            web_releases.promote(self.provider, 'local', PRE)

    def test_rejects_full_release_unknown_tag_and_unverified_commit(self):
        for tag in (FULL, 'missing'):
            with self.assertRaisesRegex(ValueError, 'Choose'):
                web_releases.promote(self.provider, 'local', tag)
        self.module.release_history.return_value[0]['commit'] = 'main'
        with self.assertRaisesRegex(ValueError, 'exact commit'):
            web_releases.promote(self.provider, 'git', PRE)
        self.module.release_tag.assert_not_called()

    def test_download_failure_cannot_create_full_release(self):
        self.module.release_download.side_effect = OSError('unavailable')
        with self.assertRaisesRegex(git_provider.ProviderError, 'unavailable'):
            web_releases.promote(self.provider, 'local', PRE)
        self.assertEqual(self.calls, [('snapshot', PRE, SHA)])

    def test_web_promotion_requires_confirmation_and_ignores_client_commit(self):
        with self.assertRaisesRegex(ValueError, 'Confirm'):
            web_api.execute(None, 'releases/promote', {'project': 'demo', 'tag': PRE})
        with mock.patch.object(web_releases, 'context', return_value=(self.provider, {'releaseMode': 'git'})), \
             mock.patch('haivequeen.web_jobs.start', side_effect=lambda key, work: work(None)):
            result = web_api.execute(None, 'releases/promote', {'project': 'demo', 'tag': PRE, 'commit': 'b' * 40, 'confirmed': True})
        self.assertEqual(result['commit'], SHA)


class HistoryTests(TestCase):
    def test_gitlab_full_release_label_and_source_download(self):
        from urllib.error import HTTPError
        missing = HTTPError('https://gitlab.com', 404, 'missing', {}, None)
        self.addCleanup(missing.close)
        with mock.patch.object(gitlab, 'release_tag'), \
             mock.patch.object(gitlab, '_request', side_effect=[missing, ([], '')]), \
             mock.patch.object(gitlab, 'release', return_value={'tag_name': FULL, 'commit': {'id': SHA}}) as release:
            gitlab.release_prepare('team/repo', FULL, SHA, 'mock')
        self.assertEqual(release.call_args.args[2], 'Release ' + FULL)
        link = {'name': 'build.zip', 'url': 'https://gitlab.com/api/v4/projects/team%2Frepo/packages/generic/haivequeen-release/' + PRE + '/build.zip'}
        with mock.patch.object(gitlab, '_request', side_effect=[({'tag_name': PRE, 'commit': {'id': SHA}}, ''), ([link], ''), (b'original', '')]) as request:
            source = gitlab.release_snapshot('team/repo', PRE, SHA, 'mock')
            self.assertEqual(gitlab.release_download('team/repo', source, source['assets'][0], 'mock'), b'original')
        self.assertTrue(request.call_args.kwargs['raw'])

    def test_gitea_snapshot_and_download_preserve_bytes_and_origin(self):
        import io
        repo = ('https://git.example', 'team', 'repo')
        asset = {'name': 'build.zip', 'browser_download_url': 'https://git.example/team/repo/releases/download/' + PRE + '/build.zip'}
        with mock.patch.object(gitea, '_request', side_effect=[{'commit': {'sha': SHA}}, {'tag_name': PRE, 'prerelease': True, 'id': 1}, [asset]]):
            source = gitea.release_snapshot(repo, PRE, SHA, 'mock')
        with mock.patch.object(gitea, 'urlopen', return_value=io.BytesIO(b'original')) as request:
            self.assertEqual(gitea.release_download(repo, source, asset, 'mock'), b'original')
        self.assertEqual(request.call_args.args[0].get_header('Authorization'), 'token mock')
        with mock.patch.object(gitea, 'urlopen') as request:
            with self.assertRaisesRegex(ValueError, 'outside'):
                gitea.release_download(repo, source, dict(asset, browser_download_url='https://other.example/build.zip'), 'mock')
        request.assert_not_called()

    def test_incomplete_github_source_is_rejected_without_deletion(self):
        with mock.patch.object(github, 'release_commit', return_value=SHA), \
             mock.patch.object(github, '_request', side_effect=[{'tag_name': PRE, 'prerelease': True, 'id': 1}, [{'id': 2, 'state': 'starter'}]]) as request:
            with self.assertRaisesRegex(ValueError, 'incomplete'):
                github.release_snapshot('team/repo', PRE, SHA, 'mock')
        self.assertEqual(request.call_count, 2)

    def test_snapshot_only_reads_existing_source_and_lists_all_assets(self):
        rows = [{'id': i, 'name': str(i), 'state': 'uploaded'} for i in range(100)]
        with mock.patch.object(github, '_request', side_effect=[
            {'object': {'type': 'tag', 'sha': 'b' * 40}}, {'object': {'type': 'commit', 'sha': SHA}},
            {'id': 1, 'tag_name': PRE, 'prerelease': True}, rows, [{'id': 101, 'name': 'last', 'state': 'uploaded'}]]) as request:
            result = github.release_snapshot('team/repo', PRE, SHA, 'mock')
        self.assertEqual(len(result['assets']), 101)
        self.assertTrue(all('method' not in call.kwargs and 'data' not in call.kwargs for call in request.call_args_list))

    def test_snapshot_rejects_moved_source_before_copy(self):
        with mock.patch.object(github, '_request', return_value={'object': {'type': 'commit', 'sha': 'b' * 40}}):
            with self.assertRaisesRegex(ValueError, 'different commit'):
                github.release_snapshot('team/repo', PRE, SHA, 'mock')
        with mock.patch.object(gitea, '_request', return_value={'commit': {'sha': 'b' * 40}}):
            with self.assertRaisesRegex(ValueError, 'different commit'):
                gitea.release_snapshot(('https://git.example', 'team', 'repo'), PRE, SHA, 'mock')
        with mock.patch.object(gitlab, '_request', return_value=({'tag_name': PRE, 'commit': {'id': 'b' * 40}}, '')):
            with self.assertRaisesRegex(ValueError, 'different tag or commit'):
                gitlab.release_snapshot('team/repo', PRE, SHA, 'mock')

    def test_no_full_publication_entry_for_plans_inbox_or_cli(self):
        from contextlib import redirect_stderr
        import io
        from haivequeen import cli, inbox, plans
        plan = plans.parse_plan_text('+++\nstatus="DRAFT"\nfullRelease=true\nnextVersion="9.0.0"\n+++\n# Publish full release\n')
        self.assertFalse(hasattr(plan, 'fullRelease'))
        self.assertFalse(hasattr(plan, 'nextVersion'))
        self.assertNotIn('promote', inbox.OWNER_KEYS)
        self.assertNotIn('fullRelease', inbox.OWNER_KEYS['release'])
        with redirect_stderr(io.StringIO()), self.assertRaises(SystemExit):
            cli.build_parser().parse_args(['releases/promote', '--tag', PRE])

    def test_github_resolves_annotated_tag_instead_of_target_branch(self):
        row = {'tag_name': PRE, 'target_commitish': 'main', 'published_at': '2026-01-02', 'prerelease': True}
        with mock.patch.object(github, 'release_list', return_value=[row]), \
             mock.patch.object(github, '_request', side_effect=[{'object': {'type': 'tag', 'sha': 'b' * 40}},
                                                               {'object': {'type': 'commit', 'sha': SHA}}]):
            result = github.release_history('team/repo', 'mock')
        self.assertEqual(result[0], {'tag': PRE, 'version': PRE, 'date': '2026-01-02', 'commit': SHA, 'prerelease': True, 'full_tag': FULL})

    def test_gitlab_and_gitea_return_same_web_contract(self):
        row = {'tag_name': PRE, 'released_at': '2026-01-02', 'published_at': '2026-01-02', 'prerelease': True, 'commit': {'id': SHA}}
        with mock.patch.object(gitlab, 'release_list', return_value=[row]):
            expected = gitlab.release_history('team/repo', 'mock')
        with mock.patch.object(gitea, 'release_list', return_value=[dict(row, target_commitish='main')]), \
             mock.patch.object(gitea, '_request', return_value={'commit': {'sha': SHA}}):
            self.assertEqual(gitea.release_history(('https://git.example', 'team', 'repo'), 'mock'), expected)

    def test_azure_peels_tag_and_paginates(self):
        with mock.patch.object(azure, '_request', side_effect=[({'value': [{'name': 'refs/tags/' + PRE, 'objectId': 'b' * 40, 'peeledObjectId': SHA}]}, 'next'),
                                                              ({'value': [{'sourceVersion': SHA, 'sourceBranch': 'refs/tags/' + PRE, 'finishTime': '2026-01-02'}]}, None), ({'value': []}, None)]) as request:
            rows = azure.release_history(('org', 'project', 'repo'), 'mock')
        self.assertEqual(rows[0]['commit'], SHA)
        self.assertEqual(rows[0]['date'], '2026-01-02')
        self.assertEqual(request.call_args.kwargs['query']['continuationToken'], 'next')

    def test_full_release_prepare_uses_full_publication_status(self):
        with mock.patch.object(github, 'publish_release', return_value={'id': 2}) as publish, \
             mock.patch.object(github, '_request', return_value=[]):
            github.release_prepare('team/repo', FULL, SHA, 'mock')
        publish.assert_called_once_with('team/repo', '2.0.1', SHA, 'mock')
        from urllib.error import HTTPError
        missing = HTTPError('https://git.example', 404, 'missing', {}, None)
        self.addCleanup(missing.close)
        with mock.patch.object(gitea, 'release_tag'), \
             mock.patch.object(gitea, '_request', side_effect=[missing, []]), \
             mock.patch.object(gitea, 'release', return_value={'id': 2, 'tag_name': FULL, 'prerelease': False}) as release:
            gitea.release_prepare(('https://git.example', 'team', 'repo'), FULL, SHA, 'mock')
        self.assertFalse(release.call_args.kwargs['prerelease'])

    def test_gitlab_download_rejects_other_origin_before_sending_credentials(self):
        with mock.patch.object(gitlab, '_request') as request:
            with self.assertRaisesRegex(ValueError, 'unexpected'):
                gitlab.release_download('team/repo', {'id': PRE}, {'name': 'a.zip', 'url': 'https://other.example/a.zip'}, 'mock')
        request.assert_not_called()

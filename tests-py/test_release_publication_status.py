from unittest import TestCase, mock
from urllib.error import HTTPError
from haivequeen.git_providers import github
from haivequeen import web_releases


class PublicationStatusTests(TestCase):
    sha = 'a' * 40
    def status(self):
        return github.release_promotion_status('team/repo', 'v1.2.3', 'v1.2.3-alpha.1', self.sha, 100, 'token')

    def test_unrelated_workflow_failure_is_not_our_failure(self):
        runs = [{'id': 1, 'event': 'workflow_dispatch', 'display_title': 'Promote v9 from v9-alpha.1', 'status': 'completed', 'conclusion': 'failure'}]
        with mock.patch.object(github, '_request', side_effect=[HTTPError('url',404,'missing',None,None), {'workflow_runs':runs}]):
            self.assertEqual(self.status(), {'state':'publishing'})

    def test_exact_failed_workflow_and_newer_retry(self):
        failed = {'id':1,'event':'workflow_dispatch','display_title':'Promote v1.2.3 from v1.2.3-alpha.1','status':'completed','conclusion':'failure'}
        for runs, expected in [([failed],'failed'), ([failed,{**failed,'id':2,'status':'in_progress'}],'publishing')]:
            with self.subTest(expected=expected), mock.patch.object(github,'_request',side_effect=[None,{'workflow_runs':runs}]):
                self.assertEqual(self.status()['state'],expected)

    def test_published_requires_matching_commit_and_files(self):
        target={'draft':False,'prerelease':False,'html_url':'https://example.test/release'}
        original={'assets':[{'name':'package.whl'}]}
        with mock.patch.object(github,'_request',return_value=target), mock.patch.object(github,'release_commit',return_value='b'*40):
            self.assertEqual(self.status()['state'],'failed')
        for digest, expected in [(['same','same'],'published'),(['source','different'],'publishing')]:
            with self.subTest(expected=expected), mock.patch.object(github,'_request',side_effect=[target,{'workflow_runs':[]}]), mock.patch.object(github,'release_commit',return_value=self.sha), mock.patch.object(github,'release_snapshot',return_value=original), mock.patch.object(github,'_release_assets',return_value=original), mock.patch.object(github,'release_asset_digest',side_effect=digest):
                self.assertEqual(self.status()['state'],expected)

    def test_completed_workflow_without_verified_assets_is_failure(self):
        run={'id':1,'event':'push','head_branch':'v1.2.3','head_sha':self.sha,'status':'completed','conclusion':'success'}
        with mock.patch.object(github,'_request',side_effect=[None,{'workflow_runs':[run]}]):
            self.assertIn('could not be verified',self.status()['error'])

    def test_status_uses_saved_job_and_rejects_other_project(self):
        job={'key':'release-promotion:demo','result':{'tag':'v1.2.3','source_tag':'v1.2.3-alpha.1','commit':self.sha,'requested_at':100}}
        provider=mock.Mock(capabilities={'release_promotion_status'})
        provider.credential.return_value='token';provider.call.return_value={'state':'publishing'}
        with mock.patch.object(web_releases,'context',return_value=(provider,'git')):
            self.assertEqual(web_releases.promotion_status(None,'demo',job),{'state':'publishing'})
            provider.call.assert_called_once_with('release_promotion_status','v1.2.3','v1.2.3-alpha.1',self.sha,100,'token')
            with self.assertRaisesRegex(ValueError,'another project'):
                web_releases.promotion_status(None,'other',job)

    def test_retry_of_annotated_promotion_dispatches_again(self):
        with mock.patch.object(github,'_request',side_effect=[{'object':{'type':'tag','sha':'tag-sha'}},{'message':'HaiveQueen prerelease: v1.2.3-alpha.1','object':{'type':'commit','sha':self.sha}},{'default_branch':'main'},None]) as request:
            github.release_promotion_tag('team/repo','v1.2.3',self.sha,'v1.2.3-alpha.1','token')
        self.assertEqual(request.call_args.args[1],'/actions/workflows/release.yml/dispatches')

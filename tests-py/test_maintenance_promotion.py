from unittest import TestCase, mock
from haivequeen.git_providers import github

class MaintenancePromotionTests(TestCase):
    def test_existing_matching_maintenance_tag_dispatches_without_rewriting_tag(self):
        sha='a'*40
        with mock.patch.object(github,'_request',side_effect=[{'object':{'type':'commit','sha':sha}},{'default_branch':'main'},None]) as request:
            github.release_promotion_tag('team/repo','v1.2.3',sha,'v1.2.3-alpha.1','token')
        self.assertEqual(request.call_args_list[-1].args[1],'/actions/workflows/release.yml/dispatches')
        self.assertEqual(request.call_args_list[-1].args[3],{'ref':'main','inputs':{'release_tag':'v1.2.3','source_tag':'v1.2.3-alpha.1'}})
        self.assertFalse(any('/git/refs' in call.args[1] for call in request.call_args_list))

    def test_different_commit_is_never_dispatched_or_retagged(self):
        with mock.patch.object(github,'_request',return_value={'object':{'type':'commit','sha':'b'*40}}) as request:
            with self.assertRaisesRegex(ValueError,'different commit'):
                github.release_promotion_tag('team/repo','v1.2.3','a'*40,'v1.2.3-alpha.1','token')
        self.assertEqual(request.call_count,1)

    def test_progress_and_error_feedback(self):
        import subprocess, shutil
        from pathlib import Path
        if not shutil.which('node'): self.skipTest('Node required')
        result=subprocess.run(['node',str(Path(__file__).with_name('web_release_promotion_test.cjs'))],capture_output=True,text=True,timeout=15)
        self.assertEqual(result.returncode,0,result.stdout+result.stderr)

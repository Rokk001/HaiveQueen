import unittest
from types import SimpleNamespace
from unittest.mock import Mock

from haivequeen.git_provider import Provider, ProviderError


class NormalizedProviderTests(unittest.TestCase):
    def provider(self, name, **operations):
        module = SimpleNamespace(CAPABILITIES=set(operations), **operations)
        return Provider(name, module, 'repo')

    def test_create_and_read_pr_for_each_provider(self):
        for name, field in [('github', 'number'), ('gitea', 'number'), ('gitlab', 'iid'), ('azure', 'pullRequestId'), ('bitbucket', 'id')]:
            with self.subTest(name=name):
                create = Mock(return_value={field: 42})
                status = Mock(return_value={'state': 'opened'})
                provider = self.provider(name, pull_request=create, pr_status=status)
                self.assertEqual(provider.open_pr('title', 'feature', 'main', 'secret'), '42')
                self.assertEqual(provider.pr_state('42', 'secret'), 'open')
                status.return_value = {'state': 'merged'}
                self.assertEqual(provider.pr_state('42', 'secret'), 'merged')
                status.return_value = {'state': 'unexpected'}
                with self.assertRaises(ProviderError):
                    provider.pr_state('42', 'secret')

    def test_pipeline_requires_evidence_for_exact_commit(self):
        github = self.provider('github', pipeline=Mock(return_value={'state': 'success', 'statuses': []}))
        github.module._request = Mock(return_value={'check_runs': []})
        self.assertEqual(github.pipeline_state('abc'), 'pending')
        github.module.pipeline.return_value['statuses'] = [{'state': 'success'}]
        self.assertEqual(github.pipeline_state('abc'), 'success')
        gitlab = self.provider('gitlab', pipeline=Mock(return_value={'sha': 'other', 'status': 'success'}))
        gitlab.module._request = Mock(return_value=([{'sha': 'other', 'status': 'success'}], ''))
        self.assertEqual(gitlab.pipeline_state('abc'), 'pending')
        azure = self.provider('azure', pipeline_for_commit=Mock(return_value=[{'sourceVersion': 'other', 'result': 'succeeded'}]))
        self.assertEqual(azure.pipeline_state('abc'), 'pending')
        azure.module.pipeline_for_commit.return_value.append({'sourceVersion': 'abc', 'result': 'failed'})
        self.assertEqual(azure.pipeline_state('abc'), 'failure')
        bitbucket = self.provider('bitbucket', pipeline_for_commit=Mock(return_value=[{'state': 'INPROGRESS'}, {'state': 'SUCCESSFUL'}]))
        self.assertEqual(bitbucket.pipeline_state('abc'), 'pending')

class ProviderStatusEndpointTests(unittest.TestCase):
    def test_pr_status_uses_read_endpoints(self):
        from haivequeen.git_providers import github, gitea, gitlab, bitbucket, azure
        from unittest.mock import patch
        for module, args, expected in [
            (github, ('o/r', '7', 'token'), '/pulls/7'),
            (gitea, (('https://codeberg.org', 'o', 'r'), '7', 'token'), '/pulls/7'),
            (gitlab, ('o/r', '7', 'token'), '/merge_requests/7'),
            (bitbucket, ('o/r', '7'), '/pullrequests/7'),
            (azure, (('org', 'project', 'repo'), '7', 'token'), '/pullrequests/7'),
        ]:
            with self.subTest(provider=module.__name__), patch.object(module, '_request', return_value=({}, '') if module is gitlab else {}) as request:
                module.pr_status(*args)
                self.assertTrue(any(expected in str(arg) for arg in request.call_args.args))


if __name__ == '__main__':
    unittest.main()

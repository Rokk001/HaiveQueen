import unittest
from types import FunctionType
import importlib
from unittest.mock import Mock, patch

from haivequeen.git_provider import Provider, ProviderError


class ProviderFixture:
    def __init__(self, name, operations):
        original = importlib.import_module('haivequeen.git_providers.' + name)
        self.__dict__['_globals'] = dict(vars(original))
        namespace = self.__dict__['_globals']
        for key, value in list(namespace.items()):
            if isinstance(value, FunctionType) and value.__module__ == original.__name__:
                namespace[key] = FunctionType(value.__code__, namespace, value.__name__, value.__defaults__, value.__closure__)
                namespace[key].__kwdefaults__ = value.__kwdefaults__
        namespace.update(operations)
        namespace['CAPABILITIES'] = set(original.CAPABILITIES) | set(operations)

    def __getattr__(self, name):
        return self._globals[name]

    def __setattr__(self, name, value):
        self._globals[name] = value


class PrRecovery(unittest.TestCase):
    def provider(self, name="github", **operations):
        return Provider(name, ProviderFixture(name, operations), "team/repo")

    def test_shared_profile_keeps_authentication_method_in_provider_module(self):
        from haivequeen import config
        provider = self.provider("azure")
        with patch.object(config, "git_credential", create=True, return_value={"token": "secret", "method": "bearer"}):
            self.assertIsNone(provider.credential())

    def test_legacy_github_login_fallback_remains_available(self):
        provider = self.provider(login=Mock(return_value="secret"))
        from haivequeen import config
        with patch.object(config, "git_credential", create=True, return_value=None):
            self.assertEqual(provider.credential(), "secret")

    def test_existing_pr_is_reused_and_fork_is_not_adopted(self):
        request = Mock(return_value=[
            {"number": 0, "head": {"ref": "feature", "repo": None},
             "base": {"ref": "main", "repo": {"full_name": "team/repo"}}},
            {"number": 1, "head": {"ref": "feature", "repo": {"full_name": "fork/repo"}},
             "base": {"ref": "main", "repo": {"full_name": "team/repo"}}},
            {"number": 2, "head": {"ref": "feature", "repo": {"full_name": "team/repo"}},
             "base": {"ref": "main", "repo": {"full_name": "team/repo"}}},
        ])
        provider = self.provider(pull_request=Mock(), pr_status=Mock())
        provider.module._request = request
        self.assertEqual(provider.ensure_pr("plan", "feature", "main", "secret"), "2")
        provider.module.pull_request.assert_not_called()

    def test_uncertain_create_is_recovered_by_second_lookup(self):
        create = Mock(side_effect=OSError("response lost"))
        lookup = Mock(side_effect=[None, 7])
        provider = self.provider(pull_request=create, find_pr=lookup)
        self.assertEqual(provider.ensure_pr("plan", "feature", "main"), "7")
        create.assert_called_once()
        self.assertEqual(lookup.call_count, 2)

    def test_lookup_failure_never_attempts_create(self):
        provider = self.provider(pull_request=Mock(), find_pr=Mock(side_effect=OSError("offline")))
        with self.assertRaisesRegex(ProviderError, "offline"):
            provider.ensure_pr("plan", "feature", "main")
        provider.module.pull_request.assert_not_called()

    def test_missing_pr_is_created_and_identifier_is_stable(self):
        provider = self.provider(pull_request=Mock(return_value={"number": 8}), find_pr=Mock(return_value=None))
        self.assertEqual(provider.ensure_pr("plan", "feature", "main"), "8")

    def test_github_lookup_reads_later_pages(self):
        rows = [{"head": {"ref": "other"}, "base": {"ref": "main"}}] * 100
        provider = self.provider(pull_request=Mock())
        provider.module._request = Mock(side_effect=[rows, [{"number": 9, "head": {"ref": "feature"}, "base": {"ref": "main"}}]])
        self.assertEqual(provider.ensure_pr("plan", "feature", "main"), "9")
        self.assertIn("page=2", provider.module._request.call_args.args[1])

    def test_merged_pr_recovers_crash_between_creation_and_state_save(self):
        provider = self.provider(pull_request=Mock(return_value={"number": 10}))
        provider.module._request = Mock(return_value=[{
            "number": 9, "state": "closed", "merged_at": "timestamp",
            "head": {"ref": "feature", "sha": "original"}, "base": {"ref": "main"}}])
        self.assertEqual(provider.ensure_pr("plan", "feature", "main", commit="original"), "9")
        provider.module.pull_request.assert_not_called()
        self.assertEqual(provider.ensure_pr("plan", "feature", "main", commit="fixed"), "10")
        provider.module.pull_request.assert_called_once()

    def test_each_provider_lookup_and_exact_merge_commit(self):
        cases = [
            ("gitlab", [{"iid": 3, "source_branch": "feature", "target_branch": "main", "source_project_id": 1, "target_project_id": 1}],
             {"merge_commit_sha": "merged"}),
            ("azure", {"value": [{"pullRequestId": 3, "sourceRefName": "refs/heads/feature", "targetRefName": "refs/heads/main"}]},
             {"lastMergeCommit": {"commitId": "merged"}}),
            ("bitbucket", [{"id": 3, "source": {"branch": {"name": "feature"}}, "destination": {"branch": {"name": "main"}}}],
             {"merge_commit": {"hash": "merged"}}),
            ("gitea", [{"number": 3, "head": {"ref": "feature"}, "base": {"ref": "main"}}],
             {"merge_commit_sha": "merged"}),
        ]
        for name, rows, detail in cases:
            with self.subTest(name=name):
                provider = self.provider(name, pull_request=Mock(), pr_status=Mock(return_value=detail))
                provider.module._request = Mock(return_value=(rows, "") if name == "gitlab" else rows)
                provider.module._list = Mock(return_value=rows)
                provider.module._repo = Mock(return_value="/repo")
                self.assertEqual(provider.ensure_pr("plan", "feature", "main"), "3")
                self.assertEqual(provider.pr_commit("3"), "merged")
                provider.module.pull_request.assert_not_called()

    def test_gitlab_pipeline_queries_sha_not_branch_ref(self):
        provider = self.provider("gitlab", pipeline=Mock())
        provider.module._request = Mock(return_value=([{"sha": "commit", "status": "success"}], ""))
        self.assertEqual(provider.pipeline_state("commit"), "success")
        self.assertEqual(provider.module._request.call_args.kwargs["query"]["sha"], "commit")
        provider.module.pipeline.assert_not_called()

    def test_github_actions_are_monitored_without_legacy_status_contexts(self):
        provider = self.provider(pipeline=Mock(return_value={"state": "pending", "statuses": []}))
        check = {"head_sha": "commit", "status": "in_progress", "conclusion": None}
        provider.module._request = Mock(return_value={"check_runs": [check]})
        self.assertEqual(provider.pipeline_state("commit"), "pending")
        check.update(status="completed", conclusion="success")
        self.assertEqual(provider.pipeline_state("commit"), "success")
        check["conclusion"] = "failure"
        self.assertEqual(provider.pipeline_state("commit"), "failure")
        check.update(head_sha="other", conclusion="success")
        self.assertEqual(provider.pipeline_state("commit"), "pending")

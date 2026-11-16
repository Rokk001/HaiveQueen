"""W-3 through W-6: simulate provider responses around real plan/remote state."""
import json
from unittest import mock

from bzztest import REL
from test_o1_orchestrator import OrchCase
from haivequeen import git_provider, orchestrator, runtime, web_api, web_server


class ProviderPipeline(OrchCase):
    def setUp(self):
        super().setUp()
        self.service = mock.Mock(name="provider")
        self.service.name = "simulated"
        self.service.supports_step.return_value = True
        self.service.ensure_pr.return_value = "17"
        self.service.pr_state.return_value = "open"
        self.service.pipeline_state.return_value = "pending"
        for patcher in (mock.patch.object(git_provider, "for_project", return_value=(self.service, [])),
                        mock.patch.object(orchestrator, "_provider_token", return_value="credential")):
            patcher.start()
            self.addCleanup(patcher.stop)

    def configure(self, merge="git", check="git", build="git", deploy="git", enabled=True):
        self.check, self.build, self.deploy = self.echo("Check"), self.echo("Build"), self.echo("Deploy")
        self.project_toml('wayOfWorking = "custom"\n' +
                          ''.join('%sMode = "%s"\n' % (key, value) for key, value in
                                  (("merge", merge), ("check", check), ("build", build), ("deploy", deploy))) +
                          ''.join('%sEnabled = %s\n' % (s, str(enabled).lower()) for s in ("check", "build", "deploy")))

    def dashboard_info(self):
        # Exercise the browser's state action and JSON representation with real data.
        state = web_api.execute(self.conn, "state", {"project": "testproj"})
        state = json.loads(json.dumps(state, default=web_server.json_value))
        rows = [h for h in state["hives"]
                if h["apiary"] == "testproj" and h["plan_path"] == REL]
        self.assertEqual(len(rows), 1)
        return rows[0]["orch"]

    def external_merge(self, squash=False):
        # The simulated maintainer, not the Orchestrator, moves the integration branch.
        if squash:
            other = self.base / "maintainer"
            from test_o1_orchestrator import sh
            sh("git", "clone", "-q", str(self.remote), str(other))
            self.git("config", "user.name", "Test", cwd=other)
            self.git("config", "user.email", "test@example.invalid", cwd=other)
            self.git("merge", "--squash", "origin/plan-br", cwd=other)
            self.git("commit", "-qm", "squash plan", cwd=other)
            self.git("push", "-q", "origin", "HEAD:main", cwd=other)
        else:
            self.git("push", "-q", "origin", "HEAD:main", cwd=self.pwt)
        self.service.pr_state.return_value = "merged"
        self.service.pr_commit.return_value = self.remote_head()
        return self.remote_head()

    def test_provider_only_waits_without_agents_resumes_and_closes_on_green(self):
        self.configure()
        tip = self.ready_plan()
        before = self.remote_head()
        with mock.patch.object(orchestrator, "_merge", side_effect=AssertionError("local merge")), \
                mock.patch.object(orchestrator, "_command", side_effect=AssertionError("local command")), \
                mock.patch.object(orchestrator, "_push", side_effect=AssertionError("integration push")):
            self.round()
            self.assertEqual(self.remote_head(), before)
            self.assertEqual(self.git("rev-parse", "refs/heads/plan-br", cwd=self.remote), tip)
            self.assertEqual(self.state()["provider"]["pr"], "17")
            # Repeated rounds load state from SQLite, as after a process restart.
            for _ in range(3):
                self.round()
            self.service.ensure_pr.assert_called_once()
            self.assertEqual(self.fixers() + self.queens(), [])
            self.assertEqual(self.run_order(), [])
            self.external_merge()
            self.round()
            self.assertEqual(self.state()["step"], "Check")
            self.service.pipeline_state.return_value = "success"
            self.round()
        self.assertIsNone(self.state())
        self.assertEqual(self.plan_status(), "REVIEW")
        self.assertFalse(self.service.merge.called)
        self.assertFalse(self.service.auto_merge.called)
        self.assertEqual(self.run_order(), [])
        self.assertEqual(orchestrator.last_info(self.conn, "testproj", REL)["commit"], tip)

    def test_mixed_provider_merge_local_commands_use_squash_commit(self):
        self.configure(check="local", build="git", deploy="local")
        self.ready_plan()
        self.round()
        merged = self.external_merge(squash=True)
        self.round()
        self.assertEqual(self.run_order(), ["Check"])
        self.assertEqual(self.git("rev-parse", "HEAD", cwd=self.pwt), merged)
        self.assertEqual(self.state()["step"], "Build")
        self.service.pipeline_state.return_value = "success"
        self.round()
        self.assertEqual(self.run_order(), ["Check", "Deploy"])
        self.assertEqual(self.plan_status(), "REVIEW")

    def test_local_merge_provider_check_and_deploy_local_build(self):
        self.configure(merge="local", build="local")
        tip = self.ready_plan()
        self.round()
        self.assertEqual(self.state()["step"], "Check")
        self.assertEqual(self.run_order(), [])
        self.service.pipeline_state.return_value = "success"
        self.round()
        self.assertEqual(self.run_order(), ["Build"])
        self.assertEqual(self.remote_head(), tip)
        self.service.ensure_pr.assert_not_called()
        self.assertEqual(self.plan_status(), "REVIEW")

    def test_unsupported_provider_steps_skip_without_changing_settings(self):
        self.configure(merge="local")
        self.service.supports_step.return_value = False
        self.ready_plan()
        self.round()
        self.assertEqual(self.plan_status(), "REVIEW")
        self.assertEqual(self.run_order(), [])
        self.service.pipeline_state.assert_not_called()
        self.assertEqual(self.env().settings["checkMode"], "git")
        events = self.conn.execute("SELECT text FROM events WHERE project='testproj'").fetchall()
        self.assertTrue(any("does not support Check; skipped" in r[0] for r in events))

    def test_unknown_remote_waits_with_reason_and_never_integrates(self):
        self.configure()
        self.ready_plan()
        before = self.remote_head()
        with mock.patch.object(git_provider, "for_project", return_value=(None, [])):
            self.round()
        self.assertIn("not supported", runtime.kv_get(self.conn, "testproj", "hive-blocker:" + REL))
        self.assertEqual(self.remote_head(), before)
        self.assertEqual(self.fixers(), [])

    def test_dashboard_pending_and_red_then_fix_and_new_attempt(self):
        self.configure()
        tip = self.ready_plan()
        self.round()
        info = self.dashboard_info()
        self.assertIn(["Merge", "running"], info["chain"])
        self.assertEqual(info["provider"]["pr"], "17")
        self.service.pipeline_state.return_value = "failure"
        self.round()
        fixer = self.fixers()[0]
        info = self.dashboard_info()
        self.assertIn(["Merge", "red"], info["chain"])
        self.assertEqual(info["fix_agent"], fixer["name"])
        fixed = self.commit(self.pwt, "fixed.txt")
        self.end_fixer(fixer)
        self.service.pipeline_state.return_value = "pending"
        self.round()
        self.assertNotEqual(tip, fixed)
        self.assertEqual(self.git("rev-parse", "plan-br", cwd=self.remote), fixed)
        self.assertEqual(self.state()["provider"]["pushed"], fixed)
        info = self.dashboard_info()
        self.assertIn(["Merge", "running"], info["chain"])
        self.assertEqual(info["fix_agent"], "")
        self.assertEqual(info["provider"]["pushed"], fixed)
        self.assertEqual(len(self.fixers()), 1)
        # An open PR is reused for the fix commit.
        self.service.ensure_pr.assert_called_once()
        self.external_merge()
        self.service.pipeline_state.return_value = "success"
        self.round()
        self.assertEqual(self.plan_status(), "REVIEW")
        self.assertIsNone(self.dashboard_info())

    def test_red_after_merge_opens_new_pr_for_fixed_commit(self):
        self.configure()
        self.ready_plan()
        self.round()
        self.external_merge()
        self.round()
        info = self.dashboard_info()
        self.assertIn(["Check", "running"], info["chain"])
        self.assertEqual(info["provider"]["pipeline"], "pending")
        self.service.pipeline_state.return_value = "failure"
        self.round()
        self.assertEqual(self.state()["fix"]["step"], "Check")
        info = self.dashboard_info()
        self.assertIn(["Check", "red"], info["chain"])
        self.assertEqual(info["fix_agent"], self.fixers()[0]["name"])
        fixed = self.commit(self.pwt, "fixed.txt")
        self.end_fixer(self.fixers()[0])
        self.service.pr_state.return_value = "open"
        self.service.ensure_pr.return_value = "18"
        self.service.pipeline_state.return_value = "pending"
        self.round()
        self.assertEqual(self.state()["provider"]["pr"], "18")
        self.assertEqual(self.state()["provider"]["pushed"], fixed)
        self.assertNotIn("commit", self.state()["provider"])
        info = self.dashboard_info()
        self.assertIn(["Merge", "running"], info["chain"])
        self.assertEqual(info["provider"]["pr"], "18")
        self.assertEqual(info["provider"]["pushed"], fixed)
        self.assertEqual(info["fix_agent"], "")

    def test_api_failure_retries_without_agent_or_duplicate_push(self):
        self.configure()
        self.ready_plan()
        self.service.ensure_pr.side_effect = git_provider.ProviderError("service unavailable")
        self.round()
        self.assertEqual(self.fixers(), [])
        self.service.ensure_pr.side_effect = None
        with mock.patch.object(orchestrator, "_git", wraps=orchestrator._git) as git:
            self.round()
        self.assertFalse(any("push" in call.args for call in git.call_args_list))
        self.assertEqual(self.state()["provider"]["pr"], "17")

    def test_closed_pr_does_not_close_plan_or_start_agent(self):
        self.configure()
        self.ready_plan()
        self.service.pr_state.return_value = "closed"
        self.round()
        self.assertEqual(self.plan_status(), "WAITING")
        self.assertEqual(self.fixers(), [])
        self.assertIn("closed without merge", runtime.kv_get(self.conn, "testproj", "hive-blocker:" + REL))

    def test_disabled_provider_steps_never_poll_pipeline_after_merge(self):
        self.configure(enabled=False)
        self.ready_plan()
        self.round()
        self.external_merge()
        self.round()
        self.assertEqual(self.plan_status(), "REVIEW")
        self.assertEqual(self.run_order(), [])
        self.service.pipeline_state.assert_not_called()

    def test_provider_only_release_waits_for_exact_tag_result_before_close(self):
        self.configure()
        with (self.repo / '.haivequeen/project.toml').open('a') as stream:
            stream.write('releaseEnabled = true\nreleaseMode = "git"\nreleasePostfix = "candidate"\n')
        self.git('tag', 'v3.0.0')
        self.git('push', '-q', 'origin', 'refs/tags/v3.0.0')
        self.service.repository = 'example/repo'
        self.service.capabilities = {'release_tag', 'release_state'}
        result = {'value': 'pending'}
        self.service.call.side_effect = lambda op, *args: result['value'] if op == 'release_state' else None
        self.ready_plan()
        with mock.patch.object(orchestrator, '_merge', side_effect=AssertionError('local merge')), \
             mock.patch.object(orchestrator, '_command', side_effect=AssertionError('local command')), \
             mock.patch.object(orchestrator, '_push', side_effect=AssertionError('local integration push')):
            self.round()
            merged = self.external_merge()
            self.service.pipeline_state.return_value = 'success'
            self.round()
            self.assertEqual(self.state()['step'], 'Release')
            self.assertEqual(self.state()['release']['tag'], 'v3.0.1-candidate.1')
            self.round()
            self.assertEqual(self.fixers() + self.queens(), [])
            tags = [c for c in self.service.call.call_args_list if c.args[0] == 'release_tag']
            self.assertEqual(len(tags), 1)
            self.assertEqual(tags[0].args[1:3], ('v3.0.1-candidate.1', merged))
            result['value'] = 'success'
            self.round()
        self.assertIsNone(self.state())
        self.assertEqual(self.plan_status(), 'REVIEW')
        self.assertEqual(self.run_order(), [])
        self.assertFalse(self.service.merge.called)
        self.assertFalse(self.service.auto_merge.called)

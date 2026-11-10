"""Package worktrees are derived from the plan and package, not agent names."""
from pathlib import Path

from test_b1_plan_header import GitCase
from bzztest import REL
from haivequeen import bees, roles


class PackageWorktrees(GitCase):
    def guarded_commit(self, cwd, name='Ada', no_verify=False):
        import json
        import os
        import subprocess
        from haivequeen import runtime
        data = json.loads(runtime.kv_get(self.conn, self.env_b2.name, 'agent-branch:' + name))
        result = subprocess.run(['git', '-C', str(cwd), '-c', 'user.name=T', '-c', 'user.email=t@example.invalid',
                                 'commit', '--allow-empty', '-m', 'work'] + (['--no-verify'] if no_verify else []),
                                env={**os.environ, **data['environment']}, capture_output=True, text=True)
        self.assertNotEqual(result.returncode, 0, result.stdout + result.stderr)

    def setUp(self):
        super().setUp()
        self.plan("RUNNING", [{"id": "P1", "title": "First"}], branch="feature/demo", worktree=str(self.base / "plan"))
        self.git("branch", "feature/demo")
        self.env_b2 = self.hq_env()

    def test_new_and_replacement_use_plan_tip_and_same_names(self):
        pair = bees.prepare_agent_worktree(self.env_b2, REL, "P1")
        self.assertEqual(pair[1], "feature/demo-p1")
        self.assertTrue(Path(pair[0]).name.lower().endswith("-dummy-p1"))
        self.assertEqual(self.git("rev-parse", "HEAD", cwd=pair[0]), self.git("rev-parse", "feature/demo"))
        self.assertEqual(bees.prepare_agent_worktree(self.env_b2, REL, "P1"), pair)

    def test_existing_checkout_is_used(self):
        self.git("branch", "feature/demo-p1", "feature/demo")
        elsewhere = self.base / "elsewhere"
        self.git("worktree", "add", str(elsewhere), "feature/demo-p1")
        self.assertEqual(Path(bees.prepare_agent_worktree(self.env_b2, REL, "P1")[0]), elsewhere)

    def test_wrong_branch_in_package_folder_blocks(self):
        from haivequeen import routine
        folder = Path(routine.new_worktree(self.env_b2, routine.plan_tag(self.env_b2, REL).lower() + "-p1"))
        self.git("worktree", "add", "-b", "other", str(folder))
        self.assertIsNone(bees.prepare_agent_worktree(self.env_b2, REL, "P1"))
        self.assertEqual(self.git("branch", "--show-current", cwd=folder), "other")

    def test_saved_legacy_worktree_is_kept(self):
        legacy = self.base / "legacy-agent"
        self.git("worktree", "add", "-b", "old-agent", str(legacy))
        bees.ensure_attempts(self.conn)
        self.conn.execute("INSERT INTO bee_worktrees VALUES (?,?,?,?)", (self.env_b2.name, REL, "P1", str(legacy)))
        self.assertEqual(bees.prepare_agent_worktree(self.env_b2, REL, "P1"), (str(legacy), "old-agent"))

    def test_agent_rules_forbid_other_branches(self):
        self.assertIn("never create", roles.role_text("bee").lower())

    def test_conflict_is_logged(self):
        from haivequeen import routine
        folder = Path(routine.new_worktree(self.env_b2, routine.plan_tag(self.env_b2, REL).lower() + '-p1'))
        self.git('worktree', 'add', '-b', 'wrong', str(folder))
        messages = []
        self.env_b2.log = messages.append
        self.assertIsNone(bees.prepare_agent_worktree(self.env_b2, REL, 'P1'))
        self.assertTrue(any(str(folder) in m and 'wrong' in m and 'feature/demo-p1' in m for m in messages))

    def test_roles_describe_automatic_naming(self):
        self.assertIn('never create', roles.role_text('bee').lower())
        text = roles.role_text('queen').lower()
        self.assertIn('haivequeen', text)
        self.assertIn('sub-branch', text)

    def test_agent_start_does_not_require_worktree(self):
        from haivequeen import cli
        args = cli.build_parser().parse_args(['agent', 'start', '--project', 'testproj', '--plan', REL, '--package', 'P1', '--class', 'medium', '--name', 'A', '--task', 'work', '--steps', '["work"]'])
        self.assertFalse(args.cwd)

    def test_readme_documents_naming(self):
        readme = (Path(__file__).resolve().parents[1] / 'README.md').read_text(encoding='utf-8').lower()
        self.assertIn('<plan branch>-<package', readme)


    def test_branch_violation_survives_switch_back_and_counts_once(self):
        pair = bees.prepare_agent_worktree(self.env_b2, REL, 'P1')
        bees.record_agent_branch_start(self.env_b2, 'Ada', *pair)
        self.git('checkout', '-b', 'foreign', cwd=pair[0])
        self.guarded_commit(pair[0])
        self.git('checkout', pair[1], cwd=pair[0])
        self.assertEqual(bees.agent_branch_violation(self.env_b2, 'Ada'), 'foreign')
        bees.report_attempt(self.env_b2, REL, 'P1', 'medium', 'failed', 'Ada', error='foreign branch', force_failure=True)
        bees.report_attempt(self.env_b2, REL, 'P1', 'medium', 'failed', 'Ada', error='foreign branch', force_failure=True)
        self.assertEqual(bees.failure_count(self.conn, self.env_b2.name, REL, 'P1', 'medium'), 1)

    def test_switch_without_commit_is_not_a_violation(self):
        from unittest import mock
        pair = bees.prepare_agent_worktree(self.env_b2, REL, 'P1')
        environment = bees.record_agent_branch_start(self.env_b2, 'Ada', *pair)
        with mock.patch.dict('os.environ', environment):
            self.git('checkout', '-b', 'foreign', cwd=pair[0])
            self.git('checkout', '--detach', cwd=pair[0])
            self.git('checkout', pair[1], cwd=pair[0])
        self.assertEqual(bees.agent_branch_violation(self.env_b2, 'Ada'), '')

    def test_cross_worktree_no_verify_and_expired_reflogs(self):
        pair = bees.prepare_agent_worktree(self.env_b2, REL, 'P1')
        bees.record_agent_branch_start(self.env_b2, 'Ada', *pair)
        other = self.base / 'foreign-checkout'
        self.git('worktree', 'add', '-b', 'foreign', str(other))
        self.guarded_commit(other, no_verify=True)
        self.git('reflog', 'expire', '--expire=now', '--all')
        self.assertEqual(bees.agent_branch_violation(self.env_b2, 'Ada'), 'foreign')

    def test_other_agents_commits_are_not_attributed(self):
        pair = bees.prepare_agent_worktree(self.env_b2, REL, 'P1')
        bees.record_agent_branch_start(self.env_b2, 'Ada', *pair)
        other = self.base / 'other-agent'
        self.git('worktree', 'add', '-b', 'foreign', str(other))
        self.commit('another process', cwd=other)
        self.assertEqual(bees.agent_branch_violation(self.env_b2, 'Ada'), '')

    def test_environment_free_commit_is_reported_after_switch_back(self):
        pair = bees.prepare_agent_worktree(self.env_b2, REL, 'P1')
        bees.record_agent_branch_start(self.env_b2, 'Ada', *pair)
        self.git('checkout', '-b', 'foreign', cwd=pair[0])
        self.commit('without guard environment', cwd=pair[0])
        self.git('checkout', pair[1], cwd=pair[0])
        self.assertEqual(bees.agent_branch_violation(self.env_b2, 'Ada'), 'foreign')
        self.git('reflog', 'expire', '--expire=now', '--all')
        self.assertEqual(bees.agent_branch_violation(self.env_b2, 'Ada'), 'foreign')

    def test_hooks_path_override_is_reported_at_end(self):
        from unittest import mock
        pair = bees.prepare_agent_worktree(self.env_b2, REL, 'P1')
        environment = bees.record_agent_branch_start(self.env_b2, 'Ada', *pair)
        self.agent('Ada', phase='bee', status='running', package='P1', cwd=pair[0], **{'class': 'medium'})
        self.git('checkout', '-b', 'foreign', cwd=pair[0])
        with mock.patch.dict('os.environ', environment):
            self.git('-c', 'core.hooksPath=' + str(self.base / 'no-hooks'), '-c', 'user.name=T',
                     '-c', 'user.email=t@example.invalid', 'commit', '--allow-empty', '--no-verify', '-m', 'bypass', cwd=pair[0])
        self.git('checkout', pair[1], cwd=pair[0])
        from haivequeen import runtime
        ended = runtime.upsert_agent(self.conn, self.env_b2.name, 'Ada', {'status': 'done'})
        self.assertIn('foreign', ended['last_line'])
        self.assertEqual(bees.failure_count(self.conn, self.env_b2.name, REL, 'P1', 'medium'), 1)

    def test_environment_free_detached_commit_is_reported(self):
        pair = bees.prepare_agent_worktree(self.env_b2, REL, 'P1')
        bees.record_agent_branch_start(self.env_b2, 'Ada', *pair)
        self.git('checkout', '--detach', cwd=pair[0])
        self.commit('unguarded detached', cwd=pair[0])
        self.git('checkout', pair[1], cwd=pair[0])
        self.assertEqual(bees.agent_branch_violation(self.env_b2, 'Ada'), '(detached HEAD)')

    def test_old_foreign_commits_and_allowed_unguarded_commits_are_ignored(self):
        pair = bees.prepare_agent_worktree(self.env_b2, REL, 'P1')
        self.git('checkout', '-b', 'foreign', cwd=pair[0])
        self.commit('before attempt', cwd=pair[0])
        self.git('checkout', pair[1], cwd=pair[0])
        bees.record_agent_branch_start(self.env_b2, 'Ada', *pair)
        self.commit('assigned branch without guard', cwd=pair[0])
        self.assertEqual(bees.agent_branch_violation(self.env_b2, 'Ada'), '')

    def test_environment_free_commit_after_branch_rename_is_reported(self):
        pair = bees.prepare_agent_worktree(self.env_b2, REL, 'P1')
        bees.record_agent_branch_start(self.env_b2, 'Ada', *pair)
        self.git('branch', '-m', 'renamed', cwd=pair[0])
        self.commit('renamed branch', cwd=pair[0])
        self.assertEqual(bees.agent_branch_violation(self.env_b2, 'Ada'), 'renamed')

    def test_reflog_expiration_before_audit_fails_without_inventing_a_branch(self):
        from haivequeen import branchguard
        pair = bees.prepare_agent_worktree(self.env_b2, REL, 'P1')
        bees.record_agent_branch_start(self.env_b2, 'Ada', *pair)
        self.git('checkout', '-b', 'foreign', cwd=pair[0])
        self.commit('no environment', cwd=pair[0])
        self.git('checkout', pair[1], cwd=pair[0])
        self.git('reflog', 'expire', '--expire=now', '--all')
        evidence = bees.agent_branch_violation(self.env_b2, 'Ada')
        self.assertIn('expired or rewritten', branchguard.failure_fields(evidence)['branch_audit_error'])
        self.assertNotIn('foreign_branch', branchguard.failure_fields(evidence))
        for _ in range(2):
            bees.report_attempt(self.env_b2, REL, 'P1', 'medium', 'succeeded', 'Ada')
        self.assertFalse(bees.package_done(self.conn, self.env_b2.name, REL, 'P1'))
        self.assertEqual(bees.failure_count(self.conn, self.env_b2.name, REL, 'P1', 'medium'), 1)

    def test_missing_reflog_is_reported_by_done_without_a_foreign_branch(self):
        import contextlib
        import io
        import json
        from unittest import mock
        from haivequeen import cli, runtime
        pair = bees.prepare_agent_worktree(self.env_b2, REL, 'P1')
        self.agent('Ada', phase='bee', status='running', package='P1', cwd=pair[0], **{'class': 'medium'})
        agent = runtime.get_agent(self.conn, self.env_b2.name, 'Ada')
        bees.record_agent_branch_start(self.env_b2, 'Ada', *pair)
        log = Path(self.git('rev-parse', '--path-format=absolute', '--git-path', 'logs/HEAD', cwd=pair[0]))
        baseline = log.read_bytes()
        log.unlink()
        report = self.base / 'result.txt'
        report.write_text('finished', encoding='utf-8')
        args = cli.build_parser().parse_args(['done', '--agent', str(agent['id']), '--result', str(report)])
        output = io.StringIO()
        with mock.patch('haivequeen.scheduler.load_projects', return_value=[self.env_b2]), contextlib.redirect_stdout(output):
            self.assertEqual(cli.cmd_done(args, self.conn), 0)
        result = json.loads(output.getvalue())
        self.assertIn('HEAD reflog is missing', result['branch_audit_error'])
        self.assertNotIn('foreign_branch', result)
        self.assertIn('Branch audit unavailable', runtime.get_agent(self.conn, self.env_b2.name, 'Ada')['last_line'])
        self.assertEqual(bees.failure_count(self.conn, self.env_b2.name, REL, 'P1', 'medium'), 1)
        log.write_bytes(baseline)
        # Restoring the log cannot turn a previously failed audit into success.
        self.assertIn('missing', bees.agent_branch_violation(self.env_b2, 'Ada'))

    def test_missing_guard_and_invalid_policy_fail_runner_end(self):
        import json
        import shutil
        from haivequeen import runtime
        for missing in (False, True):
            with self.subTest(missing_guard=missing):
                name = 'Missing' if missing else 'Invalid'
                pair = bees.prepare_agent_worktree(self.env_b2, REL, 'P1')
                self.agent(name, phase='bee', status='running', package='P1', cwd=pair[0], **{'class': 'medium'})
                bees.record_agent_branch_start(self.env_b2, name, *pair)
                data = json.loads(runtime.kv_get(self.conn, self.env_b2.name, 'agent-branch:' + name))
                if missing:
                    shutil.rmtree(data['guard'])
                else:
                    (Path(data['guard']) / 'policy.json').write_text('{invalid', encoding='utf-8')
                ended = runtime.upsert_agent(self.conn, self.env_b2.name, name, {'status': 'done'})
                self.assertIn('Branch audit unavailable', ended['last_line'])
                self.assertNotIn('Foreign branch commit', ended['last_line'])
        self.assertEqual(bees.failure_count(self.conn, self.env_b2.name, REL, 'P1', 'medium'), 2)

    def test_shared_history_cannot_identify_a_cleared_environment_writer(self):
        import os
        import subprocess
        pair = bees.prepare_agent_worktree(self.env_b2, REL, 'P1')
        other = self.base / 'concurrent-package'
        self.git('worktree', 'add', '-b', 'foreign', str(other))
        bees.record_agent_branch_start(self.env_b2, 'Ada', *pair)
        bees.record_agent_branch_start(self.env_b2, 'Other', str(other), 'foreign')
        clean = {k: v for k, v in os.environ.items() if not k.startswith(('GIT_', 'BZZ_'))}
        subprocess.run(['git', '-C', str(other), '-c', 'user.name=T', '-c', 'user.email=t@example.invalid',
                        'commit', '--allow-empty', '-m', 'unidentified writer'], env=clean,
                       capture_output=True, text=True, check=True, timeout=30)
        # The same bytes result whether Ada entered Other's worktree or Other
        # committed legitimately. Blaming Ada from shared branch history is unsafe.
        self.assertNotEqual(self.git('rev-parse', 'foreign'), self.git('rev-parse', pair[1]))
        self.assertEqual(bees.agent_branch_violation(self.env_b2, 'Ada'), '')
        self.assertEqual(bees.agent_branch_violation(self.env_b2, 'Other'), '')

    def test_embedded_result_alias_cannot_hide_an_unavailable_audit(self):
        import contextlib
        import io
        import json
        from unittest import mock
        from haivequeen import cli, hives, runtime
        hives.create(self.conn, 'testproj', REL, 'claude', '123456')
        self.agent('queen', phase='queen', status='running', provider='claude', pid=42)
        pair = bees.prepare_agent_worktree(self.env_b2, REL, 'P1')
        bees.record_agent_branch_start(self.env_b2, 'Ada', *pair)
        bees.record_embedded_start(self.conn, self.env_b2.name, REL, 'P1', 'Ada', 'medium', 'claude', queen='queen')
        log = Path(self.git('rev-parse', '--path-format=absolute', '--git-path', 'logs/HEAD', cwd=pair[0]))
        log.unlink()
        report = self.base / 'embedded-result.txt'
        report.write_text('finished', encoding='utf-8')
        args = cli.build_parser().parse_args(['agent', 'result', '--project', 'testproj', '--plan', REL,
                '--package', 'P1', '--class', 'medium', '--attempt', 'result-alias', '--outcome', 'succeeded',
                '--ended-evidence', str(report)])
        output = io.StringIO()
        with mock.patch.dict('os.environ', {'BZZ_AGENT': 'queen'}), mock.patch.object(cli.config, 'load_config', return_value=self.machine), contextlib.redirect_stdout(output):
            self.assertEqual(cli.cmd_bee(args, self.conn), 0, output.getvalue())
        result = json.loads(output.getvalue())
        self.assertEqual(result['outcome'], 'failed')
        self.assertIn('missing', result['branch_audit_error'])
        self.assertNotIn('foreign_branch', result)
        self.assertFalse(bees.package_done(self.conn, self.env_b2.name, REL, 'P1'))
        self.assertEqual(bees.failure_count(self.conn, self.env_b2.name, REL, 'P1', 'medium'), 1)
        events = runtime.list_events(self.conn, self.env_b2.name)
        self.assertTrue(any('Branch audit unavailable' in str(event) for event in events))

    def test_fast_forward_is_not_a_new_commit_but_merge_commit_is(self):
        pair = bees.prepare_agent_worktree(self.env_b2, REL, 'P1')
        self.git('branch', 'foreign', cwd=pair[0])
        self.git('checkout', '-b', 'ahead', cwd=pair[0])
        self.commit('existing commit', cwd=pair[0])
        self.git('checkout', pair[1], cwd=pair[0])
        bees.record_agent_branch_start(self.env_b2, 'Ada', *pair)
        self.git('checkout', 'foreign', cwd=pair[0])
        self.git('merge', '--ff-only', 'ahead', cwd=pair[0])
        self.assertEqual(bees.agent_branch_violation(self.env_b2, 'Ada'), '')
        self.git('checkout', pair[1], cwd=pair[0])
        self.commit('allowed divergent commit', cwd=pair[0])
        self.git('checkout', 'foreign', cwd=pair[0])
        self.git('-c', 'user.name=T', '-c', 'user.email=t@example.invalid',
                 'merge', '--no-ff', '-m', 'new merge commit', pair[1], cwd=pair[0])
        self.assertEqual(bees.agent_branch_violation(self.env_b2, 'Ada'), 'foreign')

    def test_assigned_branch_commits_preserve_existing_hooks(self):
        import os
        import subprocess
        pair = bees.prepare_agent_worktree(self.env_b2, REL, 'P1')
        hooks = self.base / 'custom-hooks'
        hooks.mkdir()
        marker = self.base / 'hook-ran'
        hook = hooks / 'pre-commit'
        hook.write_text('#!/bin/sh\necho ran > "' + marker.as_posix() + '"\n', encoding='utf-8')
        hook.chmod(0o755)
        self.git('config', 'core.hooksPath', str(hooks))
        environment = bees.record_agent_branch_start(self.env_b2, 'Ada', *pair)
        result = subprocess.run(['git', '-C', pair[0], '-c', 'user.name=T', '-c', 'user.email=t@example.invalid',
                                 'commit', '--allow-empty', '-m', 'allowed'], env={**os.environ, **environment}, capture_output=True, text=True)
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertTrue(marker.exists())
        self.assertEqual(self.git('config', '--local', 'core.hooksPath'), str(hooks))
        self.assertEqual(bees.agent_branch_violation(self.env_b2, 'Ada'), '')

    def test_detached_no_verify_commit_is_refused(self):
        pair = bees.prepare_agent_worktree(self.env_b2, REL, 'P1')
        bees.record_agent_branch_start(self.env_b2, 'Ada', *pair)
        self.git('checkout', '--detach', cwd=pair[0])
        self.guarded_commit(pair[0], no_verify=True)
        self.assertEqual(bees.agent_branch_violation(self.env_b2, 'Ada'), '(detached HEAD)')

    def test_runner_failure_reports_violation(self):
        from haivequeen import runtime
        pair = bees.prepare_agent_worktree(self.env_b2, REL, 'P1')
        bees.record_agent_branch_start(self.env_b2, 'Ada', *pair)
        self.agent('Ada', phase='bee', status='running', package='P1', cwd=pair[0], **{'class': 'medium'})
        self.git('checkout', '-b', 'foreign', cwd=pair[0])
        self.guarded_commit(pair[0])
        ended = runtime.upsert_agent(self.conn, self.env_b2.name, 'Ada', {'status': 'failed'})
        self.assertIn('foreign', ended['last_line'])
        self.assertEqual(bees.failure_count(self.conn, self.env_b2.name, REL, 'P1', 'medium'), 1)

    def test_embedded_queen_end_counts_violation(self):
        from unittest import mock
        pair = bees.prepare_agent_worktree(self.env_b2, REL, 'P1')
        bees.record_agent_branch_start(self.env_b2, 'Ada', *pair)
        bees.record_embedded_start(self.conn, self.env_b2.name, REL, 'P1', 'Ada', 'medium', 'claude', queen='queen')
        self.git('checkout', '-b', 'foreign', cwd=pair[0])
        self.guarded_commit(pair[0])
        with mock.patch.object(bees, 'queen_has_ended', return_value=True):
            self.assertEqual(bees.close_ended_embedded(self.conn, self.env_b2.name), 1)
        self.assertEqual(bees.failure_count(self.conn, self.env_b2.name, REL, 'P1', 'medium'), 1)
        self.assertEqual(self.conn.execute('SELECT outcome FROM embedded_bees').fetchone()[0], 'failed')

    def test_embedded_result_alias_cannot_bypass_violation(self):
        pair = bees.prepare_agent_worktree(self.env_b2, REL, 'P1')
        bees.record_agent_branch_start(self.env_b2, 'Ada', *pair)
        bees.record_embedded_start(self.conn, self.env_b2.name, REL, 'P1', 'Ada', 'medium', 'claude', queen='queen')
        self.git('checkout', '-b', 'foreign', cwd=pair[0])
        self.guarded_commit(pair[0])
        bees.report_attempt(self.env_b2, REL, 'P1', 'medium', 'succeeded', 'queen-result-alias')
        self.assertFalse(bees.package_done(self.conn, self.env_b2.name, REL, 'P1'))
        self.assertEqual(bees.failure_count(self.conn, self.env_b2.name, REL, 'P1', 'medium'), 1)

    def test_reattach_records_foreign_branch(self):
        from haivequeen import reattach
        pair = bees.prepare_agent_worktree(self.env_b2, REL, 'P1')
        bees.record_agent_branch_start(self.env_b2, 'Ada', *pair)
        self.agent('Ada', phase='bee', status='running', package='P1', pid=123, cwd=pair[0], **{'class': 'medium'})
        self.git('checkout', '-b', 'foreign', cwd=pair[0])
        self.guarded_commit(pair[0])
        self.assertEqual(reattach.reattach(self.conn, self.env_b2.name, lambda pid: False)['failed'], ['Ada'])
        self.assertEqual(bees.failure_count(self.conn, self.env_b2.name, REL, 'P1', 'medium'), 1)

    def test_late_violation_overrides_success_once(self):
        pair = bees.prepare_agent_worktree(self.env_b2, REL, 'P1')
        bees.record_agent_branch_start(self.env_b2, 'Ada', *pair)
        bees.report_attempt(self.env_b2, REL, 'P1', 'medium', 'succeeded', 'Ada')
        self.git('checkout', '-b', 'foreign', cwd=pair[0])
        self.guarded_commit(pair[0])
        for _ in range(2):
            bees.report_attempt(self.env_b2, REL, 'P1', 'medium', 'succeeded', 'Ada')
        self.assertFalse(bees.package_done(self.conn, self.env_b2.name, REL, 'P1'))
        self.assertEqual(bees.failure_count(self.conn, self.env_b2.name, REL, 'P1', 'medium'), 1)

    def test_cli_subagent_returns_created_pair(self):
        import contextlib
        import io
        import json
        from unittest import mock
        from haivequeen import cli, hives
        hives.create(self.conn, 'testproj', REL, 'claude', '123456')
        self.agent('queen', phase='queen', status='running', provider='claude', pid=42, cwd=str(self.base / 'plan'))
        self.machine['providers'][0]['beeMode'] = 'subagent'
        args = cli.build_parser().parse_args(['agent', 'start', '--project', 'testproj', '--plan', REL, '--package', 'P1', '--class', 'medium', '--name', 'Ada', '--task', 'work', '--steps', '["work"]'])
        output = io.StringIO()
        with mock.patch.dict('os.environ', {'BZZ_AGENT': 'queen'}), mock.patch.object(cli.config, 'load_config', return_value=self.machine), contextlib.redirect_stdout(output):
            self.assertEqual(cli.cmd_bee(args, self.conn), 0, output.getvalue())
        result = json.loads(output.getvalue())
        self.assertEqual(result['kind'], 'subagent')
        self.assertEqual(result['branch'], 'feature/demo-p1')
        self.assertTrue(Path(result['cwd']).is_dir())
        self.assertNotIn('ada', result['branch'].lower())

    def test_process_guard_uses_the_runners_final_name(self):
        from haivequeen import hives, runtime
        hives.create(self.conn, self.env_b2.name, REL, 'claude', '123456')
        self.agent('queen', phase='queen', status='running', provider='claude', pid=42)
        pair = bees.prepare_agent_worktree(self.env_b2, REL, 'P1')
        self.agent('Ada', phase='bee', status='done', package='P1', cwd=pair[0])
        result = bees.start_process(self.env_b2, REL, 'P1', 'Ada', 'medium', 'work', ['work'], [], [], no_subagents=True)
        self.assertEqual(result['name'], 'Ada-2')
        self.assertEqual(runtime.get_agent(self.conn, self.env_b2.name, result['name'])['status'], 'running')
        self.git('checkout', '-b', 'foreign', cwd=pair[0])
        self.guarded_commit(pair[0], name=result['name'])
        self.assertEqual(bees.agent_branch_violation(self.env_b2, result['name']), 'foreign')
        self.assertEqual(bees.agent_branch_violation(self.env_b2, 'Ada'), '')

    def test_done_reports_and_counts_foreign_commit(self):
        import contextlib
        import io
        import json
        from unittest import mock
        from haivequeen import cli, runtime
        pair = bees.prepare_agent_worktree(self.env_b2, REL, 'P1')
        self.agent('Ada', phase='bee', status='running', package='P1', cwd=pair[0], **{'class': 'medium'})
        agent = runtime.get_agent(self.conn, self.env_b2.name, 'Ada')
        bees.record_agent_branch_start(self.env_b2, 'Ada', *pair)
        self.git('checkout', '-b', 'foreign', cwd=pair[0])
        self.commit('without environment', cwd=pair[0])
        report = self.base / 'result.txt'
        report.write_text('done', encoding='utf-8')
        args = cli.build_parser().parse_args(['done', '--agent', str(agent['id']), '--result', str(report)])
        output = io.StringIO()
        with mock.patch('haivequeen.scheduler.load_projects', return_value=[self.env_b2]), contextlib.redirect_stdout(output):
            self.assertEqual(cli.cmd_done(args, self.conn), 0)
        self.assertEqual(json.loads(output.getvalue())['foreign_branch'], 'foreign')
        self.assertEqual(bees.failure_count(self.conn, self.env_b2.name, REL, 'P1', 'medium'), 1)

    def test_replacement_refuses_changed_branch(self):
        pair = bees.prepare_agent_worktree(self.env_b2, REL, 'P1')
        self.git('checkout', '-b', 'foreign', cwd=pair[0])
        self.assertIsNone(bees.prepare_agent_worktree(self.env_b2, REL, 'P1'))
        self.assertEqual(self.git('branch', '--show-current', cwd=pair[0]), 'foreign')

    def test_queen_cannot_report_foreign_commit_as_success(self):
        import contextlib
        import io
        import json
        from unittest import mock
        from haivequeen import cli, hives
        hives.create(self.conn, 'testproj', REL, 'claude', '123456')
        self.agent('queen', phase='queen', status='running', provider='claude', pid=42)
        pair = bees.prepare_agent_worktree(self.env_b2, REL, 'P1')
        bees.record_agent_branch_start(self.env_b2, 'Ada', *pair)
        self.git('checkout', '-b', 'foreign', cwd=pair[0])
        self.guarded_commit(pair[0])
        args = cli.build_parser().parse_args(['agent', 'result', '--project', 'testproj', '--plan', REL, '--package', 'P1', '--class', 'medium', '--attempt', 'Ada', '--outcome', 'succeeded'])
        output = io.StringIO()
        with mock.patch.dict('os.environ', {'BZZ_AGENT': 'queen'}), mock.patch.object(cli.config, 'load_config', return_value=self.machine), contextlib.redirect_stdout(output):
            self.assertEqual(cli.cmd_bee(args, self.conn), 0, output.getvalue())
        result = json.loads(output.getvalue())
        self.assertEqual((result['outcome'], result['foreign_branch']), ('failed', 'foreign'))
        self.assertEqual(bees.failure_count(self.conn, self.env_b2.name, REL, 'P1', 'medium'), 1)
        self.assertFalse(bees.package_done(self.conn, self.env_b2.name, REL, 'P1'))

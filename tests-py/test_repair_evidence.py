"""Repair decisions require evidence rather than an agent merely ending."""
import json
import sys
import time
from unittest import mock
from bzztest import REL
from haivequeen import action_logs, db, integration, loop, orchestrator
from test_o1_orchestrator import OrchCase


class RepairEvidence(OrchCase):
    def setUp(self):
        super().setUp()
        self.check = 'echo original-failure && exit 7'
        self.project_toml()
        self.ready_plan()

    def test_no_change_does_not_repeat_the_failed_pipeline(self):
        self.round()
        first = self.fixers()[0]
        log = action_logs.latest('testproj', REL, 'Check')['path']
        self.end_fixer(first, 'No repair was made; inspect the shared fixture next.')
        self.round()
        self.assertEqual(action_logs.latest('testproj', REL, 'Check')['path'], log)
        self.assertEqual(self.state()['phase'], 'fix')
        self.round()
        prompt = (self.env().run_dir(self.fixers()[-1]['name']) / 'prompt.md').read_text(encoding='utf-8')
        self.assertIn('No repair was made; inspect the shared fixture next.', prompt)
        self.assertIn(log, prompt)

    def test_empty_commit_is_not_repair_evidence(self):
        self.round()
        log = action_logs.latest('testproj', REL, 'Check')['path']
        self.git('commit', '--allow-empty', '-qm', 'empty attempt', cwd=self.pwt)
        self.end_fixer(self.fixers()[0])
        self.round()
        self.assertEqual(action_logs.latest('testproj', REL, 'Check')['path'], log)

    def test_run_records_tested_commit_and_command(self):
        self.round()
        records = action_logs.read(action_logs.latest('testproj', REL, 'Check')['path'])
        evidence = next(r for r in records if r['event'] == 'execution')
        self.assertEqual(evidence['commit'], self.git('rev-parse', 'HEAD', cwd=self.pwt))
        self.assertEqual(evidence['command'], self.check)
        self.assertEqual(next(r for r in records if r['event'] == 'end')['exit_code'], 7)

    def test_cancelled_worker_is_not_reported_as_project_failure(self):
        path = action_logs.start('testproj', REL, 'Build')
        job = orchestrator.Job('test-job')
        job.log_file = path
        job.cancelled = True
        orchestrator._work({}, job, 'Build', 'unused', self.pwt, None, self.base / 'result.json')
        end = action_logs.read(path)[-1]
        self.assertEqual(end['result'], 'cancelled')

    def test_second_failure_keeps_history_until_full_pipeline_passes(self):
        self.check = 'git ls-files --error-unmatch first-fix.txt'
        self.build = 'git ls-files --error-unmatch second-fix.txt'
        self.project_toml()
        self.round()
        first = self.fixers()[0]
        self.commit(self.pwt, 'first-fix.txt')
        self.end_fixer(first, 'First failure repaired and targeted check passed.')
        self.round()
        history = orchestrator.repair_history(self.conn, 'testproj', REL)
        self.assertFalse(any(x['event'] == 'verified' for x in history))
        self.assertEqual([x['step'] for x in history if x['event'] == 'failure'], ['Check', 'Build'])
        second = self.fixers()[-1]
        prompt = (self.env().run_dir(second['name']) / 'prompt.md').read_text(encoding='utf-8')
        self.assertIn('First failure repaired and targeted check passed.', prompt)
        self.commit(self.pwt, 'second-fix.txt')
        self.end_fixer(second, 'Second failure repaired.')
        self.round()
        history = orchestrator.repair_history(self.conn, 'testproj', REL)
        verified = [x for x in history if x['event'] == 'verified']
        self.assertEqual(len(verified), 1)
        self.assertEqual(verified[0]['commit'], self.remote_head())
        self.assertIn('Check', verified[0]['steps'])
        self.assertIn('Build', verified[0]['steps'])


class TargetedVerification(OrchCase):
    def setUp(self):
        self.check = 'git ls-files --error-unmatch repaired.txt'
        super().setUp()
        self.ready_plan()
        self.round()
        self.agent = self.fixers()[0]
        self.failure_log = self.state()['fix']['log_file']
        self.check_log = action_logs.latest('testproj', REL, 'Check')['path']
        self.commit(self.pwt, 'repaired.txt')

    def end_with_evidence(self, command, commit=None):
        self.end_fixer(self.agent)
        evidence = {'command': command,
                    'commit': commit or self.git('rev-parse', 'HEAD', cwd=self.pwt),
                    'failure_log': self.failure_log,
                    'reason': 'Verify the tracked repair for the original missing-file failure.'}
        (self.env().run_dir(self.agent['name']) / 'repair-verification.json').write_text(
            json.dumps(evidence), encoding='utf-8')

    def test_missing_verification_does_not_restart_pipeline(self):
        self.end_fixer(self.agent, verification=False)
        self.round()
        self.assertEqual(action_logs.latest('testproj', REL, 'Check')['path'], self.check_log)
        self.assertEqual(self.state()['phase'], 'fix')

    def test_verification_prompt_uses_unique_agent_run_directory(self):
        self.end_fixer(self.agent, verification=False)
        with mock.patch.object(loop, 'agent_identity', return_value={'Person': self.agent['name'], 'Color': 'FFFFFF'}):
            self.round()
        agent = self.fixers()[-1]
        self.assertNotEqual(agent['name'], self.agent['name'])
        folder = self.env().run_dir(agent['name'])
        prompt = (folder / 'prompt.md').read_text(encoding='utf-8')
        self.assertIn(str(folder / 'repair-verification.json'), prompt)

    def test_failed_targeted_verification_returns_to_diagnosis(self):
        self.end_with_evidence('echo targeted-regression-failed && exit 9')
        self.round()
        self.assertEqual(action_logs.latest('testproj', REL, 'Check')['path'], self.check_log)
        history = orchestrator.repair_history(self.conn, 'testproj', REL)
        result = next(x for x in history if x['event'] == 'targeted-verification')
        self.assertEqual(result['exit_code'], 9)
        self.assertIn('targeted-regression-failed', action_logs.output(result['log_file']))

    def test_stale_commit_evidence_is_not_executed(self):
        marker = self.base / 'must-not-run.txt'
        self.end_with_evidence('echo unexpected > "' + str(marker) + '"', commit='0' * 40)
        self.round()
        self.assertFalse(marker.exists())
        self.assertEqual(action_logs.latest('testproj', REL, 'Check')['path'], self.check_log)

    def test_successful_targeted_check_precedes_full_integration(self):
        self.end_with_evidence(self.check)
        self.round()
        history = orchestrator.repair_history(self.conn, 'testproj', REL)
        events = [x['event'] for x in history]
        self.assertIn('targeted-verification', events)
        self.assertLess(events.index('targeted-verification'), events.index('pipeline-retry'))
        self.assertLess(events.index('pipeline-retry'), events.index('verified'))
        self.assertEqual(self.plan_status(), 'REVIEW')

    def test_verification_survives_observer_restart_without_duplicate_command(self):
        self.end_with_evidence(self.check)
        orchestrator.ASYNC = True
        with mock.patch.object(integration, 'poll_compile_check', return_value=None):
            self.round()
        job = self.state()['job']
        self.assertEqual(job['step'], 'RepairCheck')
        orchestrator.JOBS.pop(('testproj', REL), None)
        deadline = time.monotonic() + 30
        while integration.poll_compile_check(self.conn, 'testproj', job) is None:
            self.assertLess(time.monotonic(), deadline)
            time.sleep(.1)
        orchestrator.ASYNC = False
        # Prevent the next normal pipeline stage from starting: only recover the
        # existing targeted result after losing the observer's in-memory Job.
        with mock.patch.object(orchestrator, '_advance_pipeline'), mock.patch.object(
                integration, 'start_compile_check') as start:
            self.round()
        start.assert_not_called()
        self.assertEqual(self.state()['phase'], 'pipeline')
        results = [x for x in orchestrator.repair_history(self.conn, 'testproj', REL)
                   if x['event'] == 'targeted-verification']
        self.assertEqual(len(results), 1)
        self.assertEqual(results[0]['exit_code'], 0)

    def test_verification_access_failure_waits_for_user_instead_of_repair_loop(self):
        command = ('"%s" -c "import pathlib,sys; print(\'fatal: Authentication failed\'); '
                   'sys.exit(0 if pathlib.Path(\'access-granted.txt\').exists() else 128)"') % sys.executable
        self.end_with_evidence(command)
        self.round()
        self.assertEqual(self.state()['phase'], 'blocked')
        questions = self.question('integration-access-repaircheck')
        self.assertEqual(len(questions), 1)
        self.round()
        self.assertEqual(len(self.fixers()), 1)
        (self.pwt / 'access-granted.txt').write_text('external credential corrected', encoding='utf-8')
        db.answer_question(self.conn, questions[0]['id'], 'Access corrected; retry.', 'owner')
        self.round()
        self.assertEqual(self.plan_status(), 'REVIEW')


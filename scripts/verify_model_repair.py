"""Opt-in real-model acceptance; all Git, databases and jobs remain in a temporary fixture.
Run with --model and --effort from the configured diagnosis class. Never part of CI.
The scheduler's process lifecycle is simulated; model diagnosis, edits and commits are real.
"""
import argparse
import csv
import json
import os
from pathlib import Path
import shutil
import subprocess
import sys
import tempfile
import unittest

ROOT = Path(__file__).resolve().parents[1]
sys.path[:0] = [str(ROOT), str(ROOT / 'tests-py')]
from test_merge_repair_scenario import MergeRepairScenario
from haivequeen import integration, repair_review, orchestrator


def model_command(executable, model, effort, fixture, *, windows=False):
    command = [executable, 'exec', '--ephemeral', '--ignore-user-config', '--ignore-rules',
               '--approve-for-me', '--add-dir', str(fixture),
               '--model', model, '-c', 'model_reasoning_effort="%s"' % effort]
    if windows:
        # Ignoring user config must not disable the installed Windows sandbox.
        command += ['-c', 'windows.sandbox="elevated"']
    return command + ['--json', '-']


def model_environment(original, repositories):
    environment = dict(original)
    count = int(environment.get('GIT_CONFIG_COUNT', '0'))
    for repository in repositories:
        environment['GIT_CONFIG_KEY_%d' % count] = 'safe.directory'
        environment['GIT_CONFIG_VALUE_%d' % count] = str(Path(repository).resolve())
        count += 1
    environment['GIT_CONFIG_COUNT'] = str(count)
    return environment


def grant_fixture_access(folder):
    # Temporary directories grant OWNER RIGHTS, which follows the sandbox account
    # for new files. Preserve access for the test runner without changing global ACLs.
    identity = subprocess.check_output(['whoami', '/user', '/fo', 'csv', '/nh'], text=True)
    sid = next(csv.reader(identity.strip().splitlines()))[1]
    subprocess.run(['icacls', str(folder), '/grant', '*' + sid + ':(OI)(CI)F', '/T', '/Q'],
                   check=True, capture_output=True, text=True)


def repair_prompt(step, command, history):
    artifact = ('The synthetic build requires an index.txt artifact; its content is unconstrained. '
                if step == 'Build' else '')
    return ('Repair only the current %s failure in this isolated fixture. Inspect Git status and '
            'the actual error. Preserve behavior and all tests. Run this required verification: %s\n'
            'Repair and commit only %s using explicit file paths. Leave the worktree clean. '
            'Do not push, access other projects, change the test command, or preempt later steps. '
            '%sPrevious findings: %s' % (step, command, 'RenderManager.cpp' if step == 'Merge' else 'index.txt', artifact, json.dumps(history[-3:])))


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--model', required=True)
    parser.add_argument('--effort', required=True, choices=['low', 'medium', 'high'])
    parser.add_argument('--evidence', type=Path, required=True)
    parser.add_argument('--test-data', type=Path, required=True, help='Existing test data folder for disposable fixtures')
    args = parser.parse_args()
    if not args.test_data.is_dir():
        parser.error('--test-data must name an existing test data folder')
    tempfile.tempdir = str(args.test_data.resolve())
    executable = shutil.which('codex.cmd' if os.name == 'nt' else 'codex')
    if not executable:
        parser.error('Codex CLI is required')
    args.evidence.mkdir(parents=True, exist_ok=True)

    class RealModelRepair(MergeRepairScenario):
        calls = 0

        def setUp(self):
            super().setUp()
            if os.name == 'nt':
                grant_fixture_access(self.base)

        def call_model(self, folder, prompt):
            self.calls += 1
            result = subprocess.run(model_command(executable, args.model, args.effort, self.base, windows=os.name == 'nt'),
                cwd=folder, input=prompt, text=True, encoding='utf-8', errors='replace',
                env=model_environment(os.environ, [self.repo, folder]),
                capture_output=True, timeout=240)
            (args.evidence / ('model-%d.jsonl' % self.calls)).write_text(result.stdout, encoding='utf-8')
            (args.evidence / ('model-%d.stderr' % self.calls)).write_text(result.stderr, encoding='utf-8')
            self.assertEqual(result.returncode, 0, result.stderr[-2000:])
            messages = [json.loads(line).get('item', {}) for line in result.stdout.splitlines() if line.strip()]
            return next((item['text'] for item in reversed(messages) if item.get('type') == 'agent_message'), '')

        def diagnose(self, agent, folder, fix):
            known = repair_review.decisions(self.conn, 'testproj', agent['plan_path'])
            report = folder / 'acceptance-diagnosis.json'
            prompt = repair_review.prompt(str(report), integration.head_commit(folder),
                fix.get('log_file', ''), known,
                orchestrator.repair_history(self.conn, 'testproj', agent['plan_path']))
            final = self.call_model(folder, 'Isolated acceptance fixture. No production access or network Git operations. '
                'All decisions for this fixture are supplied below; do not call bzz. Inspect local Git conflict stages.\n' + prompt +
                '\nFor this acceptance, return the required JSON object as your final response instead of writing a file. No Markdown fences.')
            report_data = json.loads(final)
            (self.env().run_dir(agent['name']) / 'repair-diagnosis.json').write_text(json.dumps(report_data), encoding='utf-8')

        def repair(self, agent, folder, fix):
            command = self.check if fix['step'] == 'Merge' else self.build
            history = orchestrator.repair_history(self.conn, 'testproj', agent['plan_path'])
            prompt = repair_prompt(fix['step'], command, history)
            self.call_model(folder, prompt)
            self.assertFalse(self.git('diff', '--name-only', '--diff-filter=U', cwd=folder))

    suite = unittest.TestSuite([RealModelRepair('test_comment_conflicts_and_followup_failure_finish_both_plans')])
    result = unittest.TextTestRunner(verbosity=2).run(suite)
    (args.evidence / 'result.json').write_text(json.dumps({'passed': result.wasSuccessful(),
        'tests': result.testsRun, 'model': args.model, 'effort': args.effort,
        'scope': 'real model diagnosis and repairs; fixture-controlled agent lifecycle; real Git and checks'}), encoding='utf-8')
    return 0 if result.wasSuccessful() else 1

if __name__ == '__main__':
    raise SystemExit(main())

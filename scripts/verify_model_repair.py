"""Opt-in real-model acceptance; all Git, databases and jobs remain in a temporary fixture.
Run with --model and --effort from the configured diagnosis class. Never part of CI.
The scheduler's process lifecycle is simulated; model diagnosis and code edits are real.
"""
import argparse
import json
import os
from pathlib import Path
import shutil
import subprocess
import sys
import unittest

ROOT = Path(__file__).resolve().parents[1]
sys.path[:0] = [str(ROOT), str(ROOT / 'tests-py')]
from test_merge_repair_scenario import MergeRepairScenario
from haivequeen import integration, repair_review, orchestrator


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--model', required=True)
    parser.add_argument('--effort', required=True, choices=['low', 'medium', 'high'])
    parser.add_argument('--evidence', type=Path, required=True)
    args = parser.parse_args()
    executable = shutil.which('codex.cmd' if os.name == 'nt' else 'codex')
    if not executable:
        parser.error('Codex CLI is required')
    args.evidence.mkdir(parents=True, exist_ok=True)

    class RealModelRepair(MergeRepairScenario):
        calls = 0

        def call_model(self, folder, prompt):
            self.calls += 1
            result = subprocess.run([executable, 'exec', '--ephemeral', '--ignore-user-config',
                '--ignore-rules', '--sandbox', 'workspace-write', '--model', args.model,
                '-c', 'model_reasoning_effort="%s"' % args.effort, '--json', '-'],
                cwd=folder, input=prompt, text=True, encoding='utf-8', errors='replace',
                capture_output=True, timeout=240)
            (args.evidence / ('model-%d.jsonl' % self.calls)).write_text(result.stdout, encoding='utf-8')
            (args.evidence / ('model-%d.stderr' % self.calls)).write_text(result.stderr, encoding='utf-8')
            self.assertEqual(result.returncode, 0, result.stderr[-2000:])

        def diagnose(self, agent, folder, fix):
            known = repair_review.decisions(self.conn, 'testproj', agent['plan_path'])
            report = folder / 'acceptance-diagnosis.json'
            prompt = repair_review.prompt(str(report), integration.head_commit(folder),
                fix.get('log_file', ''), known,
                orchestrator.repair_history(self.conn, 'testproj', agent['plan_path']))
            self.call_model(folder, 'Isolated acceptance fixture. No production access or network Git operations. '
                'All decisions for this fixture are supplied below; do not call bzz. Inspect local Git conflict stages.\n' + prompt)
            self.assertTrue(report.exists(), 'Model did not write its diagnosis')
            shutil.copyfile(report, self.env().run_dir(agent['name']) / 'repair-diagnosis.json')
            report.unlink()

        def repair(self, agent, folder, fix):
            command = self.check if fix['step'] == 'Merge' else self.build
            history = orchestrator.repair_history(self.conn, 'testproj', agent['plan_path'])
            prompt = ('Repair the current %s failure in this isolated fixture. Inspect Git status and '
                'the actual error. Preserve behavior and all tests. Run this required verification: %s\n'
                'Commit only your repaired files with explicit paths. Do not push, access other projects, '
                'change the test command, or run unrelated checks. The synthetic build requires a generated '
                'index.txt artifact; its content is unconstrained. Previous findings: %s' %
                (fix['step'], command, json.dumps(history[-3:])))
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

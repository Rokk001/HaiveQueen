"""Actual Git/check evidence: observation commits cannot masquerade as repairs."""
import copy
import json
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path
from haivequeen import repair_contract


class ImplementationEvidence(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.repo = self.root / 'repo'; self.repo.mkdir()
        self.logs = self.root / 'logs'; self.logs.mkdir()
        self.git('init', '-q')
        self.git('config', 'user.name', 'Test')
        self.git('config', 'user.email', 'test@example.invalid')
        (self.repo / 'subject.py').write_text('value = 0\n')
        (self.repo / 'regression.py').write_text('from subject import value\nassert value == 1\n')
        self.git('add', 'subject.py', 'regression.py'); self.git('commit', '-qm', 'Baseline defect')
        self.before = self.git('rev-parse', 'HEAD')
        self.command = 'python -B regression.py'
        result = self.check()
        self.assertNotEqual(result.returncode, 0)
        (self.logs / 'before.log').write_text(result.stdout + result.stderr)
        (self.logs / 'regression.snapshot').write_bytes((self.repo / 'regression.py').read_bytes())
        self.contract = dict(objective='Repair value', why_executable='Isolated defect', prerequisites=[], requires=[], resolves=[],
            repair=dict(goal='Return the required value', behavior='value equals one', criterion='value',
                        implementation_paths=['subject.py'], regression_paths=['regression.py']),
            criteria=[dict(id='value', expectation='value equals one', check=self.command)])
        self.assigned = dict(attempt='repair', commit=self.before, contract=self.contract, id=repair_contract.identity(self.contract))
        self.baseline = dict(commit=self.before, command=self.command, exit=result.returncode,
            log='before.log', regression_files={'regression.py':'regression.snapshot'})

    def git(self, *args):
        return subprocess.run(['git', '-C', str(self.repo), *args], check=True,
                              capture_output=True, text=True, timeout=10).stdout.strip()

    def check(self):
        return subprocess.run([sys.executable, '-B', 'regression.py'], cwd=self.repo,
                              capture_output=True, text=True, timeout=10)

    def receipt(self):
        result = self.check(); self.assertEqual(result.returncode, 0, result.stderr)
        (self.logs / 'after.log').write_text((result.stdout + result.stderr) or 'check returned 0')
        data = dict(attempt='repair', contract=self.assigned['id'], commit=self.git('rev-parse', 'HEAD'),
            baseline=self.baseline, checks=[dict(id='value', command=self.command, exit=0, log='after.log')])
        path = self.logs / 'receipt.json'; path.write_text(json.dumps(data))
        return repair_contract.result(path, self.assigned, 'repair', data['commit'])

    def fix(self):
        (self.repo / 'subject.py').write_text('value = 1\n')
        self.git('add', 'subject.py'); self.git('commit', '-qm', 'Correct behavior')

    def test_actual_red_green_implementation_is_accepted(self):
        self.fix()
        receipt = repair_contract.implementation_evidence(self.receipt(), self.assigned, self.repo)
        self.assertEqual(receipt['implementation']['changed'], ['subject.py'])
        self.assertEqual(receipt['implementation']['before'], self.before)

    def test_green_characterization_commit_is_rejected(self):
        (self.repo / 'regression.py').write_text('from subject import value\nassert value == 0\n')
        self.git('add', 'regression.py'); self.git('commit', '-qm', 'Characterize observed defect')
        with self.assertRaisesRegex(ValueError, 'investigation-only'):
            repair_contract.implementation_evidence(self.receipt(), self.assigned, self.repo)

    def test_incidental_source_change_cannot_hide_changed_assertions(self):
        (self.repo / 'subject.py').write_text('value = 0  # still broken\n')
        (self.repo / 'regression.py').write_text('from subject import value\nassert value == 0\n')
        self.git('add', 'subject.py', 'regression.py'); self.git('commit', '-qm', 'Describe the defect')
        with self.assertRaisesRegex(ValueError, 'assertions changed'):
            repair_contract.implementation_evidence(self.receipt(), self.assigned, self.repo)

    def test_missing_wrong_or_escaping_baseline_cannot_count_as_progress(self):
        self.fix(); receipt = self.receipt()
        for key, value in [('commit', 'wrong'), ('exit', 0), ('command', 'different check'),
                           ('log', '../outside.log'), ('regression_files', {})]:
            bad = copy.deepcopy(receipt); bad['baseline'][key] = value
            with self.subTest(key=key), self.assertRaises(ValueError):
                repair_contract.implementation_evidence(bad, self.assigned, self.repo)

    def test_no_repository_evidence_cannot_count_as_progress(self):
        self.fix()
        with self.assertRaisesRegex(ValueError, 'checkout is unavailable'):
            repair_contract.implementation_evidence(self.receipt(), self.assigned, None)

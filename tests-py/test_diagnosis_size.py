"""Bounded diagnosis input and scheduler correction-to-repair flow."""
import copy
import json
import tempfile
import unittest
from pathlib import Path
from unittest import mock

from bzztest import REL
import test_repair_scope as fixtures
from haivequeen import bees, package_diagnosis as diagnosis, repair_review, runtime


class SizeLimits(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.path = Path(self.temp.name) / 'diagnosis.json'
        self.report = dict(kind='technical', commit='head', failure_log='failure',
                           reviewed_decisions=[], evidence=['observed'], analysis='cause',
                           next_action='repair', why_new='new observation')

    def read(self, data):
        self.path.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding='utf-8')
        return repair_review.read(self.path, 'head', 'failure', [])

    def test_large_evidence_and_structured_contract_are_preserved(self):
        self.report['evidence'] = ['observed: ' + 'x' * 34000]
        self.report['contract'] = {'detail': 'x' * 39000}
        self.assertEqual(self.read(self.report), self.report)
        self.assertGreater(self.path.stat().st_size, 65536)

    def test_budgets_count_utf8_and_reject_each_category(self):
        for key, value, expected in (
            ('analysis', 'x' * repair_review.CORE_BYTES, 'structured report'),
            ('evidence', ['\u00e9' * (repair_review.EVIDENCE_BYTES // 2)], 'evidence')):
            data = dict(self.report, **{key: value})
            with self.subTest(key=key), self.assertRaisesRegex(ValueError, expected + ' uses'):
                self.read(data)
        self.path.write_bytes(b' ' * (repair_review.REPORT_BYTES + 1))
        with self.assertRaisesRegex(ValueError, 'file exceeds'):
            repair_review.read_document(self.path)

    def test_large_reports_do_not_bypass_identity_decisions_or_schema(self):
        self.report['evidence'] = ['observed ' + 'x' * 70000]
        for field, value in [('commit', 'wrong'), ('failure_log', 'other'),
                             ('reviewed_decisions', [999]), ('kind', 'unknown'), ('analysis', '')]:
            with self.subTest(field=field), self.assertRaises(ValueError):
                self.read(dict(self.report, **{field: value}))
        self.path.write_text('{broken', encoding='utf-8')
        with self.assertRaises(ValueError):
            repair_review.read_document(self.path)

    def test_prompts_publish_budgets_and_preserve_evidence(self):
        prompts = [repair_review.prompt(self.path, 'head', 'failure', [], []),
                   repair_review.failure_report_prompt(self.path, 'head', 'failure', []),
                   repair_review.correction_prompt(self.path.parent, 'too large')]
        for prompt in prompts:
            for limit in (repair_review.REPORT_BYTES, repair_review.CORE_BYTES, repair_review.EVIDENCE_BYTES):
                self.assertIn(str(limit), prompt)
            self.assertIn('never truncate', prompt)


class SizeFlow(fixtures.RepairScope):
    def test_size_correction_review_and_repair_without_owner_block(self):
        scope, report = self.report()
        prompt = diagnosis.prompt(self.conn, 'testproj', REL, 'P1', 'head-1', self.base / 'out.json', 'medium', ['medium'])
        self.assertIn(str(repair_review.CORE_BYTES), prompt)
        self.assertIn(str(repair_review.EVIDENCE_BYTES), prompt)
        report['analysis'] = 'x' * 66000
        folder = self.env().run_dir('oversize'); folder.mkdir(parents=True, exist_ok=True)
        path = folder / 'package-diagnosis.json'
        path.write_text(json.dumps(report), encoding='utf-8')
        self.agent('oversize', phase='fix', status='done')
        runtime.kv_set(self.conn, 'testproj', diagnosis._key(REL, 'P1'), json.dumps(dict(
            phase='running', agent='oversize', scope=scope, current_class='medium', report_path=str(path.resolve()))))
        with mock.patch('haivequeen.package_diagnosis.current_commit', return_value='head-1'), mock.patch(
                'haivequeen.readygate._launch_check_agent', return_value=('size-review', '')) as launch:
            diagnosis.advance(self.env(), 8192, alive=lambda pid: False)
            state = diagnosis._load(self.conn, 'testproj', REL, 'P1')
            self.assertEqual(state['phase'], 'pending')
            self.assertIn('structured report uses', state['feedback'])
            self.assertIsNone(state.get('authorization'))
            launch.assert_not_called()
            corrected = copy.deepcopy(report)
            corrected['analysis'] = 'Cause retained with source references.'
            corrected['evidence'].append('Observed details: ' + 'x' * 70000)
            new_folder = self.env().run_dir('corrected'); new_folder.mkdir(parents=True, exist_ok=True)
            new_path = new_folder / 'package-diagnosis.json'
            new_path.write_text(json.dumps(corrected), encoding='utf-8')
            self.agent('corrected', phase='fix', status='done')
            state.update(phase='running', agent='corrected', report_path=str(new_path.resolve()))
            runtime.kv_set(self.conn, 'testproj', diagnosis._key(REL, 'P1'), json.dumps(state))
            diagnosis.advance(self.env(), 8192, alive=lambda pid: False)
            self.assertEqual(launch.call_count, 1)
            self.assertIsNone(diagnosis.authorization(self.conn, 'testproj', REL, 'P1', scope))
            self.agent('size-review', phase='verify', status='done')
            review_dir = self.env().run_dir('size-review'); review_dir.mkdir(parents=True, exist_ok=True)
            (review_dir / 'final.txt').write_text('READY-CHECK: PASSED', encoding='utf-8')
            diagnosis.advance(self.env(), 8192, alive=lambda pid: False)
        self.assertIsNotNone(diagnosis.authorization(self.conn, 'testproj', REL, 'P1', scope))
        self.assertFalse(runtime.list_questions(self.conn, 'testproj', status='open', plan=REL))
        self.assertTrue(self.start('bounded-repair'))
        self.assertEqual(bees.failure_count(self.conn, 'testproj', REL, 'P1', 'medium'), 1)
        self.assertEqual(json.loads(path.read_text()), report)

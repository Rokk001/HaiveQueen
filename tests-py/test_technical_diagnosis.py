"""Exhausted integration repairs require agent diagnosis, not an owner decision."""
import json
from bzztest import REL
from haivequeen import db, orchestrator, repair_review, runtime
from test_o1_orchestrator import OrchCase


class TechnicalDiagnosis(OrchCase):
    def setUp(self):
        super().setUp()
        self.check = 'echo original-failure && exit 7'
        self.project_toml()
        self.ready_plan()

    def start_review(self):
        self.machine.update({'queenClass': 'heavy', 'diagnosisClass': 'light', 'escalateAfter': 1})
        self.round()
        self.end_fixer(self.fixers()[0], 'Could not determine the cause.')
        self.round()
        self.round()
        return self.fixers()[-1]

    def finish_review(self, kind='technical', finish=True, **overrides):
        agent = self.fixers()[-1]
        state = self.state()
        record = dict(kind=kind, commit=state['fix']['head'], failure_log=state['fix']['log_file'],
                      reviewed_decisions=[d['id'] for d in repair_review.decisions(self.conn, 'testproj', REL)],
                      evidence=['source.cpp: the conflict contains two comment formulations'],
                      analysis='Both comments describe the same existing behavior.',
                      next_action='Combine the comment explanations without changing behavior.',
                      why_new='Earlier attempts did not inspect the actual conflict.',
                      question='Which product behavior should be retained?',
                      why_owner='The requirements specify two mutually exclusive user behaviors.',
                      recommendation='Choose the documented behavior.')
        record.update(overrides)
        (self.env().run_dir(agent['name']) / 'repair-diagnosis.json').write_text(json.dumps(record), encoding='utf-8')
        self.end_fixer(agent, verification=False)
        if finish:
            orchestrator._finish_review(self.conn, self.env(), REL, state)
        return record

    def test_technical_review_authorizes_one_repair_and_keeps_attempt_history(self):
        agent = self.start_review()
        self.assertEqual(agent['class'], 'light')
        self.finish_review()
        self.assertTrue(self.state()['fix']['review_authorized'])
        self.assertEqual(orchestrator._attempts(self.conn, self.env(), REL)['n'], 1)
        self.round()
        self.assertFalse(self.state()['fix'].get('review_authorized'))
        self.assertFalse(self.state()['fix']['diagnosis'])
        self.assertEqual(orchestrator._attempts(self.conn, self.env(), REL)['n'], 2)
        self.assertFalse(self.question('fix-escalation'))

    def test_product_review_asks_specific_question(self):
        self.start_review()
        record = self.finish_review('product')
        question = self.question('fix-escalation')[0]
        self.assertIn(record['question'], question['question'])
        self.assertIn(record['why_owner'], question['question'])
        self.assertIn(record['evidence'][0], question['question'])
        self.assertEqual(self.state()['phase'], 'blocked')

    def test_no_change_requires_diagnosis_before_spending_retry_budget(self):
        self.machine.update({'escalateAfter': 3, 'diagnosisClass': 'light'})
        self.round()
        self.end_fixer(self.fixers()[0], 'No new evidence and no repair.')
        self.round()
        self.round()
        self.assertTrue(self.state()['fix']['diagnosis'])
        self.assertEqual(self.fixers()[-1]['class'], 'light')
        self.assertEqual(orchestrator._attempts(self.conn, self.env(), REL)['n'], 1)

    def test_existing_fix_diagnosis_avoids_an_extra_agent(self):
        self.machine.update({'escalateAfter': 3})
        self.round()
        self.finish_review(finish=False)
        self.round()
        self.round()
        self.assertFalse(self.state()['fix']['diagnosis'])
        self.assertEqual(len(self.fixers()), 2)
        self.assertEqual(len([e for e in orchestrator.repair_history(self.conn, 'testproj', REL)
                              if e['event'] == 'diagnosis-accepted']), 1)

    def test_stronger_model_needs_explicit_diagnosis_reason(self):
        self.machine.update({'escalateAfter': 1, 'diagnosisClass': 'light'})
        self.round()
        self.end_fixer(self.fixers()[0], 'No repair.')
        self.round()
        self.round()
        self.finish_review(repair_class='heavy', class_reason='The failed attempt missed interactions across three modules.')
        self.round()
        self.assertFalse(self.state()['fix']['diagnosis'])
        self.assertEqual(self.fixers()[-1]['class'], 'heavy')

    def test_commit_changed_after_review_requires_new_diagnosis(self):
        self.start_review()
        self.finish_review()
        self.commit(self.pwt, 'later-change.txt')
        self.round()
        self.assertTrue(self.state()['fix']['diagnosis'])
        self.assertFalse(self.state()['fix'].get('review_authorized'))
        self.assertEqual(orchestrator._attempts(self.conn, self.env(), REL)['n'], 1)

    def test_stale_grant_below_retry_threshold_requires_new_diagnosis(self):
        self.machine.update({'escalateAfter': 3})
        self.round()
        self.finish_review()
        self.assertTrue(self.state()['fix']['review_authorized'])
        self.commit(self.pwt, 'changed-after-review.txt')
        self.round()
        self.assertTrue(self.state()['fix']['diagnosis'])
        self.assertEqual(orchestrator._attempts(self.conn, self.env(), REL)['n'], 1)

    def test_malformed_model_class_is_rejected_without_crashing(self):
        self.start_review()
        self.finish_review(repair_class={'model': 'heavy'}, class_reason='More reasoning needed.')
        self.assertFalse(self.state()['fix'].get('review_authorized'))
        self.assertIsNone(self.state()['fix']['agent'])
        self.assertTrue(any(e['event'] == 'diagnosis-rejected'
                            for e in orchestrator.repair_history(self.conn, 'testproj', REL)))

    def test_decision_changed_after_review_requires_new_diagnosis(self):
        self.start_review()
        self.finish_review()
        runtime.add_decision(self.conn, 'testproj', 'owner', 'Preserve the original comments.', plan=REL)
        self.round()
        self.assertTrue(self.state()['fix']['diagnosis'])
        self.assertFalse(self.state()['fix'].get('review_authorized'))
        self.assertEqual(orchestrator._attempts(self.conn, self.env(), REL)['n'], 1)

    def test_identical_failed_approach_is_not_authorized_twice(self):
        self.start_review()
        self.finish_review()
        self.round()
        self.end_fixer(self.fixers()[-1], 'No change; the proposed approach did not resolve the failure.')
        self.round()
        self.round()
        self.assertTrue(self.state()['fix']['diagnosis'])
        self.finish_review()
        self.assertFalse(self.state()['fix'].get('review_authorized'))
        self.assertIn('already been authorized', self.state()['fix']['instruction'])
        self.assertFalse(self.question('fix-escalation'))

    def test_stale_review_cannot_authorize_repair_or_ask_owner(self):
        self.start_review()
        self.finish_review(commit='stale-commit')
        self.assertFalse(self.state()['fix'].get('review_authorized'))
        self.assertFalse(self.question('fix-escalation'))
        self.assertIn('current commit', self.state()['fix']['instruction'])

    def test_changed_decision_requires_new_review(self):
        self.start_review()
        runtime.add_decision(self.conn, 'testproj', 'owner', 'Keep both comment explanations.', plan=REL)
        self.finish_review()
        self.assertFalse(self.state()['fix'].get('review_authorized'))
        self.assertFalse(self.question('fix-escalation'))
        self.assertIn('decisions changed', self.state()['fix']['instruction'])

    def test_existing_decision_is_available_to_diagnosis(self):
        runtime.add_decision(self.conn, 'testproj', 'owner', 'Keep both comment explanations.', plan=REL)
        agent = self.start_review()
        prompt = (self.env().run_dir(agent['name']) / 'prompt.md').read_text(encoding='utf-8')
        self.assertIn('Keep both comment explanations.', prompt)
        self.finish_review()
        self.assertTrue(self.state()['fix']['review_authorized'])
        self.assertFalse(self.question('fix-escalation'))

    def test_product_answer_preserves_attempt_count(self):
        self.start_review()
        self.finish_review('product')
        db.answer_question(self.conn, self.question('fix-escalation')[0]['id'], 'Retain the documented behavior.', 'owner')
        orchestrator._advance_blocked(self.conn, self.env(), REL, self.state())
        self.assertEqual(orchestrator._attempts(self.conn, self.env(), REL)['n'], 1)
        self.assertTrue(self.state()['fix']['review_authorized'])

    def test_diagnosed_repair_requires_targeted_and_full_pipeline_success(self):
        self.check = 'git ls-files --error-unmatch repaired.txt'
        self.project_toml()
        self.start_review()
        self.finish_review()
        self.round()
        self.assertFalse(any(e['event'] == 'verified' for e in orchestrator.repair_history(self.conn, 'testproj', REL)))
        self.commit(self.pwt, 'repaired.txt')
        self.end_fixer(self.fixers()[-1], 'Committed repair with targeted verification.')
        self.round()
        history = orchestrator.repair_history(self.conn, 'testproj', REL)
        self.assertEqual(len([e for e in history if e['event'] == 'verified']), 1)
        self.assertEqual(self.remote_head(), self.git('rev-parse', 'HEAD', cwd=self.pwt))

    def test_exhaustion_starts_diagnosis_without_owner_question(self):
        self.machine.update({'queenClass': 'heavy', 'escalateAfter': 1})
        self.round()
        self.end_fixer(self.fixers()[0], 'Could not determine the cause.')
        self.round()
        self.round()
        self.assertFalse(self.question('fix-escalation'))
        self.assertEqual(len(self.fixers()), 2)
        self.assertTrue(self.state()['fix'].get('diagnosis'))
        prompt = (self.env().run_dir(self.fixers()[-1]['name']) / 'prompt.md').read_text(encoding='utf-8')
        self.assertIn('repair-diagnosis.json', prompt)
        self.assertIn('Could not determine the cause.', prompt)

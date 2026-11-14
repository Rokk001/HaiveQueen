import json
import sys
from unittest import mock
from bzztest import REL
from haivequeen import orchestrator
from test_o1_orchestrator import OTHER, OrchCase


class SemanticRepair(OrchCase):
    def setUp(self):
        self.check = self.command('first-fix.txt', 'shared fixture assertion failed: required table missing')
        self.build = self.command('second-fix.txt', 'second fixture assertion failed: required index missing')
        super().setUp()
        self.second = self.make_worktree('second-br', 'secondwt')
        self.ready_plan()
        self.ready_plan(OTHER, 'second-br', self.second, 'other.txt')

    @staticmethod
    def command(path, diagnostic):
        script = "import pathlib,sys; ok=pathlib.Path(%r).exists(); print(%r if not ok else 'passed'); sys.exit(0 if ok else 1)" % (path, diagnostic)
        return '"%s" -c "%s"' % (sys.executable, script)

    def separate_failures(self):
        original = orchestrator._red
        def different(conn, env, plan, state, step, error):
            if plan == OTHER:
                error = 'Independent-looking error: schema consumer cannot load the requested relation'
                state.pop('log_file', None)
            return original(conn, env, plan, state, step, error)
        with mock.patch.object(orchestrator, '_red', side_effect=different):
            self.round()
        self.assertEqual(len(self.fixers()), 2)

    def proposal(self):
        from haivequeen import shared_diagnosis
        env = self.env()
        peers = shared_diagnosis.candidates(self.conn, env, OTHER)
        target = next(p for p in peers if p['plan'] == REL)
        return peers, {'plan': REL, 'scope': target['scope'],
                       'cause': 'Both consumers depend on the missing schema fixture.',
                       'evidence': {OTHER: ['consumer.py:12 reads the same schema fixture'],
                                    REL: ['schema.py:8 requires first-fix.txt']}}

    def test_different_errors_share_only_after_diagnosis_and_both_verify(self):
        from haivequeen import shared_diagnosis
        self.separate_failures()
        peers, proposal = self.proposal()
        follower = self.state(OTHER)
        # Its diagnosis has finished; there is no running follower to interrupt.
        worker = next(a for a in self.fixers() if a['plan_path'] == OTHER)
        self.end_fixer(worker)
        from haivequeen import integration, repair_review
        head = integration.head_commit(self.second)
        known = repair_review.decisions(self.conn, 'testproj', OTHER)
        follower['fix'].update(head=head, review_decisions=known, review_peers=peers)
        report = {'kind': 'technical', 'commit': head, 'failure_log': follower['fix'].get('log_file', ''),
                  'reviewed_decisions': [d['id'] for d in known], 'evidence': ['consumer.py:12 missing schema'],
                  'analysis': proposal['cause'], 'next_action': 'Reuse the verified shared schema repair.',
                  'why_new': 'Both failure sources have now been compared.', 'shared_cause': proposal}
        (self.env().run_dir(worker['name']) / 'repair-diagnosis.json').write_text(json.dumps(report), encoding='utf-8')
        orchestrator._finish_review(self.conn, self.env(), OTHER, follower)
        self.assertEqual(self.state(OTHER)['phase'], 'shared-repair')
        first = next(a for a in self.fixers() if a['plan_path'] == REL)
        self.commit(self.pwt, 'first-fix.txt')
        self.end_fixer(first)
        self.round()
        self.assertEqual(self.state(OTHER)['phase'], 'shared-repair')
        self.assertEqual(len(self.fixers()), 3)
        self.commit(self.pwt, 'second-fix.txt')
        self.end_fixer(self.fixers()[-1])
        for _ in range(4):
            self.round()
        self.assertEqual(self.plan_status(), 'REVIEW')
        self.assertEqual(self.plan_status(OTHER), 'REVIEW')
        self.assertTrue((self.second / 'first-fix.txt').exists())
        self.assertTrue((self.second / 'second-fix.txt').exists())

    def test_stale_peer_commit_is_rejected(self):
        from haivequeen import shared_diagnosis
        self.separate_failures()
        peers, proposal = self.proposal()
        self.commit(self.pwt, 'new-evidence.txt')
        with self.assertRaisesRegex(ValueError, 'changed'):
            shared_diagnosis.validate(self.conn, self.env(), OTHER, self.state(OTHER), peers, proposal)

    def test_each_plan_needs_evidence_and_unknown_peer_is_rejected(self):
        from haivequeen import shared_diagnosis
        self.separate_failures()
        peers, proposal = self.proposal()
        proposal['evidence'].pop(OTHER)
        with self.assertRaisesRegex(ValueError, 'evidence'):
            shared_diagnosis.validate(self.conn, self.env(), OTHER, self.state(OTHER), peers, proposal)
        _, proposal = self.proposal()
        with self.assertRaisesRegex(ValueError, 'offered'):
            shared_diagnosis.validate(self.conn, self.env(), OTHER, self.state(OTHER), [], proposal)

    def test_cancelled_peer_is_rejected(self):
        from haivequeen import shared_diagnosis
        self.separate_failures()
        peers, proposal = self.proposal()
        self.conn.execute("UPDATE plans SET status='CANCELLED' WHERE project=? AND path=?", ('testproj', REL))
        with self.assertRaises(ValueError):
            shared_diagnosis.validate(self.conn, self.env(), OTHER, self.state(OTHER), peers, proposal)

    def test_peer_failure_and_decision_changes_invalidate_review(self):
        from haivequeen import shared_diagnosis, repair_review
        self.separate_failures()
        peers, proposal = self.proposal()
        leader = self.state()
        leader['fix']['log_file'] = 'another-run.jsonl'
        orchestrator.save(self.conn, 'testproj', REL, leader)
        with self.assertRaisesRegex(ValueError, 'changed'):
            shared_diagnosis.validate(self.conn, self.env(), OTHER, self.state(OTHER), peers, proposal)
        peers, proposal = self.proposal()
        with mock.patch.object(repair_review, 'decisions', return_value=[{'id': 77, 'text': 'new decision'}]):
            with self.assertRaisesRegex(ValueError, 'changed'):
                shared_diagnosis.validate(self.conn, self.env(), OTHER, self.state(OTHER), peers, proposal)

    def test_diagnosis_start_supplies_peers_without_copying_full_logs(self):
        self.separate_failures()
        worker = next(a for a in self.fixers() if a['plan_path'] == OTHER)
        self.end_fixer(worker, verification=False)
        self.round()
        self.round()
        state = self.state(OTHER)
        self.assertTrue(state['fix']['diagnosis'])
        self.assertTrue(any(p['plan'] == REL for p in state['fix']['review_peers']))
        prompt = (self.env().run_dir(state['fix']['agent']) / 'prompt.md').read_text(encoding='utf-8')
        self.assertIn('shared_cause', prompt)
        self.assertIn('scope', prompt)
        self.assertIn('OTHER FAILED INTEGRATIONS', prompt)

    def test_crossed_diagnoses_cannot_create_cycle(self):
        from haivequeen import shared_diagnosis
        self.separate_failures()
        peers, proposal = self.proposal()
        reverse_peers = shared_diagnosis.candidates(self.conn, self.env(), REL)
        target = next(p for p in reverse_peers if p['plan'] == OTHER)
        reverse = {**proposal, 'plan': OTHER, 'scope': target['scope']}
        follower = self.state(OTHER)
        shared_diagnosis.attach(self.conn, self.env(), OTHER, follower,
            shared_diagnosis.validate(self.conn, self.env(), OTHER, follower, peers, proposal))
        orchestrator.save(self.conn, 'testproj', OTHER, follower)
        with self.assertRaisesRegex(ValueError, 'changed'):
            shared_diagnosis.validate(self.conn, self.env(), REL, self.state(), reverse_peers, reverse)
        self.assertEqual(self.state()['phase'], 'fix')

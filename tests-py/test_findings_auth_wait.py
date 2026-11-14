"""Permission failures wait for an answer instead of starting a code fixer."""
from unittest import mock
from bzztest import REL, RoundCase
from haivequeen import orchestrator, db

class AuthWaitTests(RoundCase):
    def test_auth_failure_waits_once_and_resumes_same_step(self):
        env = self.env()
        state = {'phase':'pipeline','step':'Push','steps':{'Merge':'done','Check':'done','Push':'running'}, 'ran':['Merge','Check'], 'placed':True}
        with mock.patch.object(orchestrator, '_start_log', return_value='log'), \
             mock.patch.object(orchestrator, '_finish_log'), \
             mock.patch.object(orchestrator.action_logs, 'append'), \
             mock.patch.object(orchestrator, '_log'), \
             mock.patch.object(orchestrator, '_blocker'), \
             mock.patch.object(orchestrator, '_set_status'), \
             mock.patch.object(orchestrator.hives, 'release_integration'), \
             mock.patch.object(orchestrator, 'save'):
            orchestrator._red(self.conn, env, REL, state, 'Push', "refusing to allow a Personal Access Token to create or update workflow without workflow scope")
            self.assertEqual(state['phase'], 'blocked')
            self.assertNotIn('fix', state)
            self.assertEqual(state['steps']['Check'], 'done')
            qid = state['blocked']['question']
            orchestrator._advance_blocked(self.conn, env, REL, state)
            self.assertEqual(state['phase'], 'blocked')
            db.answer_question(self.conn, qid, 'Access corrected; retry.', 'owner')
            orchestrator._advance_blocked(self.conn, env, REL, state)
            self.assertEqual(state['phase'], 'pipeline')
            self.assertEqual(state['step'], 'Push')
            self.assertEqual(state['ran'], ['Merge','Check'])

    def test_code_error_is_not_auth_failure(self):
        self.assertFalse(orchestrator.authentication_failure('compiler error: access to private member denied'))
        self.assertTrue(orchestrator.authentication_failure('fatal: Authentication failed for remote'))
        self.assertTrue(orchestrator.authentication_failure('remote: Permission to repository denied to user'))

    def test_rejected_push_does_not_fetch_again_or_restart_merge(self):
        env = self.env()
        state = {'phase':'pipeline', 'step':'Push', 'steps':{'Merge':'done','Check':'done','Push':'todo'}, 'ran':['Merge','Check']}
        calls = []
        def git(folder, *args, **kwargs):
            calls.append(args)
            return (1, 'remote: refusing update without workflow scope') if args[0] == 'push' else (0, '')
        with mock.patch.object(orchestrator, '_git', side_effect=git), mock.patch.object(orchestrator, '_start_log', return_value='log'), mock.patch.object(orchestrator, '_remote', return_value=('origin','main')), mock.patch.object(orchestrator.integration, 'head_commit', return_value='abc'), mock.patch.object(orchestrator, 'save'), mock.patch.object(orchestrator, '_log'), mock.patch.object(orchestrator, 'save_step_log'), mock.patch.object(orchestrator, '_red') as red, mock.patch.object(orchestrator, '_moved') as moved:
            orchestrator._push(self.conn, env, REL, state, str(self.repo))
        self.assertEqual([call[0] for call in calls], ['fetch','merge-base','push'])
        red.assert_called_once()
        moved.assert_not_called()
        self.assertEqual(state['ran'], ['Merge','Check'])

from unittest import TestCase, mock
from types import SimpleNamespace
from haivequeen import orchestrator, publish


class ReleaseGateTests(TestCase):
    def test_chain_respects_enabled_and_orders_release_after_deploy(self):
        env = SimpleNamespace(settings={'wayOfWorking': 'custom', 'releaseEnabled': True,
                                        'deployEnabled': True, 'deployMode': 'git'})
        self.assertLess(orchestrator.chain(env).index('Deploy'), orchestrator.chain(env).index('Release'))
        env.settings['releaseEnabled'] = False
        self.assertNotIn('Release', orchestrator.chain(env))

    def test_pending_and_failure_do_not_advance(self):
        env = SimpleNamespace(settings={'wayOfWorking': 'custom', 'releaseEnabled': True}, name='test')
        state = {'steps': {}, 'ran': [], 'step': 'Release'}
        with mock.patch.object(publish, 'pipeline_release', create=True, side_effect=[None, ValueError('failed')]), \
             mock.patch.object(orchestrator, '_provider_wait') as wait, \
             mock.patch.object(orchestrator, '_done') as done:
            orchestrator._release(None, env, 'plan', state, '.')
            orchestrator._release(None, env, 'plan', state, '.')
        self.assertEqual(wait.call_count, 2)
        done.assert_not_called()

    def test_disabled_release_does_not_publish_a_previously_pending_step(self):
        env = SimpleNamespace(settings={'wayOfWorking': 'custom', 'releaseEnabled': False}, name='test')
        with mock.patch.object(publish, 'pipeline_release') as release, \
             mock.patch.object(orchestrator, '_done') as done:
            orchestrator._release(None, env, 'plan', {'step': 'Release'}, '.')
        release.assert_not_called()
        done.assert_called_once()

    def test_new_pipeline_attempt_discards_release_selected_for_old_commit(self):
        env = SimpleNamespace(name='test')
        state = {'fix': {'agent': 'fixer'}, 'release': {'tag': 'v1.0.0-dev.1'}, 'merged': 'old'}
        with mock.patch('haivequeen.runtime.list_agents', return_value=[]), \
             mock.patch.object(orchestrator, '_log'), mock.patch.object(orchestrator, '_save_last'), \
             mock.patch.object(orchestrator, 'save'):
            orchestrator._advance_fix(None, env, 'plan', state, 4096, lambda _: False)
        self.assertNotIn('release', state)
        self.assertEqual(state['step'], 'Merge')

    def test_remote_move_discards_release_selected_before_replay(self):
        state = {'release': {'tag': 'v1.0.0-dev.1'}}
        with mock.patch.object(orchestrator, '_log'), mock.patch.object(orchestrator, '_replay_followers'), \
             mock.patch.object(orchestrator, 'save'):
            orchestrator._moved(None, SimpleNamespace(name='test'), 'plan', state, 'Push')
        self.assertNotIn('release', state)

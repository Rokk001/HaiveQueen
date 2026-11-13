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
        env = SimpleNamespace(settings={}, name='test')
        state = {'steps': {}, 'ran': [], 'step': 'Release'}
        with mock.patch.object(publish, 'pipeline_release', create=True, side_effect=[None, ValueError('failed')]), \
             mock.patch.object(orchestrator, '_provider_wait') as wait, \
             mock.patch.object(orchestrator, '_done') as done:
            orchestrator._release(None, env, 'plan', state, '.')
            orchestrator._release(None, env, 'plan', state, '.')
        self.assertEqual(wait.call_count, 2)
        done.assert_not_called()

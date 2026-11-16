"""Ready timeouts observe the same attempt instead of buying another check."""
from unittest import mock
from bzztest import RoundCase, REL, runtime
from haivequeen import readygate


class ReadyObservation(RoundCase):
    def setUp(self):
        super().setUp()
        self.plan('READY', [{'id': 'P1', 'title': 'First'}])
        self.round()
        self.queen = next(a for a in runtime.list_agents(self.conn, 'testproj', 'all') if a['phase'] == 'queen')
        self.e = self.env()
        self.e.settings['readyCheckTimeoutSec'] = 0
        self.request = {'plan': REL, 'cwd': str(self.wt), 'prompt': 'Review current requirements'}
        self.starts = []

    def start(self, ctx, action, forced_pick):
        name = 'check-%s' % len(self.starts)
        self.starts.append(name)
        ctx['state']['names'].append(name)
        runtime.upsert_agent(self.conn, 'testproj', name, {'phase': 'verify', 'package': 'READY-CHECK',
                             'plan_path': REL, 'status': 'running', 'cwd': str(self.wt)})
        return 1

    def call(self):
        return readygate.start_check_agent(self.e, self.queen, self.request, sleep=lambda _: None)

    def finish(self, text='READY-CHECK: PASSED'):
        name = self.starts[-1]
        runtime.upsert_agent(self.conn, 'testproj', name, {'status': 'done'})
        folder = self.e.run_dir(name)
        folder.mkdir(parents=True, exist_ok=True)
        (folder / 'final.txt').write_text(text, encoding='utf-8')
        self.e.settings['readyCheckTimeoutSec'] = .01

    def test_timeout_reuses_attempt_even_without_free_provider(self):
        with mock.patch('haivequeen.loop._do_start', side_effect=self.start):
            self.assertEqual(self.call()[0], 'unavailable')
            with mock.patch('haivequeen.scheduler.select_provider', return_value=None) as select:
                self.assertEqual(self.call()[0], 'unavailable')
                select.assert_not_called()
            self.finish()
            self.assertEqual(self.call(), ('passed', ''))
        self.assertEqual(len(self.starts), 1)

    def test_repeated_timeout_does_not_spawn_another_checker(self):
        with mock.patch('haivequeen.loop._do_start', side_effect=self.start):
            self.call()
            self.call()
        self.assertEqual(len(self.starts), 1)

    def test_finished_failure_is_delivered_before_retry(self):
        with mock.patch('haivequeen.loop._do_start', side_effect=self.start):
            self.call()
            self.finish('READY-CHECK: FAILED\nR-1 missing')
            self.assertEqual(self.call(), ('failed', 'R-1 missing'))
        self.assertEqual(len(self.starts), 1)

    def test_changed_request_waits_for_old_attempt_then_requires_fresh_check(self):
        with mock.patch('haivequeen.loop._do_start', side_effect=self.start):
            self.call()
            self.request['prompt'] = 'New decision changes the requirements'
            self.call()
            self.assertEqual(len(self.starts), 1)
            self.finish()
            self.assertEqual(self.call()[0], 'unavailable')
            self.e.settings['readyCheckTimeoutSec'] = 0
            self.call()
            self.assertEqual(len(self.starts), 2)

    def test_terminal_row_with_live_runner_is_still_observed(self):
        with mock.patch('haivequeen.loop._do_start', side_effect=self.start):
            self.call()
            self.finish()
            with mock.patch('haivequeen.agent_process.is_live', return_value=True):
                self.assertEqual(self.call()[0], 'unavailable')
            self.assertEqual(self.call(), ('passed', ''))
            self.assertEqual(len(self.starts), 1)

    def test_owner_stop_is_not_replaced_by_an_automatic_retry(self):
        with mock.patch('haivequeen.loop._do_start', side_effect=self.start):
            self.call()
            self.finish()
            runtime.upsert_agent(self.conn, 'testproj', self.starts[-1], {'last_line': runtime.STOPPED_LINE})
            self.assertEqual(self.call()[0], 'aborted')
            self.assertEqual(len(self.starts), 1)

    def test_legacy_live_checker_blocks_new_start_without_accepting_unknown_scope(self):
        runtime.upsert_agent(self.conn, 'testproj', 'legacy', {'phase': 'verify', 'package': 'READY-CHECK',
                             'plan_path': REL, 'status': 'running'})
        with mock.patch('haivequeen.loop._do_start', side_effect=self.start):
            self.call()
            self.assertEqual(self.starts, [])
            self.starts.append('legacy')
            self.finish()
            self.assertEqual(self.call()[0], 'unavailable')
            self.e.settings['readyCheckTimeoutSec'] = 0
            self.call()
            self.assertEqual(len(self.starts), 2)

    def test_new_environment_recovers_persisted_attempt(self):
        with mock.patch('haivequeen.loop._do_start', side_effect=self.start):
            self.call()
            self.e = self.env()
            self.e.settings['readyCheckTimeoutSec'] = 0
            self.call()
            self.finish()
            self.assertEqual(self.call(), ('passed', ''))
            self.assertEqual(len(self.starts), 1)

    def test_concurrent_callers_register_only_one_attempt(self):
        import copy
        import threading
        from haivequeen import config, db
        barrier = threading.Barrier(2)
        errors = []
        launches = []
        def scope(_):
            barrier.wait(timeout=10)
            return 'same-scope'
        def launch(env, queen, request):
            launches.append('check')
            runtime.upsert_agent(env.conn, env.name, 'concurrent-check', {'phase': 'verify',
                                 'package': 'READY-CHECK', 'plan_path': REL, 'status': 'starting'})
            return 'concurrent-check', ''
        def caller():
            conn = None
            try:
                conn = db.connect(config.db_path())
                env = copy.copy(self.e)
                env.conn = conn
                self.assertEqual(readygate.start_check_agent(env, self.queen, self.request)[0], 'unavailable')
            except BaseException as exc:
                errors.append(exc)
            finally:
                if conn is not None:
                    conn.close()
        with mock.patch.object(readygate, '_check_scope', side_effect=scope), mock.patch.object(
                readygate, '_launch_check_agent', side_effect=launch):
            threads = [threading.Thread(target=caller) for _ in range(2)]
            for thread in threads:
                thread.start()
            for thread in threads:
                thread.join(timeout=15)
            self.assertTrue(all(not thread.is_alive() for thread in threads))
        self.assertEqual(errors, [])
        self.assertEqual(launches, ['check'])

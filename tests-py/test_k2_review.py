"""Regression coverage for K2 resource accounting and supervisor recovery."""
import json
import os
import subprocess
from types import SimpleNamespace
from unittest import mock

import bzztest  # noqa: F401
from haivequeen import buildjobs, integration, runtime
from test_k2_detached_compile import DetachedCompileCase


class CoreAccountingTests(DetachedCompileCase):
    def test_multi_job_builds_and_parallel_targets_use_the_allocated_cores(self):
        runtime.upsert_agent(self.conn, 'project', 'builder', {
            'status': 'running', 'buildJobs': 2, 'buildParallel': 3})
        with mock.patch.dict(os.environ, self.env), \
             mock.patch.object(integration.osplat, 'free_ram_mb', return_value=64000), \
             mock.patch.object(integration.osplat, 'logical_cores', return_value=8):
            self.assertEqual(integration._running_builds(), 6)
            settings = {'buildJobs': {'cores': 6}}
            self.assertIsNotNone(integration._slot_wait(settings, [], integration._running_builds))
            settings['buildJobs']['cores'] = 8
            self.assertIsNone(integration._slot_wait(settings, [{}], integration._running_builds))
            self.assertIsNotNone(integration._slot_wait(settings, [{}, {}], integration._running_builds))

    def test_next_build_respects_jobs_already_allocated(self):
        runtime.upsert_agent(self.conn, 'project', 'builder', {
            'status': 'running', 'buildJobs': 2, 'buildParallel': 3})
        env = SimpleNamespace(name='project', conn=self.conn,
                              settings={'buildJobs': {'cores': 8, 'firstJobs': 8}})
        with mock.patch.object(buildjobs, '_running_checks', return_value=1):
            self.assertEqual(buildjobs.plan_build(env, free_mb=64000, cores=8)['Jobs'], 1)
            self.assertEqual(buildjobs.plan_build(env, free_mb=64000, cores=8, exclude='builder')['Jobs'], 4)


class SupervisorRecoveryTests(DetachedCompileCase):
    def test_waiter_reaps_its_supervisor_before_returning_the_published_result(self):
        job = {'token': 'reap', 'pid': 101, 'database': str(self.data / 'haivequeen.db')}
        proc = mock.Mock()
        integration.SUPERVISORS[job['token']] = proc
        self.addCleanup(integration.SUPERVISORS.pop, job['token'], None)
        env = SimpleNamespace(conn=self.conn, name='project')
        with mock.patch.object(integration, 'start_compile_check', return_value=job), \
             mock.patch.object(integration, 'poll_compile_check', return_value={'ok': True}):
            self.assertEqual(integration.run_compile_check(env, {'name': 'agent'}), {'ok': True})
        proc.wait.assert_called_once_with()
        self.assertNotIn(job['token'], integration.SUPERVISORS)

    def test_reused_command_pid_is_never_terminated(self):
        request = {'check_pid': 102, 'check_identity': 'original'}
        with mock.patch.object(integration.osplat, 'process_alive', return_value=True), \
             mock.patch.object(integration.osplat, 'process_identity', return_value='replacement'), \
             mock.patch.object(integration.osplat, 'kill_process_tree') as kill, \
             mock.patch.object(integration.proctree, 'exempt_clear'):
            self.assertTrue(integration._end_orphan(request))
            kill.assert_not_called()

    def test_failed_orphan_termination_keeps_slot_until_the_command_is_gone(self):
        request = {'settings': {'compileCheck': 'check'}, 'commit': '',
                   'key': integration.COMPILE_KEY + 'agent', 'poll': 0.01,
                   'check_pid': 102, 'check_identity': 'original'}
        runtime.kv_set(self.conn, 'project', integration.COMPILE_REQUEST_KEY + 'retry', json.dumps(request))
        holder = {'token': 'slot', 'pid': 101, 'run_token': 'retry',
                  'project': 'project', 'database': str(self.data / 'haivequeen.db')}
        path = self.data / 'compile-check.lock'
        path.write_text(json.dumps({'holders': [holder]}), encoding='utf-8')
        with mock.patch.dict(os.environ, self.env), \
             mock.patch.object(integration.osplat, 'process_alive', side_effect=lambda pid: pid == 102), \
             mock.patch.object(integration.osplat, 'process_identity', return_value='original'), \
             mock.patch.object(integration.osplat, 'kill_process_tree', return_value=False) as kill:
            self.assertEqual(integration._live_holders(path), [holder])
            kill.assert_called_once_with(102)
            self.assertIsNone(self.result())
        with mock.patch.dict(os.environ, self.env), \
             mock.patch.object(integration.osplat, 'process_alive', return_value=False):
            self.assertEqual(integration._live_holders(path), [])
            self.assertTrue(self.result()['aborted'])

    def test_dead_supervisor_with_dead_waiter_ends_orphan_before_reclaim(self):
        caller = self.start("import os,pathlib,time\npathlib.Path('started').write_text(str(os.getpid()))\ntime.sleep(60)\n")
        child = int((self.base / 'started').read_text())
        self.addCleanup(integration.osplat.kill_process_tree, child)
        holder = self.wait_for(lambda: integration._read_holders(self.data / 'compile-check.lock'))[0]
        caller.kill()
        caller.wait(timeout=5)
        if os.name == 'nt':
            subprocess.run(['taskkill', '/PID', str(holder['pid']), '/F'], capture_output=True, check=True)
        else:
            os.kill(holder['pid'], 9)
        self.wait_for(lambda: not integration.osplat.process_alive(holder['pid']))
        self.assertTrue(integration.osplat.process_alive(child))
        with mock.patch.dict(os.environ, self.env):
            self.assertFalse(integration._live_holders(self.data / 'compile-check.lock'))
        self.assertFalse(integration.osplat.process_alive(child))
        self.assertTrue(self.result()['aborted'])

    def test_results_survive_loss_of_supervisor_at_slot_release(self):
        token = 'publication-gap'
        request = {'settings': {'compileCheck': 'check'}, 'cwd': str(self.base),
                   'name': 'agent', 'key': integration.COMPILE_KEY + 'agent',
                   'poll': 0.01, 'milestones': '', 'commit': ''}
        runtime.kv_set(self.conn, 'project', integration.COMPILE_REQUEST_KEY + token, json.dumps(request))

        class SupervisorGone(BaseException):
            pass

        def release(*args):
            raise SupervisorGone()

        with mock.patch.object(integration, '_acquire_compile_slot', return_value='slot'), \
             mock.patch.object(integration, '_watch_compile', return_value=(0, 'finished')), \
             mock.patch.object(integration, '_release_compile_slot', side_effect=release):
            with self.assertRaises(SupervisorGone):
                integration.compile_supervisor(str(self.data / 'haivequeen.db'), 'project', token)
        self.assertTrue(self.result()['ok'], 'caller result must exist before slot release')
        stored = runtime.kv_get(self.conn, 'project', integration.COMPILE_RUN_KEY + token)
        self.assertTrue(json.loads(stored)['ok'])

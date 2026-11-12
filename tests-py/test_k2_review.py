"""Regression coverage for K2 resource accounting and supervisor recovery."""
import contextlib
import io
import json
import os
import subprocess
import threading
from types import SimpleNamespace
from unittest import mock

import bzztest  # noqa: F401
from haivequeen import buildjobs, cli, config, hives, integration, orchestrator, projsettings, runtime, scheduler
from test_k2_detached_compile import DetachedCompileCase


real_thread_start = threading.Thread.start


class CoreAccountingTests(DetachedCompileCase):
    def test_build_and_compile_acquisitions_share_one_atomic_reservation(self):
        for kind in ('agent', 'integration', 'orchestrator'):
            with self.subTest(kind=kind):
                settings = {'buildCommand': 'build', 'buildJobs': {'cores': 1}}
                env = SimpleNamespace(name='project', conn=self.conn, settings=settings)
                calculated, attempting, granted = (threading.Event() for _ in range(3))
                slots, errors = [], []
                original = buildjobs.plan_build

                def pause_before_publication(*args, **kwargs):
                    planned = original(*args, **kwargs)
                    calculated.set()
                    self.assertTrue(attempting.wait(5))
                    granted.wait(0.3)
                    return planned

                def acquire_check():
                    try:
                        if not calculated.wait(5):
                            raise AssertionError('build did not calculate its allocation')
                        attempting.set()
                        slots.append(integration._acquire_compile_slot(settings, 0.01))
                        granted.set()
                    except Exception as exc:
                        errors.append(exc)

                state = {'steps': {}}
                with mock.patch.dict(os.environ, {**self.env, 'BZZ_AGENT': ''}), \
                     mock.patch.object(config, 'load_config', return_value={'buildJobs': settings['buildJobs']}), \
                     mock.patch.object(integration.osplat, 'free_ram_mb', return_value=64000), \
                     mock.patch.object(integration.osplat, 'logical_cores', return_value=1), \
                     mock.patch.object(buildjobs, 'plan_build', side_effect=pause_before_publication), \
                     mock.patch.object(scheduler, 'load_projects', return_value=[env]), \
                     mock.patch.object(projsettings, 'remote_wait_reason', return_value=''), \
                     mock.patch.object(integration, 'head_commit', return_value=''), \
                     mock.patch.object(orchestrator, '_log'), \
                     mock.patch.object(orchestrator, '_counter', return_value=''), \
                     mock.patch.object(orchestrator, '_harvest', return_value=None), \
                     mock.patch.object(orchestrator, '_async', return_value=True), \
                     mock.patch.object(orchestrator, '_result_file', return_value=self.base / 'result'), \
                     mock.patch.object(orchestrator.threading.Thread, 'start', autospec=True) as start:
                    # Keep the real Thread.start for the competing acquisition.
                    thread = threading.Thread(target=acquire_check)
                    real_thread_start(thread)
                    try:
                        if kind == 'agent':
                            runtime.upsert_agent(self.conn, 'project', 'builder', {'status': 'running'})
                            self.assertEqual(buildjobs.reserve_agent_build(env, 'builder')['Jobs'], 1)
                        elif kind == 'integration':
                            args = SimpleNamespace(action='acquire', project='project', plan='plan', owner_pid=os.getpid())
                            with contextlib.redirect_stdout(io.StringIO()):
                                self.assertEqual(cli.cmd_integration(args, self.conn), 0)
                        else:
                            orchestrator._command(self.conn, env, 'plan', state, self.base, 'Build', 64000)
                            start.assert_called_once()
                        self.assertFalse(granted.is_set(), 'build and compile check both reserved the last core')
                        self.assertEqual(buildjobs.allocated_cores(buildjobs.running_builds(self.conn)), 1)
                    finally:
                        runtime.upsert_agent(self.conn, 'project', 'builder', {'buildJobs': None})
                        hives.release_integration(self.conn, 'project', 'plan')
                        orchestrator.save(self.conn, 'project', 'plan', {'steps': {}})
                        orchestrator.JOBS.pop(('project', 'plan'), None)
                        thread.join(5)
                        for slot in slots:
                            integration._release_compile_slot(slot, 0.01)
                    self.assertFalse(thread.is_alive())
                    self.assertEqual(errors, [])
                    self.assertTrue(granted.is_set(), 'compile check did not start after build released its core')

    def test_orchestrator_waits_without_starting_a_zero_job_build(self):
        env = SimpleNamespace(name='project', conn=self.conn, settings={'buildCommand': 'build', 'buildJobs': 8})
        state = {'steps': {}}
        with mock.patch.object(buildjobs, 'plan_build', return_value={'Jobs': 0, 'Parallel': 1, 'Environment': {}}), \
             mock.patch.object(integration, 'head_commit', return_value=''), \
             mock.patch.object(orchestrator, '_log'), \
             mock.patch.object(orchestrator, '_counter', return_value=''), \
             mock.patch.object(orchestrator, '_harvest', return_value=None), \
             mock.patch.object(orchestrator, '_async', return_value=True), \
             mock.patch.object(orchestrator, '_result_file', return_value=self.base / 'result'), \
             mock.patch.object(orchestrator, '_blocker') as blocker, \
             mock.patch.object(orchestrator, '_set_status') as status, \
             mock.patch.object(orchestrator.threading.Thread, 'start') as start:
            self.assertIsNone(orchestrator._command(self.conn, env, 'plan', state, self.base, 'Build', 64000))
        blocker.assert_called_once()
        status.assert_called_once_with(self.conn, env, 'plan', 'WAITING')
        start.assert_not_called()
        self.assertNotIn('job', state)

    def test_integration_waits_without_acquiring_for_a_zero_job_build(self):
        env = SimpleNamespace(settings={'buildCommand': 'build', 'buildJobs': 8}, name='project', conn=self.conn)
        args = SimpleNamespace(action='acquire', project='project', plan='plan', owner_pid=os.getpid())
        with mock.patch.dict(os.environ, {'BZZ_AGENT': ''}), \
             mock.patch.object(scheduler, 'load_projects', return_value=[env]), \
             mock.patch.object(projsettings, 'remote_wait_reason', return_value=''), \
             mock.patch.object(buildjobs.osplat, 'free_ram_mb', return_value=64000), \
             mock.patch.object(buildjobs, 'plan_build', return_value={'Jobs': 0}), \
             mock.patch.object(hives, 'take_integration') as take, \
             contextlib.redirect_stdout(io.StringIO()) as output:
            self.assertEqual(cli.cmd_integration(args, self.conn), 1)
        self.assertIn('waiting for integration', output.getvalue())
        take.assert_not_called()

    def test_acquired_integration_reserves_cores_without_an_agent_build(self):
        hives.take_integration(self.conn, 'project', 'plan', owner_pid=os.getpid(), build_jobs=3)
        hives.take_integration(self.conn, 'project', 'waiting', owner_pid=os.getpid(), build_jobs=8)
        with mock.patch.dict(os.environ, self.env), \
             mock.patch.object(config, 'load_config', return_value={'buildJobs': {'parallel': 2}}), \
             mock.patch.object(integration.osplat, 'free_ram_mb', return_value=64000), \
             mock.patch.object(integration.osplat, 'logical_cores', return_value=8):
            self.assertEqual(integration._running_builds(), 6)
            self.assertIsNotNone(integration._slot_wait({'buildJobs': {'cores': 6}}, [], integration._running_builds))
            hives.release_integration(self.conn, 'project', 'plan')
            self.assertEqual(integration._running_builds(), 0)

    def test_integration_and_queen_measurement_are_counted_once(self):
        runtime.upsert_agent(self.conn, 'project', 'queen', {
            'status': 'running', 'pid': os.getpid(), 'plan': 'plan',
            'buildJobs': 3, 'buildParallel': 2})
        hives.take_integration(self.conn, 'project', 'plan', owner_pid=os.getpid(), build_jobs=3)
        with mock.patch.object(config, 'load_config', return_value={'buildJobs': {'parallel': 2}}):
            self.assertEqual(buildjobs.allocated_cores(buildjobs.running_builds(self.conn)), 6)
            self.assertEqual(buildjobs.running_builds(self.conn, exclude='queen'), [])

    def test_external_refresh_excludes_only_its_project_and_plan(self):
        for project in ('project', 'other'):
            hives.take_integration(self.conn, project, 'plan', owner_pid=os.getpid(), build_jobs=2)
        with mock.patch.object(config, 'load_config', return_value={'buildJobs': {'parallel': 2}}):
            self.assertEqual(buildjobs.allocated_cores(
                buildjobs.running_builds(self.conn, exclude=('project', 'plan'))), 4)

    def test_orchestrator_build_allocation_counts_until_the_result_exists(self):
        result_file = self.base / 'build-result.json'
        orchestrator.save(self.conn, 'project', 'plan', {'job': {
            'step': 'Build', 'owner_pid': os.getpid(), 'pid': os.getpid(),
            'buildJobs': 2, 'buildParallel': 3, 'file': str(result_file)}})
        self.assertEqual(buildjobs.allocated_cores(buildjobs.running_builds(self.conn)), 6)
        result_file.write_text('{}', encoding='utf-8')
        self.assertEqual(buildjobs.running_builds(self.conn), [])

    def test_orchestrator_publishes_allocation_before_starting_worker(self):
        env = SimpleNamespace(name='project', conn=self.conn, settings={
            'buildCommand': 'build', 'buildJobs': {'cores': 8, 'parallel': 3}})
        state = {'steps': {}}
        planned = {'Jobs': 2, 'Parallel': 3, 'Environment': {}}
        with mock.patch.object(buildjobs, 'plan_build', return_value=planned), \
             mock.patch.object(integration, 'head_commit', return_value=''), \
             mock.patch.object(orchestrator, '_log'), \
             mock.patch.object(orchestrator, '_counter', return_value=''), \
             mock.patch.object(orchestrator, '_harvest', return_value=None), \
             mock.patch.object(orchestrator, '_async', return_value=True), \
             mock.patch.object(orchestrator, '_result_file', return_value=self.base / 'result'), \
             mock.patch.object(orchestrator.threading.Thread, 'start', side_effect=lambda: self.assertEqual(
                 buildjobs.allocated_cores(buildjobs.running_builds(self.conn)), 6)):
            self.addCleanup(orchestrator.JOBS.pop, ('project', 'plan'), None)
            orchestrator._command(self.conn, env, 'plan', state, self.base, 'Build', 64000)

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

    def test_next_build_waits_when_builds_and_checks_use_the_budget(self):
        env = SimpleNamespace(name='project', conn=self.conn,
                              settings={'buildJobs': {'cores': 8, 'parallel': 2}})
        for jobs, parallel, checks in ((2, 3, 2), (4, 2, 0), (0, 1, 8), (3, 3, 0), (2, 3, 1)):
            with self.subTest(jobs=jobs, parallel=parallel, checks=checks), \
                 mock.patch.object(buildjobs, 'running_builds', return_value=(
                     [{'jobs': jobs, 'parallel': parallel}] if jobs else [])), \
                 mock.patch.object(buildjobs, '_running_checks', return_value=checks):
                planned = buildjobs.plan_build(env, free_mb=64000, cores=8)
                self.assertEqual(planned['Jobs'], 0)
                self.assertEqual(planned['Why'], 'cores')
                self.assertEqual(planned['Environment']['BUILD_JOBS'], '0')


class SupervisorRecoveryTests(DetachedCompileCase):
    def test_normal_exit_one_without_output_remains_a_failure(self):
        proc = self.start("import pathlib,sys\npathlib.Path('started').touch()\nsys.exit(1)\n")
        stdout, _ = proc.communicate(timeout=15)
        result = json.loads(stdout.strip().splitlines()[-1])
        self.assertEqual(result['exit'], 1)
        self.assertFalse(result.get('aborted'), result)

    def test_normal_exit_one_with_output_remains_a_failure(self):
        proc = self.start("import os,pathlib,sys\nprint('progress before termination', flush=True)\npathlib.Path('started').write_text(str(os.getpid()))\nsys.exit(1)\n")
        stdout, _ = proc.communicate(timeout=15)
        result = json.loads(stdout.strip().splitlines()[-1])
        self.assertEqual(result['exit'], 1)
        self.assertFalse(result.get('aborted'), result)

    def test_killed_command_with_output_is_stored_as_aborted(self):
        proc = self.start("import os,pathlib,time\nprint('progress before termination', flush=True)\npathlib.Path('started').write_text(str(os.getpid()))\ntime.sleep(60)\n")
        holder = self.wait_for(lambda: integration._read_holders(self.data / 'compile-check.lock'))[0]
        request = json.loads(runtime.kv_get(self.conn, 'project',
                                          integration.COMPILE_REQUEST_KEY + holder['run_token']))
        self.assertTrue(integration.osplat.kill_process_tree(request['check_pid']))
        stdout, _ = proc.communicate(timeout=15)
        result = json.loads(stdout.strip().splitlines()[-1])
        self.assertIn('progress before termination', result['output'])
        self.assertTrue(result.get('aborted'), result)
        self.assertTrue(self.result().get('aborted'))

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

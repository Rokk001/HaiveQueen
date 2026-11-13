import json
import os
import subprocess
import sys
import tempfile
import time
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest import mock

import bzztest  # noqa: F401
from haivequeen import config, db, integration, orchestrator, runtime


class DetachedCompileCase(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory(ignore_cleanup_errors=True)
        self.addCleanup(self.tmp.cleanup)
        self.base = Path(self.tmp.name)
        self.data = self.base / 'data'
        self.env = {**os.environ, 'BZZ_DATA_DIR': str(self.data)}
        self.conn = db.connect(self.data / 'haivequeen.db')
        self.addCleanup(self.conn.close)
        self.harness = self.base / 'caller.py'
        self.harness.write_text('''import json, sys\nfrom types import SimpleNamespace\nfrom haivequeen import config, db, integration\nc = db.connect(config.db_path())\ne = SimpleNamespace(name="project", conn=c, settings={"compileCheck": sys.argv[1], "compileStallMinutes": 5, "compileMaxMinutes": 180})\nr = integration.run_compile_check(e, {"name": "agent"}, cwd=sys.argv[2], poll=0.05, milestones=False)\nprint(json.dumps(r), flush=True)\n''', encoding='utf-8')
        self.env['PYTHONPATH'] = str(Path(integration.__file__).resolve().parent.parent)

    def wait_for(self, predicate, seconds=20):
        end = time.monotonic() + seconds
        while time.monotonic() < end:
            value = predicate()
            if value:
                return value
            time.sleep(0.05)
        self.fail('timed out waiting for compile check')

    def start(self, source):
        script = self.base / 'check.py'
        script.write_text(source, encoding='utf-8')
        command = '"%s" "%s"' % (sys.executable, script)
        proc = subprocess.Popen([sys.executable, str(self.harness), command, str(self.base)], env=self.env, stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True)
        self.addCleanup(self.stop, proc)
        self.wait_for(lambda: (self.base / 'started').exists())
        return proc

    @staticmethod
    def stop(proc):
        if proc.poll() is None:
            if os.name == 'nt':
                subprocess.run(['taskkill', '/PID', str(proc.pid), '/T', '/F'], capture_output=True)
            else:
                proc.kill()
        proc.communicate(timeout=5)

    def result(self):
        return integration.compile_result(self.conn, 'project', 'agent')


class DetachedCompileTests(DetachedCompileCase):
    def test_signal_exit_is_stored_as_aborted(self):
        env = SimpleNamespace(name='project', conn=self.conn, settings={'compileCheck': 'check'})
        with mock.patch.object(integration, '_acquire_compile_slot', return_value='slot'), \
             mock.patch.object(integration, '_release_compile_slot'), \
             mock.patch.object(integration, '_watch_compile', return_value=(-9, '')):
            result = integration._execute_compile_check(env, {'name': 'agent'}, cwd=str(self.base), milestones=False)
        self.assertTrue(result['aborted'])
        self.assertTrue(self.result()['aborted'])

    def test_caller_ends_while_check_finishes_and_stores_result(self):
        proc = self.start("import pathlib, time\np=pathlib.Path('started'); p.touch()\ntime.sleep(2)\n")
        proc.kill()
        proc.wait(timeout=5)
        result = self.wait_for(self.result)
        self.assertTrue(result['ok'], result)

    def test_killed_launcher_is_reported_as_aborted(self):
        proc = self.start("import os, pathlib, time\npathlib.Path('started').write_text(str(os.getpid()))\ntime.sleep(60)\n")
        holder = self.wait_for(lambda: integration._read_holders(self.data / 'compile-check.lock'))[0]
        request = json.loads(runtime.kv_get(self.conn, 'project',
                                          integration.COMPILE_REQUEST_KEY + holder['run_token']))
        self.assertTrue(integration.osplat.kill_process_tree(request['check_pid']))
        stdout, _ = proc.communicate(timeout=15)
        result = json.loads(stdout.strip().splitlines()[-1])
        self.assertTrue(result.get('aborted'), result)
        self.assertTrue(self.result().get('aborted'))

    def test_killed_check_returns_and_stores_aborted(self):
        proc = self.start("import pathlib, time\npathlib.Path('started').touch()\ntime.sleep(60)\n")
        holders = self.wait_for(lambda: integration._read_holders(self.data / 'compile-check.lock'))
        pid = holders[0]['pid']
        self.assertNotEqual(pid, proc.pid)
        if os.name == 'nt':
            subprocess.run(['taskkill', '/PID', str(pid), '/T', '/F'], capture_output=True, check=True)
        else:
            os.kill(pid, 9)
        stdout, _ = proc.communicate(timeout=15)
        result = json.loads(stdout.strip().splitlines()[-1])
        self.assertTrue(result['aborted'], result)
        self.assertTrue(self.result()['aborted'])
        self.assertFalse(integration._live_holders(self.data / 'compile-check.lock'))
        (self.base / 'started').unlink()
        next_proc = self.start("import pathlib\npathlib.Path('started').touch()\n")
        out, _ = next_proc.communicate(timeout=15)
        self.assertTrue(json.loads(out.strip().splitlines()[-1])['ok'])

    def test_dead_slot_recovers_result_after_the_waiter_also_dies(self):
        proc = self.start("import pathlib,time\npathlib.Path('started').touch()\ntime.sleep(60)\n")
        holder = self.wait_for(lambda: integration._read_holders(self.data / 'compile-check.lock'))[0]
        proc.kill()
        proc.wait(timeout=5)
        if os.name == 'nt':
            subprocess.run(['taskkill', '/PID', str(holder['pid']), '/T', '/F'], capture_output=True, check=True)
        else:
            os.kill(holder['pid'], 9)
        self.wait_for(lambda: not integration.osplat.process_alive(holder['pid']))
        self.assertFalse(integration._live_holders(self.data / 'compile-check.lock'))
        self.assertTrue(self.result()['aborted'])

    def test_supervisor_start_failure_is_stored_and_returned(self):
        env = SimpleNamespace(name='project', conn=self.conn, settings={'compileCheck': 'check'})
        with mock.patch.object(integration.subprocess, 'Popen', side_effect=OSError('unavailable')):
            result = integration.run_compile_check(env, {'name': 'agent'}, cwd=str(self.base), milestones=False)
        self.assertTrue(result['aborted'])
        self.assertEqual(self.result(), result)



class CheckRecoveryTests(DetachedCompileCase):
    # Exercise handover after all in-memory orchestrator jobs have been lost.
    def check_job(self, source):
        script = self.base / 'check.py'
        script.write_text(source, encoding='utf-8')
        env = SimpleNamespace(name='project', conn=self.conn, settings={
            'compileCheck': '"%s" "%s"' % (sys.executable, script),
            'compileMaxMinutes': 180})
        with mock.patch.dict(os.environ, self.env):
            state = {'steps': {}, 'ran': []}
            with mock.patch.object(orchestrator, 'save'), mock.patch.object(orchestrator, '_log'), \
                 mock.patch.object(orchestrator, '_async', return_value=True):
                orchestrator._command(self.conn, env, 'plan', state, self.base, 'Check', 16000)
        self.addCleanup(orchestrator.JOBS.pop, ('project', 'plan'), None)
        self.wait_for(lambda: (self.base / 'started').exists())
        orchestrator.JOBS.pop(('project', 'plan'), None)
        return env, state

    def test_restart_harvests_the_original_stored_check_result(self):
        env, state = self.check_job("import pathlib,time\npathlib.Path('started').touch()\ntime.sleep(0.5)\nprint('finished')\n")
        result = self.wait_for(lambda: orchestrator._harvest(self.conn, env, 'plan', state))
        self.assertEqual(result, (0, 'finished\n'))  # the action output is now preserved without stripping
        stored = json.loads(runtime.kv_get(self.conn, 'project', 'orch-check:plan'))
        self.assertTrue(stored['ok'])

    def test_restart_marks_a_disappeared_supervisor_aborted(self):
        env, state = self.check_job("import pathlib,time\npathlib.Path('started').touch()\ntime.sleep(60)\n")
        pid = state['job']['pid']
        if os.name == 'nt':
            subprocess.run(['taskkill', '/PID', str(pid), '/T', '/F'], capture_output=True, check=True)
        else:
            os.kill(pid, 9)
        with mock.patch.dict(os.environ, self.env):
            result = self.wait_for(lambda: orchestrator._harvest(self.conn, env, 'plan', state))
            self.assertFalse(integration._live_holders(self.data / 'compile-check.lock'))
        self.assertNotEqual(result[0], 0)
        stored = json.loads(runtime.kv_get(self.conn, 'project', 'orch-check:plan'))
        self.assertTrue(stored['aborted'])
        self.assertEqual(stored['commit'], integration.head_commit(str(self.base)))


if __name__ == '__main__':
    unittest.main()

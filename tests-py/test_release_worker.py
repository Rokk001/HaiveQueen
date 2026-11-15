import argparse
import contextlib
import io
import json
import os
import subprocess
import sys
import time
import tempfile
import unittest
from pathlib import Path
from unittest import mock

import isolation  # noqa: F401
from haivequeen import db, loop, release_compat, release_staging, selfupdate, updates, version_layout
import test_release_provenance


class ReleaseWorkerTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.env = mock.patch.dict(os.environ, {'BZZ_DATA_DIR': self.temp.name})
        self.env.start()
        self.addCleanup(self.env.stop)
        self.root = Path(self.temp.name) / 'install'
        for version in ('1.0.0', '2.0.0'):
            folder = self.root / 'versions' / version / 'haivequeen'
            folder.mkdir(parents=True)
            (folder / 'cli.py').write_text('pass', encoding='utf-8')
        version_layout.set_active(self.root, '1.0.0')
        self.conn = db.connect(Path(self.temp.name) / 'test.db')
        self.addCleanup(self.conn.close)
        self.target = self.root / 'versions' / '2.0.0'

    def metadata(self, compatible, reason=''):
        (self.target / 'haivequeen-update.json').write_text(json.dumps({'compatible_with_older': compatible, 'reason': reason}), encoding='utf-8')

    def active(self):
        return (self.root / 'active-version').read_text(encoding='utf-8').strip()

    def test_unproven_positive_release_waits_for_live_job(self):
        self.metadata(True)
        live = [True]
        def jobs(conn):
            return [{'kind': 'Agent', 'name': 'child'}] if live[0] else []
        def sleep(_):
            self.assertEqual(self.active(), '1.0.0')
            self.assertTrue(loop.stop_flag().exists())
            status = json.loads(selfupdate.release_status_path().read_text(encoding='utf-8'))
            self.assertIn('unknown', status['reason'])
            live[0] = False
        with mock.patch('haivequeen.release_staging.verify_installed', return_value=True):
            result = selfupdate.run_release_update(self.conn, self.root, '2.0.0', jobs=jobs, sleep=sleep,
                loop_pid=lambda: 42 if live[0] else 0, start=lambda: True, receipt=lambda *a, **k: True)
        self.assertEqual(result['result'], 'ok')

    def test_unverified_or_changed_release_never_activates(self):
        self.metadata(True)
        for checks in ([False], [True, False]):
            with self.subTest(checks=checks), mock.patch('haivequeen.release_staging.verify_installed', side_effect=checks):
                activate = mock.Mock()
                result = selfupdate.run_release_update(self.conn, self.root, '2.0.0', jobs=lambda c: [],
                    loop_pid=lambda: 0, start=lambda: True, activate=activate)
                self.assertEqual(result['result'], 'failed')
                self.assertEqual(self.active(), '1.0.0')
                activate.assert_not_called()

    def test_versioned_entry_point_selects_release_worker(self):
        with mock.patch.object(selfupdate, 'run_release_update', return_value={'result': 'ok'}) as run:
            result = selfupdate.run_update(self.conn, live=self.root / 'versions' / '1.0.0', version='2.0.0')
        self.assertEqual(result['result'], 'ok')
        self.assertEqual(run.call_args.args[:3], (self.conn, self.root, '2.0.0'))

    def test_command_reports_wait_and_force_requires_explicit_parameter(self):
        status = {'waiting': True, 'reason': 'database change', 'jobs': [{'kind': 'Queen', 'name': 'Q'}]}
        selfupdate.write_release_status(status)
        output = io.StringIO()
        with contextlib.redirect_stdout(output):
            self.assertEqual(updates.cmd_update(argparse.Namespace(install=None, force=False), self.conn), 0)
        self.assertEqual(json.loads(output.getvalue())['jobs'][0]['name'], 'Q')
        self.assertFalse(selfupdate.release_force_path().exists())
        output = io.StringIO()
        with contextlib.redirect_stdout(output):
            self.assertEqual(updates.cmd_update(argparse.Namespace(install=None, force=True), self.conn), 0)
        self.assertIn('Unfinished work', output.getvalue())
        self.assertTrue(selfupdate.release_force_path().exists())

    @unittest.expectedFailure  # No production proof provider or admission exclusion yet.
    def test_compatible_switch_does_not_drain_or_wait_for_jobs(self):
        # This controlled provider stands for all required evidence, including
        # process/environment provenance, shared state, and launch exclusion.
        # Production has no provider capable of establishing these facts yet.
        def proven_compatible(_target):
            return {'compatible': True, 'reason': ''}
        def receipt(conn, target, began, sleep, poll, timeout, loop_pid):
            self.assertEqual(self.active(), '2.0.0')
            self.assertFalse(loop.stop_flag().exists())
            self.assertFalse(selfupdate.lock_path().exists())
            return True
        with self.verified_release(compatible=True) as stage, mock.patch.object(release_compat, 'compatibility', side_effect=proven_compatible):
            result = selfupdate.run_release_update(self.conn, self.root, '2.0.0', stage=stage,
                jobs=lambda c: self.fail('must not wait'), loop_pid=lambda: 42,
                receipt=receipt, start=lambda: self.fail('old core hands over'))
        self.assertEqual(result['result'], 'ok')
        self.assertEqual(self.active(), '2.0.0')

    def test_verified_staged_release_drains_with_unknown_reason(self):
        # A release body claiming a database change is not evidence of it.
        fixture = test_release_provenance.ProvenanceTests()
        fixture.setUp()
        original_fetch = fixture.fetch
        def fetch(url):
            if '/releases/' in url:
                return json.dumps({'tag_name': 'v2.0.0', 'body': json.dumps({
                    'repository': 'example/project', 'version': '2.0.0',
                    'commit': fixture.commit, 'compatible_with_older': False,
                    'reason': 'database change', 'evidence': 'assertion'})}).encode()
            return original_fetch(url)
        import shutil
        shutil.rmtree(self.target)
        live = [True]
        observed = []
        def sleep(_):
            observed.append(json.loads(selfupdate.release_status_path().read_text(encoding='utf-8')))
            self.assertEqual(self.active(), '1.0.0')
            self.assertTrue(loop.stop_flag().exists())
            live[0] = False
        def stage(root, repository, version):
            return release_staging.stage_release(root, repository, version, fetch)
        with mock.patch.object(updates, 'repository', return_value='example/project'), \
             mock.patch.object(release_staging, '_fetch', side_effect=fetch):
            result = selfupdate.run_release_update(self.conn, self.root, '2.0.0', stage=stage,
                jobs=lambda conn: [{'kind': 'Queen', 'name': 'Q'}] if live[0] else [],
                sleep=sleep, loop_pid=lambda: 42 if live[0] else 0,
                start=lambda: True, receipt=lambda *a, **k: True)
        self.assertEqual(result['result'], 'ok')
        self.assertEqual(observed[0]['reason'], release_compat.UNKNOWN_REASON)
        self.assertEqual(observed[0]['jobs'][0]['name'], 'Q')
        self.assertEqual(self.active(), '2.0.0')

    @contextlib.contextmanager
    def verified_release(self, compatible=False):
        fixture = test_release_provenance.ProvenanceTests()
        fixture.setUp()
        original_fetch = fixture.fetch
        def fetch(url):
            if '/releases/' in url:
                return json.dumps({'tag_name': 'v2.0.0', 'body': json.dumps({
                    'repository': 'example/project', 'version': '2.0.0',
                    'commit': fixture.commit, 'compatible_with_older': compatible,
                    'reason': '' if compatible else 'database change', 'evidence': 'assertion'})}).encode()
            return original_fetch(url)
        import shutil
        shutil.rmtree(self.target)
        def stage(root, repository, version):
            return release_staging.stage_release(root, repository, version, fetch)
        with mock.patch.object(updates, 'repository', return_value='example/project'), \
             mock.patch.object(release_staging, '_fetch', side_effect=fetch):
            yield stage

    def test_incompatible_waits_for_jobs_and_old_core(self):
        live = [True]
        observed = []
        def jobs(conn):
            return [{'kind': 'Queen', 'name': 'Q'}] if live[0] else []
        def sleep(_):
            status = json.loads(selfupdate.release_status_path().read_text(encoding='utf-8'))
            observed.append(status)
            self.assertEqual(self.active(), '1.0.0')
            self.assertTrue(loop.stop_flag().exists())
            live[0] = False
        with self.verified_release() as stage:
            result = selfupdate.run_release_update(self.conn, self.root, '2.0.0', stage=stage, jobs=jobs, sleep=sleep,
                        loop_pid=lambda: 42 if live[0] else 0, start=lambda: True, receipt=lambda *a, **k: True)
        self.assertEqual(result['result'], 'ok')
        self.assertEqual(observed[0]['reason'], release_compat.UNKNOWN_REASON)
        self.assertEqual(observed[0]['jobs'][0]['name'], 'Q')

    def test_force_requires_request_and_stops_before_activation(self):
        live = [True]
        calls = []
        def jobs(conn):
            return [{'kind': 'Build', 'name': 'plan'}] if live[0] else []
        def sleep(_):
            self.assertEqual(calls, [])
            self.assertEqual(self.active(), '1.0.0')
            selfupdate.release_force_path().write_text('confirmed', encoding='utf-8')
        def stop(conn):
            self.assertEqual(self.active(), '1.0.0')
            calls.append('stop')
            live[0] = False
            return {'ok': True}
        with self.verified_release() as stage, mock.patch('haivequeen.force_stop.stop', side_effect=stop):
            result = selfupdate.run_release_update(self.conn, self.root, '2.0.0', stage=stage, jobs=jobs, sleep=sleep,
                        loop_pid=lambda: 42 if live[0] else 0, start=lambda: True, receipt=lambda *a, **k: True)
        self.assertEqual(calls, ['stop'])
        self.assertEqual(result['result'], 'ok')

    def test_real_stalled_successor_yields_before_verified_old_restart(self):
        lock = Path(self.temp.name) / 'loop.lock'
        code = ("import os,sys,time,pathlib; p=pathlib.Path(sys.argv[1]); "
                "lock=pathlib.Path(sys.argv[2]); lock.write_text(str(os.getpid())); "
                "exec(\"while p.read_text().strip() == '2.0.0': time.sleep(.01)\"); lock.unlink()")
        child = None
        calls = []
        def pid():
            try:
                return int(lock.read_text())
            except (OSError, ValueError):
                return 0
        def receipt(conn, target, began, sleep, poll, timeout, loop_pid):
            nonlocal child
            calls.append(target.name)
            if target.name == '2.0.0':
                child = subprocess.Popen([sys.executable, '-c', code, str(self.root / 'active-version'), str(lock)])
                for _ in range(200):
                    if pid(): break
                    time.sleep(.01)
                self.assertTrue(pid())
                return False
            self.assertEqual(self.active(), '1.0.0')
            return not pid()
        try:
            with self.verified_release() as stage, \
                 mock.patch.object(selfupdate, 'file_fault_rollback') as fault, \
                 mock.patch.object(selfupdate, 'wait_successor_round', side_effect=receipt), \
                 mock.patch.object(selfupdate, 'run_command', return_value=(0, '')):
                stage(self.root, 'example/project', '2.0.0')
                result = selfupdate.run_update(self.conn, live=self.root / 'versions' / '1.0.0', version='2.0.0',
                    poll=.01, round_timeout=3, loop_pid=pid, sleep=time.sleep)
        finally:
            if child:
                child.wait(timeout=5)
        self.assertEqual(result['result'], 'rollback', result)
        self.assertEqual(calls[0], '2.0.0')
        self.assertIn('1.0.0', calls)
        fault.assert_called_once()

    def test_exception_in_successor_check_attempts_old_recovery(self):
        self.metadata(True)
        seen = []
        def receipt(conn, target, *args, **kwargs):
            seen.append(target.name)
            if target.name == '2.0.0':
                raise RuntimeError('receipt failed')
            return True
        with self.verified_release() as stage:
            result = selfupdate.run_release_update(self.conn, self.root, '2.0.0', stage=stage,
                      loop_pid=lambda: 0, start=lambda: True, receipt=receipt)
        self.assertEqual(result['result'], 'failed')
        self.assertEqual(seen, ['2.0.0', '1.0.0'])
        self.assertEqual(self.active(), '1.0.0')
        self.assertFalse(selfupdate.release_lock_path().exists())

    def test_recovery_start_exception_is_reported_and_lock_released(self):
        self.metadata(True)
        def fail_start():
            raise RuntimeError('restart unavailable')
        result = selfupdate.run_release_update(self.conn, self.root, '2.0.0',
                  loop_pid=lambda: 0, start=fail_start)
        self.assertEqual(result['result'], 'failed')
        self.assertIn('restart unavailable', result['note'])
        self.assertEqual(self.active(), '1.0.0')
        self.assertFalse(selfupdate.release_lock_path().exists())

    def test_missing_pointer_releases_update_lock(self):
        (self.root / 'active-version').unlink()
        result = selfupdate.run_release_update(self.conn, self.root, '2.0.0')
        self.assertEqual(result['result'], 'failed')
        self.assertFalse(selfupdate.release_lock_path().exists())

    def test_row_setup_failure_releases_update_lock(self):
        with mock.patch.object(selfupdate, 'start_row', side_effect=RuntimeError('row failed')):
            result = selfupdate.run_release_update(self.conn, self.root, '2.0.0', loop_pid=lambda: 1)
        self.assertEqual(result['result'], 'failed')
        self.assertFalse(selfupdate.release_lock_path().exists())

    def test_failed_successor_restores_pointer_and_reports(self):
        self.metadata(True)
        with self.verified_release() as stage, mock.patch.object(selfupdate, 'file_fault_rollback') as fault:
            result = selfupdate.run_release_update(self.conn, self.root, '2.0.0', stage=stage, loop_pid=lambda: 0,
                                                   start=lambda: True, timeout=0, receipt=lambda *a, **k: False)
        self.assertEqual(result['result'], 'failed')
        self.assertIn('restart was not verified', result['note'])
        self.assertEqual(self.active(), '1.0.0')
        fault.assert_called_once()


if __name__ == '__main__':
    unittest.main()

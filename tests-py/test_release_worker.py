import argparse
import contextlib
import io
import json
import os
import tempfile
import unittest
from pathlib import Path
from unittest import mock

import isolation  # noqa: F401
from haivequeen import db, loop, selfupdate, updates, version_layout


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

    def test_compatible_switch_does_not_drain_or_wait_for_jobs(self):
        self.metadata(True)
        def receipt(conn, target, began, sleep, poll, timeout, loop_pid):
            self.assertEqual(self.active(), '2.0.0')
            self.assertFalse(loop.stop_flag().exists())
            self.assertFalse(selfupdate.lock_path().exists())
            return True
        result = selfupdate.run_release_update(self.conn, self.root, '2.0.0', jobs=lambda c: self.fail('must not wait'),
                                                loop_pid=lambda: 42, receipt=receipt, start=lambda: self.fail('old core hands over'))
        self.assertEqual(result['result'], 'ok')
        self.assertEqual(self.active(), '2.0.0')

    def test_incompatible_waits_for_jobs_and_old_core(self):
        self.metadata(False, 'database change')
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
        result = selfupdate.run_release_update(self.conn, self.root, '2.0.0', jobs=jobs, sleep=sleep,
                    loop_pid=lambda: 42 if live[0] else 0, start=lambda: True, receipt=lambda *a, **k: True)
        self.assertEqual(result['result'], 'ok')
        self.assertEqual(observed[0]['reason'], 'database change')
        self.assertEqual(observed[0]['jobs'][0]['name'], 'Q')

    def test_force_requires_request_and_stops_before_activation(self):
        self.metadata(False, 'shared configuration change')
        live = [True]
        calls = []
        def jobs(conn):
            return [{'kind': 'Build', 'name': 'plan'}] if live[0] else []
        def sleep(_):
            self.assertEqual(calls, [])
            self.assertEqual(self.active(), '1.0.0')
            selfupdate.release_force_path().write_text('confirmed', encoding='utf-8')
        def stop(conn):
            calls.append('stop')
            live[0] = False
            return {'ok': True}
        with mock.patch('haivequeen.force_stop.stop', side_effect=stop):
            result = selfupdate.run_release_update(self.conn, self.root, '2.0.0', jobs=jobs, sleep=sleep,
                        loop_pid=lambda: 42 if live[0] else 0, start=lambda: True, receipt=lambda *a, **k: True)
        self.assertEqual(calls, ['stop'])
        self.assertEqual(result['result'], 'ok')

    def test_failed_successor_restores_pointer_and_reports(self):
        self.metadata(True)
        with mock.patch.object(selfupdate, 'file_fault_rollback') as fault:
            result = selfupdate.run_release_update(self.conn, self.root, '2.0.0', loop_pid=lambda: 42,
                                                   receipt=lambda *a, **k: False)
        self.assertEqual(result['result'], 'rollback')
        self.assertEqual(self.active(), '1.0.0')
        fault.assert_called_once()


if __name__ == '__main__':
    unittest.main()

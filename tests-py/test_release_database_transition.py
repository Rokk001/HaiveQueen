"""Focused safety checks for the supported, reversible database transition."""
import json
import os
from pathlib import Path
import subprocess
import sys
import threading
import time
from unittest.mock import patch

from bzztest import RoundCase
import test_release_compatibility_switch as fixtures
from release_authority_fixture import database_break
from haivequeen import config, db, loop, release_activation, release_contract, release_staging, runtime, runstate, selfupdate, updates, version_leases
from haivequeen.stable_launcher import lifetime_lock


class DatabaseTransitionTests(RoundCase):
    def test_automatic_restart_does_not_open_setup_browser(self):
        with patch.object(selfupdate.subprocess, 'run') as run:
            run.return_value.returncode = 0
            self.assertTrue(selfupdate.start_release_core(self.base))
        self.assertIn('--no-browser', run.call_args.args[0])

    def test_rejected_publication_respects_preexisting_pause(self):
        fixture = self.fixture()
        runstate.pause(self.conn, 'existing owner pause')
        receipts = []
        with patch.object(release_staging, '_fetch', fixture.fetch), patch.object(updates, 'repository', return_value='example/project'):
            result = selfupdate.run_release_update(self.conn, fixture.root, '2.0.0',
                                                  activate=lambda *a, **k: (_ for _ in ()).throw(RuntimeError('publication rejected')),
                                                  jobs=lambda c: [], loop_pid=lambda: None,
                                                  start=lambda: True,
                                                  receipt=lambda *a, **k: receipts.append(True) or True,
                                                  poll=.01, timeout=.1)
        self.assertEqual(result['result'], 'failed', result)
        self.assertEqual(receipts, [True])
        self.assertFalse(runstate.starts_allowed(self.conn))

    def test_rejected_publication_does_not_restart_after_owner_stop(self):
        fixture = self.fixture()
        started = []
        def reject(*args, **kwargs):
            loop.stop_flag().write_text('owner requested stop', encoding='utf-8')
            raise RuntimeError('publication rejected')
        with patch.object(release_staging, '_fetch', fixture.fetch), patch.object(updates, 'repository', return_value='example/project'):
            result = selfupdate.run_release_update(self.conn, fixture.root, '2.0.0', activate=reject,
                                                  jobs=lambda c: [], loop_pid=lambda: None,
                                                  start=lambda: started.append(True) or True,
                                                  receipt=lambda *a, **k: True,
                                                  poll=.01, timeout=.1)
        self.assertEqual(result['result'], 'failed', result)
        self.assertEqual(started, [])
        self.assertFalse(runstate.starts_allowed(self.conn))
        self.assertEqual(loop.stop_flag().read_text(), 'owner requested stop')

    def test_rejected_publication_requires_old_round_before_resuming(self):
        fixture = self.fixture()
        root = fixture.root
        observed = []

        def reject(*args, **kwargs):
            self.assertFalse(runstate.starts_allowed(self.conn))
            raise RuntimeError('target verification failed')

        def receipt(conn, target, *args, **kwargs):
            observed.append(target.name)
            self.assertFalse(runstate.starts_allowed(conn))
            return True

        with patch.object(release_staging, '_fetch', fixture.fetch), patch.object(updates, 'repository', return_value='example/project'):
            result = selfupdate.run_release_update(self.conn, root, '2.0.0', activate=reject,
                                                  jobs=lambda c: [], loop_pid=lambda: None,
                                                  start=lambda: True, receipt=receipt,
                                                  poll=.01, timeout=.1)
        self.assertEqual(result['result'], 'failed', result)
        self.assertEqual(observed, ['1.0.0'])
        self.assertTrue(runstate.starts_allowed(self.conn))
        self.assertIn('old core recovery verified', result['note'])

    def test_rejected_publication_keeps_pause_without_old_round(self):
        fixture = self.fixture()
        with patch.object(release_staging, '_fetch', fixture.fetch), patch.object(updates, 'repository', return_value='example/project'):
            result = selfupdate.run_release_update(self.conn, fixture.root, '2.0.0',
                                                  activate=lambda *a, **k: (_ for _ in ()).throw(RuntimeError('publication rejected')),
                                                  jobs=lambda c: [], loop_pid=lambda: None,
                                                  start=lambda: True, receipt=lambda *a, **k: False,
                                                  poll=.01, timeout=.1)
        self.assertEqual(result['result'], 'failed', result)
        self.assertFalse(runstate.starts_allowed(self.conn))
        self.assertIn('old core recovery not verified', result['note'])

    def test_start_command_returns_while_successor_keeps_output_handle(self):
        root = self.base / 'start-fixture'
        root.mkdir()
        started = root / 'started'
        finish = root / 'finish'
        child = "import os,sys,time;from pathlib import Path;Path(sys.argv[1]).write_text(str(os.getpid()))\nwhile not Path(sys.argv[2]).exists(): time.sleep(.02)"
        (root / 'launcher.py').write_text('import subprocess,sys\nsubprocess.Popen([sys.executable,"-c",%r,%r,%r])\n' % (child, str(started), str(finish)))
        result = []
        completed = threading.Event()
        def run():
            try:
                result.append(selfupdate.start_release_core(root))
            finally:
                completed.set()
        thread = threading.Thread(target=run, daemon=True)
        thread.start()
        try:
            self.assertTrue(completed.wait(10), 'start waited for detached output EOF')
            self.assertEqual(result, [True])
            deadline = time.monotonic() + 10
            while not started.exists() and time.monotonic() < deadline:
                time.sleep(.02)
            self.assertTrue(started.exists())
            self.assertFalse(release_contract.process_ended(int(started.read_text())))
        finally:
            finish.touch()
            thread.join(timeout=10)
            if started.exists():
                deadline = time.monotonic() + 10
                while not release_contract.process_ended(int(started.read_text())) and time.monotonic() < deadline:
                    time.sleep(.02)

    def test_older_database_consumer_refuses_newer_schema_without_writing(self):
        self.conn.execute('PRAGMA user_version = 18')
        opened = None
        try:
            with self.assertRaisesRegex(RuntimeError, 'newer database schema'):
                opened = db.connect(config.db_path())
            self.assertEqual(self.conn.execute('PRAGMA user_version').fetchone()[0], 18)
        finally:
            if opened is not None:
                opened.close()
            self.conn.execute('PRAGMA user_version = 17')

    def fixture(self):
        fixture = fixtures.VerifiedCompatibilityTests('runTest')
        fixture.setUp()
        self.addCleanup(fixture.doCleanups)
        fixture.target = database_break(fixture.target)
        fixture.install()
        return fixture

    def test_pending_or_live_old_launch_prevents_drained_publication(self):
        fixture = self.fixture()
        root = fixture.root
        installs = {k: v for k, v in version_leases._load(root).items() if v.get('installation')}
        with lifetime_lock(root):
            self.assertTrue(release_contract.drained(root, installs))
        token = version_leases.reserve(root, '1.0.0')
        with lifetime_lock(root):
            self.assertFalse(release_contract.drained(root, installs))
        version_leases.publish(root, token, os.getpid())
        with lifetime_lock(root):
            self.assertFalse(release_contract.drained(root, installs))
        version_leases.release(root, token)
        records = version_leases._load(root)
        records['other-install'] = dict(next(iter(installs.values())))
        version_leases._save(root, records)
        with lifetime_lock(root):
            self.assertFalse(release_contract.drained(root, installs))

    def test_exited_launch_with_retained_process_handle_is_quiescent(self):
        fixture = self.fixture()
        root = fixture.root
        installs = {k: v for k, v in version_leases._load(root).items() if v.get('installation')}
        process = subprocess.Popen([sys.executable, '-c', 'pass'])
        token = version_leases.reserve(root, '1.0.0')
        version_leases.publish(root, token, process.pid)
        process.wait(timeout=10)
        # Keep the Popen's Windows process handle open across the check.
        with lifetime_lock(root):
            self.assertTrue(release_contract.drained(root, installs))
        version_leases.release(root, token)

    def test_old_work_admitted_after_drain_rejects_actual_publication(self):
        for live in (False, True):
            with self.subTest(live=live):
                fixture = self.fixture()
                root = fixture.root
                first_round = []
                def activate(*args, **kwargs):
                    token = version_leases.reserve(root, '1.0.0')
                    if live:
                        version_leases.publish(root, token, os.getpid())
                    return release_activation.activate(*args, **kwargs)
                with patch.object(release_staging, '_fetch', fixture.fetch), patch.object(updates, 'repository', return_value='example/project'):
                    result = selfupdate.run_release_update(self.conn, root, '2.0.0', activate=activate,
                                                           jobs=lambda c: [], loop_pid=lambda: None,
                                                           start=lambda: False,
                                                           receipt=lambda *a, **k: first_round.append(True),
                                                           poll=.01, timeout=1)
                self.assertEqual(result['result'], 'failed', result)
                self.assertIn('target verification failed', result['note'])
                self.assertEqual((root / 'active-version').read_text().strip(), '1.0.0')
                self.assertEqual(self.conn.execute('PRAGMA user_version').fetchone()[0], 17)
                self.assertEqual(first_round, [])

    def test_failed_target_round_restores_shared_rows_and_real_old_round(self):
        fixture = self.fixture()
        root = fixture.root
        runtime.usage_add(self.conn, actions='before migration', kind='transition')
        (self.base / 'cfg/config.toml').write_text('roundMinutes=0.01\nslots=0\nramReserveMb=0\n')
        processes = []
        logs = []

        def start():
            version = (root / 'active-version').read_text().strip()
            code_path = root / 'versions' / version
            script = '''import sys
sys.path.insert(0, sys.argv[1])
from haivequeen import config,db,loop,runtime
c=db.connect(config.db_path())
loop.stop_flag().unlink(missing_ok=True)
if sys.argv[2] == '2.0.0':
    runtime.usage_add(c, actions='during failed round', kind='transition')
    def fail(*args, **kwargs):
        raise RuntimeError('owned first-round failure')
    loop.run_round=fail
try:
    loop.run_loop(c, config.load_config(), round_minutes=.001, wait_seconds=.02)
finally:
    c.close()
'''
            log = (self.base / ('core-%s-%s.log' % (version, len(processes)))).open('w')
            logs.append(log)
            processes.append(subprocess.Popen([sys.executable, '-I', '-c', script, str(code_path), version],
                                              env=dict(os.environ, BZZ_NO_DESKTOP='1'), stdout=log, stderr=log))
            deadline = time.monotonic() + 10
            while not loop.loop_running() and processes[-1].poll() is None and time.monotonic() < deadline:
                time.sleep(.02)
            return bool(loop.loop_running())

        try:
            with patch.object(release_staging, '_fetch', fixture.fetch), patch.object(updates, 'repository', return_value='example/project'):
                result = selfupdate.run_release_update(self.conn, root, '2.0.0', start=start,
                                                       poll=.05, timeout=10)
            self.assertEqual(result['result'], 'rollback', result)
            self.assertEqual((root / 'active-version').read_text().strip(), '1.0.0')
            self.assertEqual(self.conn.execute('PRAGMA user_version').fetchone()[0], 17)
            columns = {r[1] for r in self.conn.execute('PRAGMA table_info(providers_usage)')}
            self.assertIn('actions', columns)
            self.assertNotIn('actions_json', columns)
            rows = runtime.usage_list(self.conn, kind='transition')
            self.assertEqual([row['actions'] for row in rows], ['before migration', 'during failed round'])
            receipt = json.loads(runtime.kv_get(self.conn, '*', 'loop-completed-round'))
            self.assertEqual(Path(receipt['code_path']), root / 'versions/1.0.0')
            self.assertEqual(receipt['pid'], loop.loop_running())
        finally:
            loop.stop_flag().parent.mkdir(parents=True, exist_ok=True)
            loop.stop_flag().touch()
            for process in processes:
                try:
                    process.wait(timeout=20)
                except subprocess.TimeoutExpired:
                    # Every handle here was started by this fixture.
                    process.terminate()
                    process.wait(timeout=10)
            for log in logs:
                log.close()
            # A short-lived helper can retain the inherited descriptor after
            # the core process has exited. Verify closure of each owned log.
            for path in self.base.glob('core-*.log'):
                deadline = time.monotonic() + 20
                while True:
                    try:
                        probe = path.with_suffix('.closed')
                        path.rename(probe)
                        probe.rename(path)
                        break
                    except PermissionError:
                        if time.monotonic() >= deadline:
                            raise
                        time.sleep(.02)

    def test_pending_target_launch_prevents_shared_schema_recovery(self):
        fixture = self.fixture()
        root = fixture.root
        def start():
            if (root / 'active-version').read_text().strip() == '2.0.0':
                version_leases.reserve(root, '2.0.0')
            return False
        with patch.object(release_staging, '_fetch', fixture.fetch), patch.object(updates, 'repository', return_value='example/project'), patch.object(release_contract, 'restore_database', wraps=release_contract.restore_database) as restore:
            result = selfupdate.run_release_update(self.conn, root, '2.0.0', jobs=lambda c: [],
                                                   loop_pid=lambda: None, start=start,
                                                   poll=.01, timeout=.05)
        self.assertEqual(result['result'], 'failed', result)
        self.assertFalse(restore.called, 'recovery ignored an unpublished target launch')

    def test_inactive_target_cannot_migrate_the_shared_database(self):
        fixture = self.fixture()
        command = [sys.executable, '-I', '-c',
                   'import sys;sys.path.insert(0,sys.argv[1]);from haivequeen import config,db;c=db.connect(config.db_path());c.close()',
                   str(fixture.root / 'versions/2.0.0')]
        try:
            result = subprocess.run(command, capture_output=True, text=True, timeout=30)
            self.assertNotEqual(result.returncode, 0, result.stdout + result.stderr)
            self.assertIn('inactive HQ version cannot migrate', result.stderr)
            self.assertEqual(self.conn.execute('PRAGMA user_version').fetchone()[0], 17)
        finally:
            release_contract.restore_database(self.conn)

    def test_inactive_version_can_initialize_a_private_database(self):
        fixture = fixtures.VerifiedCompatibilityTests('runTest')
        fixture.setUp()
        self.addCleanup(fixture.doCleanups)
        fixture.install()
        from haivequeen import version_layout
        version_layout.set_active(fixture.root, '2.0.0')
        private = self.base / 'private.db'
        command = [sys.executable, '-I', '-c',
                   'import sys;sys.path.insert(0,sys.argv[1]);from haivequeen import db;c=db.connect(sys.argv[2]);print(c.execute("PRAGMA user_version").fetchone()[0]);c.close()',
                   str(fixture.root / 'versions/1.0.0'), str(private)]
        result = subprocess.run(command, capture_output=True, text=True, timeout=30)
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        self.assertEqual(result.stdout.strip(), '17')

    def test_final_quiescence_and_schema_recovery_hold_the_launch_lock(self):
        fixture = self.fixture()
        root = fixture.root
        admitted = threading.Event()
        attempting = threading.Event()
        observed = []
        threads = []
        original = release_contract.restore_database
        def restore(conn):
            def launch():
                attempting.set()
                version_leases.reserve(root, '2.0.0')
                admitted.set()
            thread = threading.Thread(target=launch)
            threads.append(thread)
            thread.start()
            self.assertTrue(attempting.wait(5))
            observed.append(admitted.wait(1))
            original(conn)
        try:
            with patch.object(release_staging, '_fetch', fixture.fetch), patch.object(updates, 'repository', return_value='example/project'), patch.object(release_contract, 'restore_database', side_effect=restore):
                result = selfupdate.run_release_update(self.conn, root, '2.0.0', jobs=lambda c: [],
                                                       loop_pid=lambda: None, start=lambda: False,
                                                       poll=.01, timeout=2)
            self.assertEqual(result['result'], 'failed', result)
            self.assertEqual(observed, [False], 'target launch crossed the schema recovery boundary')
        finally:
            for thread in threads:
                thread.join(timeout=10)

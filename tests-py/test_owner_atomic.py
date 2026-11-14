"""Direct owner decisions keep plan files and database changes recoverable together."""
from pathlib import Path
import subprocess
import sys
from unittest import mock

from bzztest import REL, RoundCase
from haivequeen import config, db, filetxn, hives, owner


class OwnerAtomic(RoundCase):
    def setUp(self):
        super().setUp()
        self.plan('REVIEW', [{'id': 'P1'}])
        ready = db.add_ready(self.conn, 'testproj', 'plan-branch', 'abcdef012345', REL, '')
        self.conn.execute("UPDATE queue SET status='integrated' WHERE id=?", (ready['id'],))
        self.conn.execute(
            "INSERT INTO hives(project, plan_path, provider, colour, state, ready_id) VALUES (?,?,?,?,?,?)",
            ('testproj', REL, 'test', 'blue', 'owner_check', ready['id']))
        self.qid = db.add_question(self.conn, 'testproj', REL, 'owner-check', 'Check?', '')
        self.path = self.repo / REL

    def snapshot(self):
        return '\n'.join(self.conn.iterdump())

    def test_missing_file_rolls_back_accept_and_reject(self):
        self.path.unlink()
        before = self.snapshot()
        for action in ('accept', 'reject'):
            with self.subTest(action=action):
                with self.assertRaisesRegex(Exception, 'plan file not found'):
                    owner.apply(self.conn, action, 'testproj', REL, text='Fix the lighting')
                self.assertEqual(self.snapshot(), before)

    def test_late_database_failure_does_not_publish_plan_file(self):
        before, contents = self.snapshot(), self.path.read_bytes()
        with mock.patch.object(hives, 'finish_hive', side_effect=RuntimeError('late write failed')):
            with self.assertRaisesRegex(RuntimeError, 'late write failed'):
                owner.apply(self.conn, 'accept', 'testproj', REL)
        self.assertEqual(self.snapshot(), before)
        self.assertEqual(self.path.read_bytes(), contents)

    def test_locked_plan_has_durable_recovery_without_duplicate_decision(self):
        original = filetxn.atomic_write
        def locked_plan(path, contents, **kwargs):
            if Path(path) == self.path:
                raise PermissionError('plan locked')
            return original(path, contents, **kwargs)
        with mock.patch.object(filetxn, 'atomic_write', side_effect=locked_plan):
            with self.assertRaises(filetxn.RecoveryError):
                owner.apply(self.conn, 'accept', 'testproj', REL)
        self.assertIn(b'REVIEW', self.path.read_bytes())
        self.assertEqual(len(list(filetxn._journals().glob('*.json'))), 1)
        reopened = db.connect(config.db_path())
        reopened.close()
        self.assertIn(b'DONE', self.path.read_bytes())
        self.assertEqual(self.conn.execute('SELECT status FROM questions WHERE id=?', (self.qid,)).fetchone()[0], 'answered')
        self.assertEqual(self.conn.execute("SELECT count(*) FROM decisions WHERE item='owner-check'").fetchone()[0], 1)
        self.assertEqual(list(filetxn._journals().glob('*.json')), [])

    def test_successful_reject_updates_file_and_question_together(self):
        owner.apply(self.conn, 'reject', 'testproj', REL, text='Fix the lighting', packages=['P1'])
        self.assertIn(b'RUNNING', self.path.read_bytes())
        self.assertEqual(self.conn.execute('SELECT owner_check_result FROM questions WHERE id=?', (self.qid,)).fetchone()[0], 'rejected')
        self.assertEqual(list(filetxn._journals().glob('*.json')), [])

    def crash_accept(self, point):
        script = '''
import os, sys
from haivequeen import config, db, filetxn, owner
conn = db.connect(config.db_path())
if sys.argv[1] == 'prepare':
    original = filetxn.Batch.prepare
    def crash(self, *args, **kwargs):
        original(self, *args, **kwargs)
        os._exit(71)
    filetxn.Batch.prepare = crash
else:
    filetxn.finish = lambda *args: os._exit(72)
owner.apply(conn, 'accept', 'testproj', sys.argv[2])
'''
        result = subprocess.run([sys.executable, '-c', script, point, REL],
                                capture_output=True, text=True, timeout=30)
        self.assertEqual(result.returncode, 71 if point == 'prepare' else 72, result.stderr)

    def test_process_exit_before_commit_preserves_original_state(self):
        before, contents = self.snapshot(), self.path.read_bytes()
        self.crash_accept('prepare')
        reopened = db.connect(config.db_path())
        reopened.close()
        self.assertEqual(self.snapshot(), before)
        self.assertEqual(self.path.read_bytes(), contents)
        self.assertEqual(list(filetxn._journals().glob('*.json')), [])

    def test_process_exit_after_commit_recovers_plan_before_next_database_open(self):
        self.crash_accept('commit')
        self.assertIn(b'REVIEW', self.path.read_bytes())
        reopened = db.connect(config.db_path())
        reopened.close()
        self.assertIn(b'DONE', self.path.read_bytes())
        self.assertEqual(self.conn.execute('SELECT status FROM questions WHERE id=?', (self.qid,)).fetchone()[0], 'answered')
        self.assertEqual(self.conn.execute("SELECT count(*) FROM decisions WHERE item='owner-check'").fetchone()[0], 1)
        self.assertEqual(list(filetxn._journals().glob('*.json')), [])

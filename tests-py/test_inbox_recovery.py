"""Real process interruptions across the inbox database/file commit boundary."""
import subprocess
import sqlite3
import sys
import threading
from pathlib import Path
from unittest import mock

from bzztest import REL, RoundCase
from haivequeen import config, db, filetxn, settings, inbox, owner, runtime, servers


CHILD = r'''
import os, sys
from pathlib import Path
from types import SimpleNamespace
from haivequeen import config, db, filetxn, inbox
conn = db.connect(config.db_path())
mode, workspace = sys.argv[1:]
env = SimpleNamespace(conn=conn, name='testproj', workspace=Path(workspace),
                      settings={}, log=lambda *a, **k: None, event=lambda *a, **k: None)
if mode == 'prepare':
    original = filetxn.Batch.prepare
    def crash(self, *args):
        original(self, *args)
        os._exit(71)
    filetxn.Batch.prepare = crash
elif mode == 'commit':
    filetxn.finish = lambda *args: os._exit(72)
elif mode == 'file':
    original = filetxn.atomic_write
    def crash(path, contents):
        original(path, contents)
        if Path(path).name == 'config.toml':
            os._exit(73)
    filetxn.atomic_write = crash
elif mode == 'claim':
    original = Path.rename
    def crash(path, target):
        result = original(path, target)
        if path.name == 'batch.toml':
            os._exit(75)
        return result
    Path.rename = crash
elif mode in ('archive', 'unlink'):
    if mode == 'archive':
        filetxn._acknowledge = lambda *args: os._exit(74)
    original = Path.unlink
    def crash(path, *args, **kwargs):
        if path.suffix == '.ack' and path.parent.name == 'inbox':
            original(path, *args, **kwargs)
            os._exit(74)
        return original(path, *args, **kwargs)
    Path.unlink = crash
inbox.process_inbox(env)
'''


class InboxRecovery(RoundCase):
    def setUp(self):
        super().setUp()
        self.plan('RUNNING', packages=[{'id': 'P1', 'class': 'medium'}])
        self.settings_path = config.config_dir() / 'config.toml'
        self.settings_path.parent.mkdir(parents=True, exist_ok=True)
        self.original = b'[[providers]]\nname="account"\nprotocol="codex"\naccess="subscription"\nenabled=true\n'
        self.settings_path.write_bytes(self.original)
        self.box = inbox.inbox_dir(self.repo)
        self.box.mkdir(parents=True)
        self.source = self.box / 'batch.toml'
        self.contents = ('[[decision]]\nsource="owner"\ntext="Once only"\n'
                         '[[provider]]\nprovider="account"\naction="disable"\n'
                         '[[pause]]\nplan="%s"\n' % REL).encode()
        self.source.write_bytes(self.contents)

    def crash(self, mode):
        completed = subprocess.run([sys.executable, '-c', CHILD, mode, str(self.repo)],
                                   capture_output=True, text=True, timeout=30)
        self.assertIn(completed.returncode, (71, 72, 73, 74, 75), completed.stderr)

    def check_recovered(self):
        self.assertEqual(inbox.process_inbox(self.env()), {'done': [], 'failed': []})
        self.assertFalse(self.source.exists())
        self.assertFalse(settings.load_migrated(self.settings_path)['providers'][0]['enabled'])
        self.assertEqual(self.settings_path.with_suffix('.toml.bak').read_bytes(), self.original)
        self.assertIn('PAUSED', (self.repo / REL).read_text())
        self.assertEqual(runtime.list_plans(self.conn, 'testproj')[0]['status'], 'PAUSED')
        decisions = runtime.list_decisions(self.conn, 'testproj', active_only=False)
        self.assertEqual([d['text'] for d in decisions].count('Once only'), 1)
        self.assertEqual([p.read_bytes() for p in (self.box / 'done').iterdir()], [self.contents])
        self.assertEqual(list(filetxn._journals().glob('*.json')), [])
        self.assertEqual(list(self.box.glob('*.ack')), [])

    def test_killed_before_commit_discards_staged_files_and_retries(self):
        self.crash('prepare')
        self.assertEqual(self.settings_path.read_bytes(), self.original)
        self.assertIn('RUNNING', (self.repo / REL).read_text())
        self.assertEqual(inbox.process_inbox(self.env())['done'], ['batch.toml'])
        self.check_recovered()

    def test_killed_after_commit_recovers_before_database_open(self):
        self.crash('commit')
        self.assertEqual(self.settings_path.read_bytes(), self.original)
        reopened = db.connect(config.db_path())
        reopened.close()
        self.check_recovered()

    def test_killed_during_file_publication(self):
        self.crash('file')
        self.check_recovered()

    def test_killed_after_archive_before_source_removal(self):
        self.crash('archive')
        self.check_recovered()

    def test_killed_after_source_removal_before_journal_removal(self):
        self.crash('unlink')
        self.check_recovered()

    def test_killed_after_source_claim_before_verification(self):
        self.crash('claim')
        self.check_recovered()

    def test_archive_failure_keeps_committed_batch_pending(self):
        original = filetxn.atomic_write
        def fail_archive(path, contents):
            if Path(path).parent.name == 'done':
                raise PermissionError('archive unavailable')
            return original(path, contents)
        with mock.patch.object(filetxn, 'atomic_write', side_effect=fail_archive):
            with self.assertRaises(filetxn.RecoveryError):
                inbox.process_inbox(self.env())
        self.assertFalse((self.box / 'failed').exists())
        self.check_recovered()

    def test_failed_commit_rolls_back_before_journal_recovery(self):
        conn = self.conn
        class FailingCommit:
            def __getattr__(self, name):
                return getattr(conn, name)

            def execute(self, sql, *args):
                if sql == 'COMMIT':
                    raise sqlite3.OperationalError('commit unavailable')
                return conn.execute(sql, *args)

        env = self.env()
        env.conn = FailingCommit()
        with self.assertRaisesRegex(sqlite3.OperationalError, 'commit unavailable'):
            inbox.process_inbox(env)
        self.assertFalse(conn.in_transaction)
        self.assertEqual(self.settings_path.read_bytes(), self.original)
        self.assertTrue(self.source.exists())
        self.assertEqual(runtime.list_decisions(conn, 'testproj', active_only=False), [])
        self.assertEqual(inbox.process_inbox(self.env())['done'], ['batch.toml'])
        self.check_recovered()

    def test_recovery_preserves_external_edit_and_blocks_other_writers(self):
        self.crash('commit')
        concurrent = self.original + b'# owner edit after interruption\n'
        self.settings_path.write_bytes(concurrent)
        with self.assertRaises(filetxn.RecoveryError):
            inbox.process_inbox(self.env())
        with self.assertRaises(filetxn.RecoveryError):
            servers._write_tests({'other': {}})
        self.assertEqual(self.settings_path.read_bytes(), concurrent)
        self.assertIn('RUNNING', (self.repo / REL).read_text())
        self.assertTrue(self.source.exists())
        self.assertEqual(len(list(filetxn._journals().glob('*.json'))), 1)
        # Resolving the conflict to the recorded original permits recovery.
        self.settings_path.write_bytes(self.original)
        self.check_recovered()

    def test_recovery_preserves_replaced_inbox_source(self):
        self.crash('commit')
        replacement = b'[[decision]]\nsource="owner"\ntext="New message"\n'
        self.source.write_bytes(replacement)
        with filetxn.locked():
            self.assertEqual(self.source.read_bytes(), replacement)
            self.assertEqual([p.read_bytes() for p in (self.box / 'done').iterdir()], [self.contents])
        self.assertEqual(inbox.process_inbox(self.env())['done'], ['batch.toml'])
        texts = [d['text'] for d in runtime.list_decisions(self.conn, 'testproj', active_only=False)]
        self.assertEqual(texts.count('Once only'), 1)
        self.assertEqual(texts.count('New message'), 1)

    def test_producer_winning_acknowledgement_race_keeps_its_message(self):
        rename = Path.rename
        replacement = b'[[decision]]\nsource="owner"\ntext="Concurrent message"\n'
        def replace_before_claim(path, target):
            if path == self.source:
                path.write_bytes(replacement)
            return rename(path, target)
        with mock.patch.object(Path, 'rename', replace_before_claim):
            self.assertEqual(inbox.process_inbox(self.env())['done'], ['batch.toml'])
        remaining = list(self.box.glob('*.toml'))
        self.assertEqual(len(remaining), 1)
        self.assertEqual(remaining[0].read_bytes(), replacement)
        inbox.process_inbox(self.env())
        texts = [d['text'] for d in runtime.list_decisions(self.conn, 'testproj', active_only=False)]
        self.assertEqual(texts.count('Once only'), 1)
        self.assertEqual(texts.count('Concurrent message'), 1)

    def test_stale_settings_page_cannot_overwrite_owner_command(self):
        page = settings.load_migrated(self.settings_path)
        owner.apply(self.conn, 'provider', provider='account', provider_action='disable')
        page['slots'] = 9
        with self.assertRaisesRegex(ValueError, 'reload'):
            settings.save_file(self.settings_path, page)
        self.assertFalse(settings.load_migrated(self.settings_path)['providers'][0]['enabled'])

    def test_other_thread_writer_waits_and_keeps_committed_changes(self):
        attempted, completed = threading.Event(), threading.Event()
        errors = []
        def writer():
            attempted.set()
            try:
                data = settings.load_migrated(self.settings_path)
                data['slots'] = 9
                settings.save_file(self.settings_path, data)
            except BaseException as exc:
                errors.append(exc)
            finally:
                completed.set()
        with filetxn.locked():
            thread = threading.Thread(target=writer)
            thread.start()
            self.assertTrue(attempted.wait(5))
            self.assertFalse(completed.wait(.05))
            inbox.process_inbox(self.env())
        thread.join(10)
        self.assertFalse(thread.is_alive())
        self.assertEqual(errors, [])
        data = settings.load_migrated(self.settings_path)
        self.assertEqual(data['slots'], 9)
        self.assertFalse(data['providers'][0]['enabled'])

    def test_later_failure_discards_plan_backup_and_test_result_staging(self):
        self.source.write_text('[[pause]]\nplan="%s"\n[[provider]]\nprovider="account"\naction="disable"\n'
                               '[[unassign]]\nplan="%s"\npackage="P1"\n' % (REL, REL))
        before = (self.repo / REL).read_bytes()
        apply = owner.apply
        def fail(conn, action, *args, **kwargs):
            if action == 'provider':
                servers._write_tests({'account': {'reachable': True}})
            if action == 'unassign':
                raise ValueError('later failure')
            return apply(conn, action, *args, **kwargs)
        with mock.patch.object(owner, 'apply', side_effect=fail):
            self.assertEqual(inbox.process_inbox(self.env())['failed'], ['batch.toml'])
        self.assertEqual((self.repo / REL).read_bytes(), before)
        self.assertEqual(self.settings_path.read_bytes(), self.original)
        self.assertFalse(self.settings_path.with_suffix('.toml.bak').exists())
        self.assertFalse(servers.tests_file().exists())

    def test_local_connection_result_is_staged_and_recovered_after_commit(self):
        self.settings_path.write_text('[[providers]]\nname="account"\nprotocol="codex"\naccess="llm"\naddress="http://localhost:1234"\n')
        self.source.write_text('[[provider]]\nprovider="account"\naction="test"\n[[pause]]\nplan="%s"\n' % REL)
        with mock.patch.object(servers, '_request', return_value=(200, '{"data":[{"id":"model"}]}')), mock.patch.object(servers, 'summary', return_value='Connection usable'):
            with mock.patch.object(filetxn, 'finish', side_effect=KeyboardInterrupt('after commit')):
                with self.assertRaises(KeyboardInterrupt):
                    inbox.process_inbox(self.env())
        self.assertFalse(servers.tests_file().exists())
        self.assertEqual(inbox.process_inbox(self.env()), {'done': [], 'failed': []})
        result = servers._read_tests()['account']
        self.assertTrue(result['reachable'])
        self.assertEqual(result['models'], ['model'])
        self.assertIn('PAUSED', (self.repo / REL).read_text())

import json
from unittest import mock

from bzztest import RoundCase, REL
from haivequeen import db, filetxn, hives, runtime


class CorruptRecoveryScopeTests(RoundCase):
    def test_corrupt_journal_blocks_affected_project_but_not_reads_or_other_project(self):
        self.plan('RUNNING', [{'id': 'P1'}])
        other = self.add_project('other')
        target = self.repo / REL
        original = target.read_bytes()
        with filetxn.staging() as batch:
            with db.transaction(self.conn):
                filetxn.write(target, original + b'\n')
                journal, record = batch.prepare(self.conn)
        intact = journal.read_bytes()
        journal.write_bytes(b'{broken')
        # Recovery must keep the damaged bytes and must not publish uncertain writes.
        self.assertEqual(runtime.list_plans(self.conn, 'testproj')[0]['status'], 'RUNNING')
        self.assertIn('recovery', hives.start_block_reason(self.conn, 'testproj', REL))
        self.assertEqual(hives.start_block_reason(self.conn, 'other'), '')
        self.assertEqual(target.read_bytes(), original)
        self.assertEqual(journal.read_bytes(), b'{broken')
        with filetxn.locked():
            with self.assertRaises(filetxn.RecoveryError):
                filetxn.write(target, b'overwrite')
            filetxn.write(other / 'independent.txt', b'allowed')
        # Restoring verified journal evidence replays the committed operation once.
        journal.write_bytes(intact)
        self.assertEqual(hives.start_block_reason(self.conn, 'testproj', REL), '')
        self.assertEqual(target.read_bytes(), original + b'\n')
        self.assertFalse(journal.exists())

    def test_legacy_corruption_with_unknown_scope_fails_closed_but_allows_inspection(self):
        self.plan('RUNNING', [{'id': 'P1'}])
        folder = filetxn._journals()
        folder.mkdir(parents=True, exist_ok=True)
        journal = folder / 'legacy.json'
        journal.write_bytes(b'not json')
        self.assertEqual(len(runtime.list_plans(self.conn, 'testproj')), 1)
        self.assertIn('recovery', hives.start_block_reason(self.conn, 'testproj', REL))
        self.assertEqual(journal.read_bytes(), b'not json')

    def test_parseable_corruption_is_not_replayed(self):
        self.plan('RUNNING', [{'id': 'P1'}])
        target = self.repo / REL
        original = target.read_bytes()
        with filetxn.staging() as batch:
            with db.transaction(self.conn):
                filetxn.write(target, original + b'\n')
                journal, record = batch.prepare(self.conn)
        record['files'][0]['after'] = filetxn._encode(b'corrupt replacement')
        journal.write_text(json.dumps(record), encoding='utf-8')
        self.assertIn('recovery', hives.start_block_reason(self.conn, 'testproj', REL))
        self.assertEqual(target.read_bytes(), original)

import json
import os
import unittest
from pathlib import Path
from unittest import mock
from datetime import timedelta

import test_retention
from haivequeen import retention


class ActionLogRetentionTests(unittest.TestCase):
    setUp = test_retention.RetentionTest.setUp
    touch = test_retention.RetentionTest.touch

    def journal(self, name, records, age=40):
        path = self.data / 'orchestrator/logs/project/plan' / (name + '.jsonl')
        self.touch(path, age)
        path.write_text(''.join(json.dumps(r) + '\n' for r in records), encoding='utf-8')
        stamp = (self.now - timedelta(days=age)).timestamp()
        os.utime(path, (stamp, stamp))
        return path

    def records(self, age=40):
        return [{'event': 'start', 'result': 'running'},
                {'event': 'output', 'text': 'evidence'},
                {'event': 'end', 'ended_at': (self.now - timedelta(days=age)).astimezone().isoformat(),
                 'exit_code': 1, 'result': 'failed'}]

    def clean(self, days=30):
        return retention.cleanup(self.conn, {'retention': {'logsDays': days}},
                                 now=self.now, data_dir=self.data, runs_dir=self.runs, projects=[])

    def test_completed_expired_journals_removed_but_active_recent_and_invalid_kept(self):
        old = self.journal('old', self.records())
        kept = [self.journal('active', self.records()[:-1]),
                self.journal('recent-end', self.records(2)),
                self.journal('recent-write', self.records(), age=2),
                self.journal('no-start', self.records()[1:]),
                self.journal('invalid-end', [{'event': 'start'}, {'event': 'end', 'ended_at': 'bad'}]),
                self.journal('after-end', self.records() + [{'event': 'output', 'text': 'still writing'}])]
        truncated = self.journal('truncated', self.records()[:-1])
        with truncated.open('a') as stream:
            stream.write('{"event":"end"')
        stamp = (self.now - timedelta(days=40)).timestamp()
        os.utime(truncated, (stamp, stamp))
        kept.append(truncated)
        self.assertEqual(self.clean()['logs'], 1)
        self.assertFalse(old.exists())
        for path in kept:
            self.assertTrue(path.exists(), path.name)

    def test_zero_disables_and_custom_period_applies(self):
        path = self.journal('old', self.records())
        self.assertEqual(self.clean(0)['logs'], 0)
        self.assertTrue(path.exists())
        self.assertEqual(self.clean(60)['logs'], 0)
        self.assertEqual(self.clean(10)['logs'], 1)

    def test_changed_journal_is_preserved(self):
        path = self.journal('changed', self.records())
        original = retention.db.parse_time

        def change(text):
            with path.open('a', encoding='utf-8') as stream:
                stream.write('{"event":"output","text":"late"}\n')
            return original(text)

        with mock.patch.object(retention.db, 'parse_time', side_effect=change):
            self.assertEqual(self.clean()['logs'], 0)
        self.assertTrue(path.exists())

    def test_linked_directory_or_journal_is_not_followed(self):
        path = self.journal('linked', self.records())
        for target in (path, path.parent, path.parent.parent,
                       path.parent.parent.parent, path.parent.parent.parent.parent):
            with self.subTest(target=target.name), mock.patch.object(
                    Path, 'is_symlink', autospec=True, side_effect=lambda p: p == target):
                self.assertEqual(self.clean()['logs'], 0)
                self.assertTrue(path.exists())
        with mock.patch.object(Path, 'is_junction', create=True,
                               side_effect=lambda: True):
            self.assertEqual(self.clean()['logs'], 0)
        self.assertTrue(path.exists())

    def test_unreadable_journal_is_preserved(self):
        path = self.journal('unreadable', self.records())
        with mock.patch.object(Path, 'open', side_effect=OSError('unavailable')):
            self.assertEqual(self.clean()['logs'], 0)
        self.assertTrue(path.exists())


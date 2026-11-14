import json
from pathlib import Path
from unittest import mock

from bzztest import RoundCase, REL
from haivequeen import action_logs, web_api, web_log_pages


class WebLogPagesTests(RoundCase):
    def setUp(self):
        super().setUp()
        self.plan(branch='test', worktree=str(self.wt))

    def request(self, **data):
        return web_api.execute(self.conn, 'plan/step-log',
                               {'project': 'testproj', 'plan': REL, 'step': 'Build', **data})

    def test_console_payload_is_bounded_not_duplicated_and_consumed_output_is_not_read_again(self):
        path = action_logs.start('testproj', REL, 'Build')
        for _ in range(100):
            action_logs.append(path, 'x' * 1000 + '\n')
        action_logs.finish(path, 1)
        with mock.patch.object(action_logs, 'read', side_effect=AssertionError('full read')):
            first = self.request()
            self.assertEqual(first['status'], 'red')
            self.assertEqual(len(first['output']), web_log_pages.PAGE_SIZE)
            self.assertNotIn('console_output', first)
            self.assertLess(len(json.dumps(first)), 40000)
            next_page = self.request(run=first['run'], cursor=first['cursor'])
        self.assertGreater(next_page['cursor'][0], first['cursor'][0])

    def test_new_run_resets_cursor_and_old_reference_selection(self):
        old = action_logs.start('testproj', REL, 'Build')
        action_logs.append(old, 'old')
        first = self.request()
        new = action_logs.start('testproj', REL, 'Build')
        action_logs.append(new, 'new')
        result = self.request(run=first['run'], cursor=first['cursor'], source='old source')
        self.assertTrue(result['reset'])
        self.assertEqual(result['source'], '')
        self.assertEqual(result['output'], 'new')

    def test_references_are_metadata_only_and_selected_file_can_be_paged(self):
        folder = self.wt / 'out'
        folder.mkdir(exist_ok=True)
        expected = 'Grüße 🙂\n' * 10000
        (folder / 'check.txt').write_text(expected, encoding='utf-16', newline='')
        path = action_logs.start('testproj', REL, 'Build')
        action_logs.append(path, 'see out/check.txt\n')
        first = self.request()
        source = first['references'][0]['source']
        self.assertNotIn('content', first['references'][0])
        cursor, parts = None, []
        while True:
            result = self.request(source=source, run=first['run'], cursor=cursor)
            parts.append(result['output'])
            if not result['more']:
                break
            cursor = result['cursor']
        self.assertEqual(''.join(parts), expected)
        with self.assertRaises(ValueError):
            self.request(source=source[:-5] + 'wrong', run=first['run'])

    def test_unfinished_utf8_file_character_is_retried(self):
        path = self.base / 'file.log'
        raw = 'abc🙂'.encode()
        path.write_bytes(raw[:-1])
        first = web_log_pages.file_page(path, None)
        self.assertEqual(first['output'], 'abc')
        path.write_bytes(raw)
        self.assertEqual(web_log_pages.file_page(path, first['cursor'])['output'], '🙂')

    def test_file_reference_split_between_pages_is_discovered(self):
        folder = self.wt / 'out'
        folder.mkdir(exist_ok=True)
        (folder / 'check.txt').write_text('failure', encoding='utf-8')
        path = action_logs.start('testproj', REL, 'Build')
        action_logs.append(path, 'x' * (web_log_pages.PAGE_SIZE - 8) + '\nsee out/check.txt\n')
        first = self.request()
        second = self.request(run=first['run'], cursor=first['cursor'], continuation=first.get('continuation'))
        self.assertEqual([item['path'] for item in second['references']], ['out/check.txt'])

    def test_large_terminal_message_preserves_failed_run_status(self):
        path = action_logs.start('testproj', REL, 'Build')
        action_logs.append(path, 'compiler failed\n')
        action_logs.finish(path, 7, message='diagnostic detail\n' * 10000)
        result = self.request()
        self.assertEqual(result['status'], 'red')
        self.assertEqual(result['exit_code'], 7)
        self.assertIsNotNone(result['ended_at'])
        parts = [result['output']]
        while result['more']:
            result = self.request(run=result['run'], cursor=result['cursor'])
            self.assertEqual(result['status'], 'red')
            self.assertLessEqual(len(result['output']), web_log_pages.PAGE_SIZE)
            parts.append(result['output'])
        self.assertEqual(''.join(parts), 'compiler failed\n' + 'diagnostic detail\n' * 10000)
        with mock.patch.object(Path, 'open', side_effect=AssertionError('unchanged metadata reread')):
            cached = action_logs.metadata(path)
        self.assertEqual(cached[-1]['exit_code'], 7)

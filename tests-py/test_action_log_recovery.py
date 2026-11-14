import json
from pathlib import Path

from bzztest import RoundCase, REL
from haivequeen import action_logs


class ActionLogRecoveryTests(RoundCase):
    def damaged(self, raw):
        path = action_logs.start('testproj', REL, 'Check')
        action_logs.append(path, 'before\n')
        with Path(path).open('ab') as stream:
            stream.write(raw)
        action_logs.append(path, 'after\n')
        action_logs.finish(path, 7)
        return path

    def test_corruption_is_reported_and_later_output_and_result_are_recovered(self):
        for raw in (b'{broken\n', b'[]\n', b'\xff\xfe\n'):
            with self.subTest(raw=raw):
                path = self.damaged(raw)
                records = action_logs.read(path)
                text = ''.join(r.get('text', '') for r in records)
                self.assertIn('before\n', text)
                self.assertIn('after\n', text)
                self.assertIn('damaged log record', text)
                self.assertEqual(records[-1]['exit_code'], 7)
                before = Path(path).read_bytes()
                action_logs.finish(path, 0)
                self.assertEqual(Path(path).read_bytes(), before, 'existing terminal result must not be duplicated')

    def test_paged_reader_reports_corruption_without_hiding_later_output(self):
        for raw in (b'{broken\n', b'[]\n', b'\xff\xfe\n', b'{' + b'x' * 100000 + b'\n'):
            with self.subTest(raw=raw[:20]):
                path = self.damaged(raw)
                cursor, parts = None, []
                for _ in range(100):
                    page = action_logs.page(path, cursor)
                    parts.append(page['output'])
                    self.assertLessEqual(len(page['output']), 32768)
                    if not page['more']:
                        break
                    self.assertNotEqual(cursor, page['cursor'])
                    cursor = page['cursor']
                else:
                    self.fail('Corruption must not trap the cursor')
                self.assertIn('damaged log record', ''.join(parts))
                self.assertIn('after\n', ''.join(parts))
                self.assertEqual(page['end']['exit_code'], 7)

    def test_valid_json_without_final_newline_is_still_pending(self):
        path = action_logs.start('testproj', REL, 'Check')
        with Path(path).open('ab') as stream:
            stream.write(json.dumps({'event': 'output', 'text': 'pending'}).encode())
        self.assertNotIn('pending', action_logs.output(path))
        self.assertNotIn('damaged', action_logs.output(path))
        with Path(path).open('ab') as stream:
            stream.write(b'\n')
        self.assertEqual(action_logs.output(path), 'pending')

    def test_next_write_separates_an_interrupted_record(self):
        path = self.damaged(b'{"event":"output","text":"interrupted')
        records = action_logs.read(path)
        self.assertIn('after\n', ''.join(r.get('text', '') for r in records))
        self.assertIn('damaged log record', ''.join(r.get('text', '') for r in records))
        self.assertEqual(records[-1]['exit_code'], 7)

    def test_damaged_trailing_record_cannot_replace_terminal_metadata(self):
        path = action_logs.start('testproj', REL, 'Check')
        action_logs.finish(path, 7)
        for raw in (b'{broken\n', json.dumps({'event': 'end', 'exit_code': 0,
                    'result': 'succeeded', 'message': 'x' * 100000}).encode()[:-1] + b'BROKEN\n'):
            with Path(path).open('ab') as stream:
                stream.write(raw)
            terminal = action_logs.metadata(path)[-1]
            self.assertEqual(terminal.get('exit_code'), 7)
            self.assertEqual(terminal.get('result'), 'failed')

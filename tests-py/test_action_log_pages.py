import json
from pathlib import Path
from unittest import mock

from bzztest import RoundCase, REL
from haivequeen import action_logs


class ActionLogPagesTests(RoundCase):
    def test_pages_reconstruct_output_and_cursor_skips_consumed_records(self):
        path = action_logs.start('testproj', REL, 'Build')
        expected = ''.join('line-%04d Grüße\n' % i for i in range(3000))
        for line in expected.splitlines(keepends=True):
            action_logs.append(path, line)
        action_logs.finish(path, 7)
        cursor, output, offsets = None, [], []
        while True:
            result = action_logs.page(path, cursor=cursor, limit=4096)
            self.assertLessEqual(len(result['output']), 4096)
            output.append(result['output'])
            offsets.append(result['cursor'][0])
            if not result['more']:
                break
            self.assertNotEqual(cursor, result['cursor'])
            cursor = result['cursor']
        self.assertEqual(''.join(output), expected)
        self.assertEqual(offsets, sorted(offsets))
        self.assertEqual(result['end']['exit_code'], 7)
        with mock.patch.object(action_logs, 'read', side_effect=AssertionError('full read forbidden')):
            self.assertEqual(action_logs.page(path, cursor=result['cursor'])['output'], '')

    def test_large_single_record_is_paginated_without_loss(self):
        path = action_logs.start('testproj', REL, 'Check')
        text = '🙂\\\n' * 3000
        action_logs.append(path, text)
        cursor, parts = None, []
        while True:
            result = action_logs.page(path, cursor=cursor, limit=1000)
            parts.append(result['output'])
            if not result['more']:
                break
            cursor = result['cursor']
        self.assertEqual(''.join(parts), text)

    def test_partial_record_does_not_advance_and_new_output_arrives(self):
        path = action_logs.start('testproj', REL, 'Build')
        result = action_logs.page(path)
        cursor = result['cursor']
        with Path(path).open('a', encoding='utf-8') as stream:
            stream.write('{"event":"output","text":"pending')
        partial = action_logs.page(path, cursor=cursor)
        self.assertEqual(partial['output'], '')
        self.assertEqual(partial['cursor'], cursor)
        with Path(path).open('a', encoding='utf-8') as stream:
            stream.write('"}\n')
        self.assertEqual(action_logs.page(path, cursor=cursor)['output'], 'pending')

    def test_large_record_reads_are_bounded_on_every_page(self):
        path = action_logs.start('testproj', REL, 'Build')
        text = '🙂\\\nGrüße' * 100000
        action_logs.append(path, text)
        original = Path.open
        sizes = []

        class Reader:
            def __init__(self, stream): self.stream = stream
            def __enter__(self): return self
            def __exit__(self, *args): self.stream.close()
            def __getattr__(self, name): return getattr(self.stream, name)
            def readline(self, size=-1):
                self.assert_bound(size)
                result = self.stream.readline(size); sizes.append(len(result)); return result
            def read(self, size=-1):
                self.assert_bound(size)
                result = self.stream.read(size); sizes.append(len(result)); return result
            @staticmethod
            def assert_bound(size):
                if not 0 <= size <= 262144:
                    raise AssertionError('unbounded log read')

        def opened(p, *args, **kwargs):
            stream = original(p, *args, **kwargs)
            return Reader(stream) if str(p) == path and args and args[0] == 'rb' else stream

        with mock.patch.object(Path, 'open', opened):
            first = action_logs.page(path)
            self.assertLess(sum(sizes), 262144)
            sizes.clear()
            second = action_logs.page(path, first['cursor'])
            self.assertLess(sum(sizes), 262144)
        self.assertTrue(text.startswith(first['output'] + second['output']))
        self.assertTrue(first['output'])
        self.assertTrue(second['output'])

    def test_large_record_can_finish_writing_between_polls(self):
        path = action_logs.start('testproj', REL, 'Build')
        expected = 'x' * 9000 + '🙂'
        encoded = (json.dumps({'event': 'output', 'text': expected}) + '\n').encode()
        with Path(path).open('ab') as stream:
            stream.write(encoded[:-2])  # closing quote exists, record is unfinished
        cursor, parts = None, []
        for _ in range(20):
            result = action_logs.page(path, cursor, limit=1024)
            parts.append(result['output'])
            cursor = result['cursor']
            if not result['more']:
                break
        else:
            self.fail('Incomplete record must not cause a busy polling loop')
        with Path(path).open('ab') as stream:
            stream.write(encoded[-2:])
        for _ in range(20):
            result = action_logs.page(path, cursor, limit=1024)
            parts.append(result['output'])
            cursor = result['cursor']
            if not result['more']:
                break
        self.assertEqual(''.join(parts), expected)

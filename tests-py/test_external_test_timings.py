import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch
from haivequeen import test_timings, web_log_pages


class ExternalTimingTests(unittest.TestCase):
    def test_full_file_incremental_append_replacement_and_truncation(self):
        with tempfile.TemporaryDirectory() as folder:
            path = Path(folder) / 'checks.txt'
            path.write_text('noise\n' * 100000 + '12s call tests/test_first.py::test_slow\n', encoding='utf-8')
            summary = test_timings.file_summary(path, 10)
            self.assertFalse(summary['complete'])
            for _ in range(8):
                summary = test_timings.file_summary(path, 10)
                if summary['complete']:
                    break
            self.assertEqual(summary['count'], 1)
            with patch.object(web_log_pages, 'file_page', side_effect=AssertionError('unchanged reread')):
                self.assertEqual(test_timings.file_summary(path, 10)['count'], 1)
            with path.open('a', encoding='utf-8') as out:
                out.write('22s call tests/test_second.py::test_slow\n')
            self.assertEqual(test_timings.file_summary(path, 10)['count'], 2)
            for _ in range(8):
                changed_threshold = test_timings.file_summary(path, 20)
                if changed_threshold['complete']:
                    break
            self.assertTrue(changed_threshold['complete'])
            self.assertEqual(changed_threshold['count'], 1)
            path.write_text('30s call tests/test_new.py::test_slow\n', encoding='utf-8')
            self.assertEqual(test_timings.file_summary(path, 10)['count'], 1)
            replacement = path.with_suffix('.new')
            replacement.write_text('40s call tests/test_replaced.py::test_slow\n', encoding='utf-8')
            replacement.replace(path)
            result = test_timings.file_summary(path, 10)
            self.assertIn('test_replaced', result['rows'][0]['name'])
            self.assertEqual(result['count'], 1)

    def test_utf16_partial_line_and_completed_tail(self):
        with tempfile.TemporaryDirectory() as folder:
            path = Path(folder) / 'check.log'
            path.write_text('12s call tests/test_a.py::', encoding='utf-16')
            self.assertEqual(test_timings.file_summary(path, 10)['count'], 0)
            with path.open('ab') as out:
                out.write('test_slow'.encode('utf-16-le'))
            self.assertEqual(test_timings.file_summary(path, 10, finished=True)['count'], 1)
            self.assertEqual(test_timings.file_summary(path, 10, finished=True)['count'], 1)

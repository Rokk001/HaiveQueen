import tempfile
import unittest
from pathlib import Path
from unittest import mock

from haivequeen import action_logs, web_step_logs


class LogSnapshotTests(unittest.TestCase):
    def test_completed_run_keeps_original_bytes_after_source_replaced(self):
        with tempfile.TemporaryDirectory() as folder:
            root = Path(folder)
            (root / 'out').mkdir()
            source = root / 'out/check.log'
            original = 'original result\r\n'.encode('utf-16')
            source.write_bytes(original)
            with mock.patch.object(action_logs.osplat, 'data_dir', return_value=root / 'data'):
                journal = action_logs.start('project', 'plan.md', 'Check')
            action_logs._write(journal, {'event': 'execution', 'cwd': str(root)})
            action_logs.append(journal, 'see out/check.log\n')
            action_logs.finish(journal, 1)
            source.write_text('later run', encoding='utf-8')
            with mock.patch.object(web_step_logs, 'reference_root', return_value=root):
                result = web_step_logs.referenced_logs(None, 'project', 'plan.md',
                    'see out/check.log', journal=journal)
            self.assertTrue(result[0]['archived'])
            self.assertEqual(result[0]['content'], 'original result\r\n')
            from haivequeen import log_snapshots
            self.assertEqual(log_snapshots.resolve(journal, 'out/check.log').read_bytes(), original)

    def test_missing_at_completion_never_substitutes_a_later_file(self):
        with tempfile.TemporaryDirectory() as folder:
            root = Path(folder)
            with mock.patch.object(action_logs.osplat, 'data_dir', return_value=root / 'data'):
                journal = action_logs.start('project', 'plan.md', 'Check')
            action_logs._write(journal, {'event': 'execution', 'cwd': str(root)})
            action_logs.append(journal, 'see out/missing.log\n')
            action_logs.finish(journal, 1)
            (root / 'out').mkdir()
            (root / 'out/missing.log').write_text('unrelated later evidence')
            with mock.patch.object(web_step_logs, 'reference_root', return_value=root):
                result = web_step_logs.referenced_logs(None, 'project', 'plan.md',
                    'see out/missing.log', journal=journal)
            self.assertIn('error', result[0])
            self.assertNotIn('content', result[0])

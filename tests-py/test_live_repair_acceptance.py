import importlib.util
from pathlib import Path
import tempfile
import unittest

SPEC = importlib.util.spec_from_file_location('live_acceptance', Path(__file__).resolve().parents[1] / 'scripts/verify_live_repair.py')
acceptance = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(acceptance)


class LiveAcceptanceGuards(unittest.TestCase):
    def test_requires_existing_installation(self):
        with tempfile.TemporaryDirectory() as folder:
            with self.assertRaises(ValueError):
                acceptance.validate_root(Path(folder))
            self.assertEqual(list(Path(folder).iterdir()), [])

    def test_rejects_profile_or_data_outside_test_root(self):
        with tempfile.TemporaryDirectory() as folder:
            root = Path(folder)
            for name in ('config', 'data', 'venv'):
                (root / name).mkdir()
            (root / 'config/config.toml').write_text('')
            (root / 'data/haivequeen.db').touch()
            self.assertEqual(acceptance.validate_root(root), root.resolve())
            with self.assertRaises(ValueError):
                acceptance.require_inside(root, root.parent)

    def test_completion_requires_real_runner_identity_and_terminal_result(self):
        row = {'status': 'done', 'pid': 123, 'processIdentity': 'birth', 'exit_code': 0}
        self.assertTrue(acceptance.finished_runner(row))
        for field in ('pid', 'processIdentity', 'exit_code'):
            changed = dict(row); changed.pop(field)
            self.assertFalse(acceptance.finished_runner(changed))
        self.assertFalse(acceptance.finished_runner(dict(row, status='running')))

import json
import unittest
from unittest.mock import patch

from haivequeen import loop, selfupdate


class SuccessorRoundReceiptTests(unittest.TestCase):
    def test_round_record_contains_process_identity_and_code_path(self):
        with patch.object(loop.runtime, 'kv_set') as save:
            loop.record_completed_round(object())
        receipt = json.loads(save.call_args.args[3])
        self.assertEqual(receipt['pid'], loop.os.getpid())
        self.assertEqual(receipt['identity'], loop.osplat.process_identity(receipt['pid']))
        self.assertEqual(receipt['code_path'], str(loop.TOOLS_DIR.resolve()))

    def test_only_current_successor_round_is_accepted(self):
        receipt = {'at': '2026-01-01T00:00:01', 'pid': 42,
                   'identity': 'current', 'code_path': str(loop.TOOLS_DIR)}

        def check(value):
            with patch.object(selfupdate.time, 'monotonic', side_effect=[0, 0, 2]), \
                 patch('haivequeen.runtime.kv_get', return_value=json.dumps(value)):
                return selfupdate.wait_successor_round(
                    object(), loop.TOOLS_DIR, '2026-01-01T00:00:00', lambda _: None,
                    0, 1, loop_pid=lambda: 42, stopped=lambda: False,
                    identity=lambda _: 'current')

        self.assertTrue(check(receipt))
        for change in ({'pid': 41}, {'identity': 'old'}, {'code_path': 'another-version'},
                       {'at': '2025-12-31T23:59:59'}):
            self.assertFalse(check({**receipt, **change}))


if __name__ == '__main__':
    unittest.main()

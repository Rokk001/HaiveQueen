"""Completion evidence must outlive the command's observer."""
import subprocess
import sys
from pathlib import Path
from types import SimpleNamespace
from unittest import mock

from bzztest import RoundCase, REL
from haivequeen import action_logs, integration, orchestrator


class CompletionLifetime(RoundCase):
    def test_recovery_uses_launcher_receipt_without_rebuilding(self):
        log = action_logs.start('testproj', REL, 'Build')
        action_logs.append(log, 'checks passed\n')
        Path(log).with_suffix('.exit').write_text('0', encoding='ascii')
        state = {'log_file': log, 'job': {'token': 'old-worker', 'step': 'Build',
                 'file': str(self.base / 'missing-result'), 'pid': None}}
        with mock.patch.object(orchestrator, 'save_step_log'):
            self.assertEqual(orchestrator._harvest(self.conn, SimpleNamespace(name='testproj'), REL, state),
                             (0, 'checks passed\n'))

    def test_receipt_does_not_release_a_still_running_launcher(self):
        log = action_logs.start('testproj', REL, 'Build')
        Path(log).with_suffix('.exit').write_text('0', encoding='ascii')
        state = {'log_file': log, 'job': {'token': 'old-worker', 'step': 'Build',
                 'file': str(self.base / 'missing-result'), 'pid': 123, 'identity': 'test'}}
        with mock.patch.object(orchestrator.agent_process, 'recorded_alive', return_value=True), \
             mock.patch.object(orchestrator, 'save_step_log') as save_log:
            self.assertIsNone(orchestrator._harvest(self.conn, SimpleNamespace(name='testproj'), REL, state))
            save_log.assert_not_called()
        self.assertFalse(any(row['event'] == 'end' for row in action_logs.read(log)))

    def test_observer_exception_preserves_late_launcher_result(self):
        log = action_logs.start('testproj', REL, 'Build')
        captured = []
        original = subprocess.Popen

        def launch(*args, **kwargs):
            proc = original(*args, **kwargs)
            captured.append((proc, Path(args[0][-1])))
            return proc

        script = self.base / 'late.py'
        script.write_text('import time, sys\ntime.sleep(.3)\nsys.exit(7)\n')
        with mock.patch.object(integration.subprocess, 'Popen', side_effect=launch), \
             mock.patch.object(integration.proctree, 'tree_cpu_seconds', side_effect=RuntimeError('observer failed')):
            with self.assertRaisesRegex(RuntimeError, 'observer failed'):
                integration._watch_compile('"%s" "%s"' % (sys.executable, script),
                                           str(self.base), 30, 30, .01, log_file=log)
        proc, completion = captured[0]
        proc.wait(timeout=15)
        self.assertTrue(completion.exists(), 'Observer cleanup must not erase a running launcher destination')
        self.assertEqual(completion.read_text(encoding='ascii'), '7')
        self.assertEqual(completion, Path(log).with_suffix('.exit'))

    def test_completed_result_is_retained_next_to_action_log(self):
        log = action_logs.start('testproj', REL, 'Build')
        code, _ = integration._watch_compile('"%s" -c "raise SystemExit(3)"' % sys.executable,
                                             str(self.base), 30, 30, .01, log_file=log)
        self.assertEqual(code, 3)
        self.assertEqual(Path(log).with_suffix('.exit').read_text(encoding='ascii'), '3')

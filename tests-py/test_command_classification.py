"""Distinguish executed check scripts from filenames read or searched."""
import unittest

import bzztest  # noqa: F401
from haivequeen import agent_runner as ar


# Check the execution boundary without running any shell command.
class CommandClassificationTest(unittest.TestCase):
    """Only actual check invocations create check milestones."""

    # A read followed by a search must not manufacture a check milestone.
    def test_read_and_search_are_not_checks(self):
        """The observed read/search pattern contains no script execution."""
        commands = [
            'Get-Content -Raw tests-py/test_isolation_check.py; rg "cleanup" haivequeen/loop.py',
            'rg check-leaks.py haivequeen',
            'echo python check-leaks.py',
            'Write-Output "python check-leaks.py; python check-other.py"',
            'Get-Content scripts/compile-check-changed.ps1',
            'python report.py check-leaks.py',
            'python -c "print(\'check-leaks.py\')"',
            'pwsh -Command "Get-Content check-leaks.ps1"',
            'pwsh -Command Get-Content "C:\\Some Folder\\check-data.py"',
            'pwsh -Command python -c "print(\'check-data.py\')"',
            'python --help scripts/check_data.py',
            'python --version scripts/check_data.py',
            'python -V scripts/check_data.py',
            '# note; python scripts/check_data.py',
            'echo done # note; python scripts/check_data.py',
            'pwsh -Command "# note; python scripts/check_data.py"',
            '"C:\\Program Files\\PowerShell\\7\\pwsh.exe" -Command "Get-Content -Raw tests-py/test_isolation_check.py; rg cleanup haivequeen/loop.py"',
        ]
        for command in commands:
            with self.subTest(command=command):
                self.assertIsNone(ar.classify_cmd(command))

    # Retain check milestones for executable scripts and interpreter flags.
    def test_executed_checks_keep_their_names(self):
        """Quoted paths, compound commands and interpreter flags remain supported."""
        commands = [
            ('python check-leaks.py', 'check-leaks.py'),
            ('python -I -B "scripts/check_data.py"', 'check_data.py'),
            ('python -x -W ignore scripts/check_data.py', 'check_data.py'),
            ('python --check-hash-based-pycs always scripts/check_data.py', 'check_data.py'),
            ('py -3.14 -X utf8 scripts/check_data.py', 'check_data.py'),
            ('& "C:\\Python314\\python.exe" -B "C:\\Some Folder\\check-data.py"', 'check-data.py'),
            ('pwsh -NoProfile -ExecutionPolicy Bypass -File "scripts/check-data.ps1"', 'check-data.ps1'),
            ('& "scripts/check-data.ps1"', 'check-data.ps1'),
            ('./scripts/check-data.py', 'check-data.py'),
            ('Get-Content README.md; python -I scripts/check_data.py', 'check_data.py'),
            ('echo ready && python scripts/check_data.py', 'check_data.py'),
            ('"C:\\Program Files\\PowerShell\\7\\pwsh.exe" -Command "python scripts/check_data.py; Get-Content README.md"', 'check_data.py'),
            ('pwsh -c "python -I scripts/check_data.py"', 'check_data.py'),
            ('pwsh -Command python scripts/check_data.py', 'check_data.py'),
            ('pwsh -c python "C:\\Some Folder\\check-data.py"', 'check-data.py'),
            ('pwsh -Command "& \'C:\\Some Folder\\check-data.ps1\'"', 'check-data.ps1'),
            ('python scripts/check_data.py --help', 'check_data.py'),
            ('# note; python check-fake.py\npython scripts/check_data.py', 'check_data.py'),
            ('echo "# note; python check-fake.py"; python scripts/check_data.py', 'check_data.py'),
        ]
        for command, name in commands:
            with self.subTest(command=command):
                self.assertEqual(ar.classify_cmd(command), {'kind': 'check', 'label': 'Check ' + name})

    # The special compile milestone also requires an actual script invocation.
    def test_compile_script_execution_keeps_label(self):
        """A script configured as the compile check keeps the label however it is run; its file name alone says nothing."""
        for command in ('pwsh scripts/compile-check-changed.ps1', '& "scripts/compile-check-changed.ps1"',
                        '"C:\\Program Files\\PowerShell\\7\\pwsh.exe" -Command "& scripts/compile-check-changed.ps1"'):
            with self.subTest(command=command):
                self.assertEqual(ar.classify_cmd(command, compile_check='scripts/compile-check-changed.ps1'), {'kind': 'check', 'label': 'Compile test'})
                self.assertEqual(ar.classify_cmd(command), {'kind': 'check', 'label': 'Check compile-check-changed.ps1'})


if __name__ == '__main__':
    unittest.main()

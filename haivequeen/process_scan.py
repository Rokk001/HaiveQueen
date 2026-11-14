"""Conservative process table inspection for version directory recovery."""
import os
import sys
from pathlib import Path


def scan(directory):
    """Return absence only when the platform inspection can prove it."""
    if sys.platform.startswith('linux'):
        return scan_linux(directory)
    return None


def _contains(directory, value):
    # A command line can contain an option followed by an absolute path.
    # Substring matches intentionally err on the side of retaining a version.
    return str(directory) in value


def scan_linux(directory, proc=Path('/proc')):
    """Return True if in use, False if absent, None if inspection is uncertain."""
    directory = Path(directory).resolve()
    try:
        processes = list(proc.iterdir())
    except OSError:
        return None
    uncertain = False
    for process in processes:
        if not process.name.isdigit():
            continue
        try:
            stat = (process / 'stat').read_text(encoding='utf-8')
            # Zombie processes cannot use a version folder.
            if stat[stat.rfind(')') + 2:][:1] == 'Z':
                continue
            exe = os.readlink(process / 'exe')
            cwd = os.readlink(process / 'cwd')
            cmd = (process / 'cmdline').read_bytes().decode('utf-8', 'surrogateescape')
            if any(_contains(directory, v) for v in (exe, cwd, cmd)):
                return True
        except (OSError, ValueError):
            # Vanishing processes are harmless, but inaccessible live ones are
            # unknown. Recheck existence because proc entries race with exit.
            if process.exists():
                uncertain = True
    return None if uncertain else False

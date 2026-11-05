"""Visible results for start and stop shortcuts; ordinary CLI output stays JSON."""
import sys

from .i18n import tr


# Describe the start or stop result without suggesting agents were terminated.
def result_text(command, result, language=None):
    """Return desktop feedback in `language` (default: the machine language), with English fallback."""
    if not result.get("ok"):
        return tr("Error: %s", result.get("error") or tr("HQ could not execute the command.", language=language), language=language)
    if command == "stop" or result.get("stopping"):
        count = int(result.get("running_agents") or 0)
        if count:
            return tr("Stop requested. HQ is waiting for %d running agents; they continue working.", count, language=language)
        return tr("Stop requested. HQ is stopping the scheduler; no new agents will start.", language=language)
    if result.get("starting"):
        return tr("HQ is already starting.", language=language)
    if result.get("running"):
        return tr("HQ is already running. The dashboard is open.", language=language)
    return tr("HQ has started.", language=language)


# Keep shortcut feedback visible after the command's temporary console closes.
def show_result(command, result):
    """Show a Windows result dialog, or print its text on other systems."""
    text = result_text(command, result)
    if sys.platform == "win32":
        import ctypes
        ctypes.windll.user32.MessageBoxW(None, text, "HaiveQueen", 0x10 if not result.get("ok") else 0x40)
    else:
        print(text, file=sys.stderr)

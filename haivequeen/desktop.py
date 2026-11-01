"""Visible results for start and stop shortcuts; ordinary CLI output stays JSON."""
import sys


# Describe the start or stop result without suggesting agents were terminated.
def result_text(command, result):
    """Return the German desktop feedback for a command's JSON result."""
    if not result.get("ok"):
        return "Fehler: " + str(result.get("error") or "HQ konnte den Befehl nicht ausführen.")
    if command == "stop" or result.get("stopping"):
        count = int(result.get("running_agents") or 0)
        if count:
            return "Stopp angefordert. HQ wartet auf %d laufende Agenten; sie arbeiten ununterbrochen weiter." % count
        return "Stopp angefordert. HQ beendet die Steuerung; es werden keine neuen Agenten gestartet."
    if result.get("starting"):
        return "HQ wird bereits gestartet."
    if result.get("running"):
        return "HQ läuft bereits. Das Dashboard ist geöffnet."
    return "HQ wurde gestartet."


# Keep shortcut feedback visible after the command's temporary console closes.
def show_result(command, result):
    """Show a Windows result dialog, or print its text on other systems."""
    text = result_text(command, result)
    if sys.platform == "win32":
        import ctypes
        ctypes.windll.user32.MessageBoxW(None, text, "HaiveQueen", 0x10 if not result.get("ok") else 0x40)
    else:
        print(text, file=sys.stderr)

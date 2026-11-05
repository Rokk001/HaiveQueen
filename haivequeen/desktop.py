"""Visible results for start and stop shortcuts; ordinary CLI output stays JSON."""
import sys


# Describe the start or stop result without suggesting agents were terminated.
def result_text(command, result, language=None):
    """Return desktop feedback in the configured language, with English fallback."""
    if language is None:
        from . import config
        language = config.load_config().get("language", "English")
    if str(language).strip().lower() not in ("german", "deutsch", "de", "de-de"):
        if not result.get("ok"):
            return "Error: " + str(result.get("error") or "HQ could not execute the command.")
        if command == "stop" or result.get("stopping"):
            count = int(result.get("running_agents") or 0)
            if count:
                return "Stop requested. HQ is waiting for %d running agents; they continue working." % count
            return "Stop requested. HQ is stopping the scheduler; no new agents will start."
        if result.get("starting"):
            return "HQ is already starting."
        if result.get("running"):
            return "HQ is already running. The dashboard is open."
        return "HQ has started."
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

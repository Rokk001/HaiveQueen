"""Texts of the GUI, the desktop messages and the owner texts the program writes itself, in English and German.

The English text is the key: `tr("Close")` gives it back unchanged for English and for every language without a catalog (fallback). GUI and desktop
messages use the language of the machine settings (General page of config.toml); owner texts of an Apiary pass that Apiary's language. Fixed protocol
words (plan statuses, the words in plan and state files) and the product names Apiary, Hive, Queen and Bee are never translated.
"""
import json
import re

from . import config, platform as osplat

_cache = {"key": None, "language": "English"}


# Tell whether a language setting means German.
def is_german_language(language) -> bool:
    """True for German, Deutsch, de and regional codes such as de-CH or de_AT, in any case."""
    code = str(language or "").strip().lower().replace("_", "-")
    return code in ("german", "deutsch", "de") or code.startswith("de-")


# Read the language of the machine settings.
def machine_language() -> str:
    """The `language` value of config.toml, English without one or with an unreadable file; read again only when the file changed."""
    path = osplat.config_dir() / "config.toml"
    try:
        stat = path.stat()
        key = (str(path), stat.st_mtime_ns, stat.st_size)
    except OSError:
        return "English"
    if key != _cache["key"]:
        try:
            language = str(config.load_config().get("language") or "English")
        except Exception:   # a broken settings file must not stop the window; the settings page shows the error
            language = "English"
        _cache.update(key=key, language=language)
    return _cache["language"]


# Tell whether the machine language is German.
def is_german() -> bool:
    """True when the GUI and the desktop messages are German."""
    return is_german_language(machine_language())


# Mark a text in a table as translatable; the table's user calls tr.
def N_(text):
    """Return `text` unchanged; marks it for the catalog check."""
    return text


# Translate one text.
def tr(text, *args, language=None) -> str:
    """The text in `language` (default: the machine language), with `args` filled into its format fields; other languages and unknown texts stay English."""
    german = is_german_language(language) if language is not None else is_german()
    out = GERMAN.get(text, text) if german else text
    return out % args if args else out


# Write the decimal point of a formatted number in the language.
def decimal(text, language=None) -> str:
    """Formatted numbers of `text` with a decimal comma in German; other languages keep the point. Only a point between two digits changes."""
    german = is_german_language(language) if language is not None else is_german()
    return re.sub(r"(?<=\d)\.(?=\d)", ",", text) if german else text


# Translate a log line the program wrote.
def log_text(text, language=None) -> str:
    """A stored program log line in the language; a line of an agent, or any other text, stays as written."""
    german = is_german_language(language) if language is not None else is_german()
    return _german_line(text) if german else text


# Translate one line of the live text of an agent.
def live_text(colour, text, language=None) -> str:
    """A program line of the live view ("<time> <text>") in the language. White lines are the words of the agent and stay as written; the time may be one or two words."""
    german = is_german_language(language) if language is not None else is_german()
    if not german or colour == "White":
        return text
    parts = text.split(" ")
    for count in (1, 2):
        head, tail = " ".join(parts[:count]), " ".join(parts[count:])
        changed = _german_line(tail) if tail else tail
        if changed != tail:
            return head + " " + changed
    return text


def _german_line(text):
    """The German form of the first pattern that fits; a pattern may end before the end of the line, the rest then stays as written."""
    for pattern, replacement in _LOG_LINES:
        match = pattern.match(text)
        if match:
            return (replacement(match) if callable(replacement) else match.expand(replacement)) + text[match.end():]
    return text


def _round_done(match):
    """The round line with each "<plan> not started: <reason>" in German."""
    return "Runde fertig: %s Queen(s) gestartet%s" % (match.group(1), "".join("; " + _german_line(part) for part in re.split(r"; (?=[^;]* not started: )", match.group(2))[1:]))


def _worktree_reason(reason):
    """Why a worktree stays, in German on one line; the text of Git or the system and any path behind a known start stay as written."""
    reason = " ".join(str(reason).split())
    for english, german in _WORKTREE_REASONS:
        if reason.startswith(english):
            return german + reason[len(english):]
    return reason


def _worktree_cleanup(match):
    """The result of a cleanup in German words instead of its JSON; a text that is not such a result stays as written behind the translated head."""
    head = "Worktree-Bereinigung: "
    try:
        result = json.loads(match.group(1))
        groups = (("entfernt", [str(path) for path in result["removed"]]),
                  ("behalten", ["%s (%s)" % (row["path"], _worktree_reason(row["reason"])) for row in result["kept"]]),
                  ("nicht entfernbar", ["%s (%s)" % (row["path"], _worktree_reason(row["reason"])) for row in result["notRemovable"]]))
    except (ValueError, KeyError, TypeError):
        return head + match.group(1)
    return head + ("; ".join("%s: %s" % (label, ", ".join(items)) for label, items in groups if items) or "nichts zu tun")


def _with_source(german):
    """A run state line with the source of the change when it has one."""
    return lambda match: german + (" (Quelle: %s)" % match.group(1) if match.group(1) else "")


def _live_label(text):
    """The label of a tool line of the live view in German."""
    return "Kompiliertest" if text == "Compile test" else _german_line(text)


_BUILD_WHY = {"first": "erster Build", "ram": "durch RAM begrenzt", "cores": "durch Kerne begrenzt"}
_LIVE_LABEL = r"(Commit(?:: .*?)?|Push|Compile test|Check \S+|changed: .*?|ready line registered)"


# Why a worktree cleanup kept a worktree or could not remove it: the start of the English text and its German form.
_WORKTREE_REASONS = (
    ("protected worktree", "geschützter Worktree"),
    ("locked or missing", "gesperrt oder fehlt"),
    ("branch has an open queue row", "Branch hat eine offene Zeile in der Warteschlange"),
    ("branch has no current integrated queue row", "Branch hat keine aktuelle integrierte Zeile in der Warteschlange"),
    ("integrated queue tip is invalid", "Commit der integrierten Zeile in der Warteschlange ist ungültig"),
    ("current branch does not match the worktree inventory", "aktueller Branch passt nicht zur Worktree-Liste"),
    ("HEAD does not match the integrated queue tip", "HEAD passt nicht zum Commit der integrierten Zeile in der Warteschlange"),
    ("used by active agent", "von einem aktiven Agenten verwendet"),
    ("used by assigned Bee", "von einer zugewiesenen Bee verwendet"),
    ("git status failed", "git status fehlgeschlagen"),
    ("uncommitted or untracked changes", "nicht committete oder nicht verfolgte Änderungen"),
    ("ignored file: ", "ignorierte Datei: "),
    ("HEAD is not in the integration branch", "HEAD ist nicht im Integrations-Branch"),
    ("process working directory or unknown process state", "Arbeitsverzeichnis eines Prozesses oder unbekannter Prozesszustand"),
    ("fetch failed: ", "fetch fehlgeschlagen: "),
    ("cannot resolve fetched integration branch", "abgerufener Integrations-Branch lässt sich nicht auflösen"),
    ("integration branch changed during fetch", "Integrations-Branch hat sich während des Abrufs geändert"),
    ("link target changed: ", "Link-Ziel geändert: "),
    ("link target changed; links retained at ", "Link-Ziel geändert; Links bleiben erhalten unter "),
    ("link target is missing", "Link-Ziel fehlt"),
    ("link target is inside the worktree", "Link-Ziel liegt im Worktree"),
    ("nested link in target", "verschachtelter Link im Ziel"),
    ("non-regular target file", "keine reguläre Datei im Ziel"),
    ("new link appeared during cleanup", "neuer Link während der Bereinigung aufgetaucht"),
    ("tracked link cannot be safely moved: ", "versionierter Link lässt sich nicht sicher verschieben: "),
    ("unsupported reparse point: ", "nicht unterstützter Reparse-Point: "),
    ("process memory is inaccessible", "Prozessspeicher ist nicht zugänglich"),
    ("process information is inaccessible", "Prozessinformationen sind nicht zugänglich"),
    ("worktree cleanup is busy", "Worktree-Bereinigung läuft bereits"),
)


_LOG_LINES = [(re.compile(pattern), replacement) for pattern, replacement in (
    # ---- worktree cleanup, before the general patterns: its JSON holds free text
    (r"^Worktree cleanup failed: (.*)$", lambda m: "Worktree-Bereinigung fehlgeschlagen: " + _worktree_reason(m.group(1))),
    (r"^Worktree cleanup: (.*)$", _worktree_cleanup),
    # ---- rounds and the loop
    (r"^round done: (\d+) Queen\(s\) started((?:; .*)?)$", _round_done),
    (r"^round skipped \((.*)\)$", r"Runde übersprungen (\1)"),
    (r"^(.*) not started: (.*)$", lambda m: "%s nicht gestartet: %s" % (m.group(1), _german_line(m.group(2)))),
    (r"^ROUND ERROR ", "RUNDENFEHLER "),
    (r"^Round error: ", "Rundenfehler: "),
    (r"^RETENTION ERROR ", "AUFBEWAHRUNGSFEHLER "),
    (r"^EXPORT ERROR ", "EXPORTFEHLER "),
    (r"^TECHNICAL NOTE: (.*?): (.*), marked failed$", lambda m: "TECHNISCHER HINWEIS: %s: %s, als fehlgeschlagen markiert" % (m.group(1), _german_line(m.group(2)))),
    (r"^TECHNICAL NOTE: ", "TECHNISCHER HINWEIS: "),
    (r"^loop started \(roundMinutes=(.*)\)$", r"Steuerung gestartet (roundMinutes=\1)"),
    (r"^Beekeeper started \(round every (.*) minutes\)$", r"Beekeeper gestartet (Runde alle \1 Minuten)"),
    (r"^STOP found, loop ending$", "STOP gefunden, Steuerung endet"),
    (r"^STOP file found: no new agents, waiting for running agents$", "STOP-Datei gefunden: keine neuen Agenten, warte auf laufende Agenten"),
    (r"^loop ending$", "Steuerung endet"),
    (r"^Loop ending$", "Steuerung endet"),
    (r"^loop ended$", "Steuerung beendet"),
    (r"^Loop ended: all agents are done$", "Steuerung beendet: alle Agenten sind fertig"),
    (r"^restart: re-attached (\d+) running agent\(s\), (\d+) gone$", r"Neustart: \1 laufende(r) Agent(en) wieder verbunden, \2 verschwunden"),
    (r"^TOML configuration reloaded; running agents retained\.$", "TOML-Einstellungen neu geladen; laufende Agenten bleiben."),
    (r"^TOML configuration reload failed \((.*)\); keeping last valid settings\.$", r"TOML-Einstellungen nicht neu geladen (\1); die letzten gültigen bleiben."),
    (r"^paused: no new agents are started(?: \(source: (.*)\))?$", _with_source("pausiert: es werden keine neuen Agenten gestartet")),
    (r"^resumed(?: \(source: (.*)\))?$", _with_source("fortgesetzt")),
    (r"^start (\S+) \((.*), (.*)\)$", r"Start \1 (\2, \3)"),
    (r"^(\S+) started \((.*)\)$", r"\1 gestartet (\2)"),
    (r"^plan (.+?): READY -> IN_PROGRESS$", r"Plan \1: READY -> IN_PROGRESS"),
    (r"^plan (.+?): status change failed: ", r"Plan \1: Statuswechsel fehlgeschlagen: "),
    (r"^cannot prepare (plan|Queen) worktree: ", r"Worktree (\1) kann nicht vorbereitet werden: "),
    (r"^event hook ok: (\d+) event\(s\)$", r"Event-Hook ok: \1 Ereignis(se)"),
    (r"^event hook failed \(exit (.*?)\): ", r"Event-Hook fehlgeschlagen (Exit \1): "),
    (r"^event hook error: ", "Event-Hook-Fehler: "),
    (r"^Event command failed \(again next round\): ", "Event-Befehl fehlgeschlagen (erneut nächste Runde): "),
    (r"^Event command error: ", "Event-Befehl-Fehler: "),
    (r"^fault check failed: ", "Fehlerprüfung fehlgeschlagen: "),
    (r"^Fault question (\S+) remains open \((.*?)\): ", r"Fehlerfrage \1 bleibt offen (\2): "),
    (r"^views not written: ", "Ansichten nicht geschrieben: "),
    (r"^database exported to ", "Datenbank exportiert nach "),
    (r"^inbox file (\S+) taken over: (\d+) answer\(s\), (\d+) decision\(s\), (\d+) queue order\(s\)$", r"Inbox-Datei \1 übernommen: \2 Antwort(en), \3 Entscheidung(en), \4 Warteschlangen-Auftrag/-Aufträge"),
    (r"^inbox queue order (\S+): branch (\S+), rows (.*) \((.*)\)$", r"Inbox-Warteschlangen-Auftrag \1: Branch \2, Zeilen \3 (\4)"),
    (r"^warning inbox file (\S+) rejected: ", r"Warnung: Inbox-Datei \1 abgelehnt: "),
    (r"^question (\d+) waits for fault checking$", r"Frage \1 wartet auf die Fehlerprüfung"),
    (r"^question (\d+) waits for triage$", r"Frage \1 wartet auf die Vorprüfung"),
    (r"^Provider (\S+) needs its login in the HaiveQueen profile \(once\)\. Run `(.*)` with the environment (.*)\. Its packages wait until then, all others go on\.$", r"Anbieter \1 braucht einmalig seinen Login im HaiveQueen-Profil. Führe `\2` mit der Umgebung \3 aus. Seine Pakete warten bis dahin, alle anderen laufen weiter."),
    (r"^Provider (\S+) cannot be kept to the HaiveQueen profile\.$", r"Anbieter \1 lässt sich nicht auf das HaiveQueen-Profil beschränken."),
    (r"^self-update ok: (.*)$", lambda m: "Selbst-Update ok: " + _german_line(m.group(1))),
    (r"^self-update (\S+): (.*)$", lambda m: "Selbst-Update %s: %s" % (m.group(1), _german_line(m.group(2)))),
    (r"^updated to (\S+)$", r"aktualisiert auf \1"),
    (r"^already current at (\S+)$", r"bereits aktuell auf \1"),
    (r"^(.*); rolled back to (\S+)$", r"\1; zurückgesetzt auf \2"),
    # ---- why a Hive got no Queen
    (r"^quota used up$", "Kontingent aufgebraucht"),
    (r"^not available$", "nicht verfügbar"),
    (r"^provider unknown$", "Anbieter unbekannt"),
    (r"^no free slot \(needs (\d+), (\d+) free\)$", r"kein freier Slot (braucht \1, \2 frei)"),
    (r"^needs (\d+) slots \(Queen and Bee process\) but has (\d+)$", r"braucht \1 Slots (Queen und Bee-Prozess), hat aber \2"),
    (r"^usage unknown, no recent reading \((.*)\)$", r"Verbrauch unbekannt, keine aktuelle Messung (\1)"),
    (r'^Hive state "(.*?)"(?: \(questions (.*)\))?$', lambda m: "Hive-Zustand %s%s" % (m.group(1), " (Fragen %s)" % m.group(2) if m.group(2) else "")),
    (r"^waiting for the build: (.*)$", lambda m: "Wartet auf den Build: " + _german_line(m.group(1))),
    (r"^waiting for provider (\S+): (.*)$", lambda m: "Wartet auf Anbieter %s: %s" % (m.group(1), _german_line(m.group(2)))),
    (r"^waiting for the integration place$", "Wartet auf den Integrationsplatz"),
    (r"^handing off, Bee processes are still running$", "Übergabe, Bee-Prozesse laufen noch"),
    (r"^retry after an interrupted Queen is not due yet$", "Neuer Versuch nach unterbrochener Queen ist noch nicht fällig"),
    (r"^Hive limit (\d+) reached$", r"Hive-Limit \1 erreicht"),
    (r"^free RAM (\S+) GB is below the minimum free RAM before a build of (\S+) GB$", lambda m: decimal("freier RAM %s GB liegt unter dem Mindestwert vor einem Build von %s GB" % m.groups(), "German")),
    (r"^free RAM (\S+) GB is below the minimum (\S+) GB$", lambda m: decimal("freier RAM %s GB liegt unter dem Mindestwert %s GB" % m.groups(), "German")),
    (r"^no provider with a free slot and a free limit offers class (\S+)$", r"kein Anbieter mit freiem Slot und freiem Limit bietet Klasse \1"),
    (r"^no provider with a free slot offers class (\S+)$", r"kein Anbieter mit freiem Slot bietet Klasse \1"),
    (r"^its branch (.+) is held by work outside the Beekeeper$", r"sein Branch \1 wird von Arbeit außerhalb des Beekeepers gehalten"),
    (r"^the Hive is already claimed$", "der Hive ist bereits reserviert"),
    (r"^worktree for branch (.+?) could not be prepared \((.*)\)$", r"Worktree für Branch \1 konnte nicht vorbereitet werden (\2)"),
    (r"^no branch or worktree named in the plan or its state file$", "kein Branch oder Worktree in Plan oder State-Datei genannt"),
    # ---- why a Bee did not start
    (r"^Bee start refused for package (\S+): (.*)$", lambda m: "Bee-Start für Paket %s abgelehnt: %s" % (m.group(1), _german_line(m.group(2)))),
    (r"^dependency (\S+) of package (\S+) not in plan$", r"Abhängigkeit \1 von Paket \2 ist nicht im Plan"),
    (r"^dependency (\S+) of package (\S+) not done$", r"Abhängigkeit \1 von Paket \2 ist nicht fertig"),
    (r"^the Hive may not start a Bee now \(Hive state (\S+), context limit or HANDOFF reached\)$", r"der Hive darf jetzt keine Bee starten (Hive-Zustand \1, Kontextgrenze oder HANDOFF erreicht)"),
    (r"^no running Queen of this plan$", "keine laufende Queen dieses Plans"),
    (r"^the name (\S+) is already held by a running Queen or Bee; use the free name (\S+)$", r"der Name \1 gehört bereits einer laufenden Queen oder Bee; nutze den freien Namen \2"),
    (r"^the Queen already has (\S+) Bees running$", r"die Queen hat bereits \1 laufende Bees"),
    (r"^the plan is not cut yet: write its packages into the header and run bzz plan check first$", "der Plan ist noch nicht aufgeteilt: schreibe zuerst seine Pakete in den Kopf und führe bzz plan check aus"),
    (r"^the package is not in the plan$", "das Paket ist nicht im Plan"),
    (r"^no configured provider offers the required class or the capability (.*) \(question filed\)$", r"kein eingerichteter Anbieter bietet die nötige Klasse oder die Fähigkeit \1 (Frage gestellt)"),
    (r"^no configured provider offers the required class \(question filed\)$", "kein eingerichteter Anbieter bietet die nötige Klasse (Frage gestellt)"),
    (r"^the package failed (\d+) times at the Queen class (\S+) and no higher class is offered$", r"das Paket scheiterte \1-mal auf der Queen-Klasse \2 und keine höhere Klasse wird angeboten"),
    (r"^the package failed (\d+) times and no higher class is offered$", r"das Paket scheiterte \1-mal und keine höhere Klasse wird angeboten"),
    (r"^class (\S+) was requested but the system computes class (\S+) \(plan class (\S+), no stored escalation\)$", r"Klasse \1 wurde angefordert, das System berechnet aber Klasse \2 (Planklasse \3, keine gespeicherte Eskalation)"),
    (r"^the assigned provider (\S+) is unknown, excluded, at its limit or has no free slot$", r"der zugewiesene Anbieter \1 ist unbekannt, ausgeschlossen, am Limit oder hat keinen freien Slot"),
    (r"^the worktree (.+) is not a valid separate worktree with a branch of this repository$", r"der Worktree \1 ist kein gültiger eigener Worktree mit einem Branch dieses Repositorys"),
    (r"^a Bee process may not start now \(starts paused or stopped, too little free RAM, or no free slot of provider (\S+)\)$", r"ein Bee-Prozess darf jetzt nicht starten (Starts pausiert oder gestoppt, zu wenig freier RAM oder kein freier Slot von Anbieter \1)"),
    (r"^no valid separate worktree for the Bee was given$", "für die Bee wurde kein gültiger eigener Worktree angegeben"),
    (r"^the agent runner could not start the Bee process$", "der Agent-Runner konnte den Bee-Prozess nicht starten"),
    # ---- agent runs: event lines and the last line of an agent row
    (r"^warning (\S+) could not update its row after (\d+) tries, set to failed: ", r"Warnung: \1 konnte seine Zeile nach \2 Versuchen nicht aktualisieren, auf fehlgeschlagen gesetzt: "),
    (r"^warning (\S+) exit code (\S+) after a finished task \(hook/stderr\), kept as done$", r"Warnung: \1 Exit-Code \2 nach fertiger Aufgabe (Hook/stderr), bleibt fertig"),
    (r"^build (\S+): starting with (\d+) jobs \((\w+), free RAM (\d+) MB\)$", lambda m: "Build %s: startet mit %s Jobs (%s, freier RAM %s MB)" % (m.group(1), m.group(2), _BUILD_WHY.get(m.group(3), m.group(3)), m.group(4))),
    (r"^build (\S+): jobs=(\d+), duration=([\d:]+), no build process seen, nothing stored$", r"Build \1: Jobs=\2, Dauer=\3, kein Build-Prozess gesehen, nichts gespeichert"),
    (r"^build (\S+): jobs=(\d+), duration=([\d:]+), build process RAM peak=(\d+) MB, use per job=(\d+) MB \(median (\d+) MB\)$", r"Build \1: Jobs=\2, Dauer=\3, RAM-Spitze der Build-Prozesse=\4 MB, Nutzung pro Job=\5 MB (Median \6 MB)"),
    (r"^(\S+) stalled: no output for (\d+) s \(still running, not ended\)$", r"\1 hängt: keine Ausgabe seit \2 s (läuft noch, nicht beendet)"),
    (r"^(\S+) prints again$", r"\1 gibt wieder aus"),
    (r"^process gone after orchestrator restart$", "Prozess nach Neustart der Steuerung verschwunden"),
    (r"^runner process vanished \(ended by executor or crashed\)$", "Runner-Prozess verschwunden (von außen beendet oder abgestürzt)"),
    (r"^pane never started \(no runner process\)$", "Fenster nie gestartet (kein Runner-Prozess)"),
    (r"^queued$", "in der Warteschlange"),
    (r"^starting$", "startet"),
    (r"^runner error: ", "Runner-Fehler: "),
    (r"^error: ", "Fehler: "),
    (r"^tool: ", "Werkzeug: "),
    (r"^cmd: ", "Befehl: "),
    (r"^no result event, exit (\S*)", r"kein Ergebnis-Ereignis, Exit \1"),
    (r"^agent row error after (\d+) tries \(wanted status '(.*?)'\): ", r"Fehler in der Agentenzeile nach \1 Versuchen (gewünschter Status '\2'): "),
    # ---- program lines of the live view
    (r"^ready line registered$", "ready-Zeile eingetragen"),
    (r"^Stalled: no output for (\d+) s\. The agent is still running\.$", r"Hängt: keine Ausgabe seit \1 s. Der Agent läuft noch."),
    (r"^Note: exit code (\S+) although the agent is done \(see stderr\.txt\)$", r"Hinweis: Exit-Code \1, obwohl der Agent fertig ist (siehe stderr.txt)"),
    (r"^Final message: ", "Abschlussnachricht: "),
    (r"^End: (\S+) \(exit (\S+)\), total input (\S+) / output (\S+)\.$", lambda m: decimal("Ende: %s (Exit %s), gesamt Eingabe %s / Ausgabe %s." % (GERMAN.get(m.group(1), m.group(1)), m.group(2), m.group(3), m.group(4)), "German")),
    ("^" + _LIVE_LABEL + r" - (Exit \S+|OK|Error)(?=: |$)", lambda m: "%s - %s" % (_live_label(m.group(1)), "Fehler" if m.group(2) == "Error" else m.group(2))),
    (r"^changed: ", "geändert: "),
    (r"^Compile test$", "Kompiliertest"),
    (r"^Check (\S+)$", r"Prüfung \1"),
)]


GERMAN = {
    "Coding (including tests, docs and review)": "Programmierung (einschließlich Tests, Dokumentation und Prüfung)",
    "Queen (plan cutting and coordination)": "Queen (Planaufteilung und Koordination)",
    "Build (with this machine’s tools)": "Build (mit den Werkzeugen dieses Rechners)",
    "Vision (understand images and screenshots)": "Bildverständnis (Bilder und Screenshots verstehen)",
    "Confidential data (local models only)": "Vertrauliche Daten (nur lokale Modelle)",
    "Coding": "Programmierung", "Queen": "Queen", "Build": "Build", "Deploy": "Deployment",
    "Image generation": "Bilderzeugung", "3D modelling": "3D-Modellierung", "Audio generation": "Audioerzeugung",
    "Video generation": "Videoerzeugung", "Vision": "Bildverständnis", "Browser automation": "Browserautomatisierung",
    "Web research": "Webrecherche", "GPU workloads": "GPU-Aufgaben", "Confidential data": "Vertrauliche Daten",
    "Claude limit unknown": "Claude-Limit unbekannt", "Add own": "Eigene hinzufügen",
    # ---- desktop messages
    "Error: %s": "Fehler: %s",
    "HQ could not execute the command.": "HQ konnte den Befehl nicht ausführen.",
    "Stop requested. HQ is waiting for %d running agents; they continue working.": "Stopp angefordert. HQ wartet auf %d laufende Agenten; sie arbeiten ununterbrochen weiter.",
    "Stop requested. HQ is stopping the scheduler; no new agents will start.": "Stopp angefordert. HQ beendet die Steuerung; es werden keine neuen Agenten gestartet.",
    "HQ is already starting.": "HQ wird bereits gestartet.",
    "HQ is already running. The dashboard is open.": "HQ läuft bereits. Das Dashboard ist geöffnet.",
    "HQ has started.": "HQ wurde gestartet.",
    "The desktop message supports only the background start.": "Die Desktop-Rückmeldung unterstützt nur den Hintergrundstart.",
    "The graphical dashboard is already open.": "Das grafische Dashboard ist bereits geöffnet.",
    # ---- common
    "Yes": "Ja", "No": "Nein", "yes": "ja", "no": "nein", "Cancel": "Abbrechen", "Save": "Speichern", "Close": "Schließen", "Add": "Hinzufügen",
    "error: %s": "Fehler: %s", "loading…": "wird geladen…", "none": "keine", "no data": "keine Daten",
    # ---- run and agent states
    "RUNNING": "LÄUFT", "PAUSED": "PAUSIERT", "STOPPED": "GESTOPPT", "STOPPING": "STOPPT",
    "running": "läuft", "waiting": "wartet", "starting": "startet", "done": "fertig", "failed": "fehlgeschlagen", "stopped": "gestoppt", "killed": "beendet", "interrupted": "unterbrochen", "ended": "beendet", "timeout": "Zeitüberschreitung",
    # ---- main window
    "HaiveQueen | AI Agent Orchestrator": "HaiveQueen | KI-Agenten-Orchestrator",
    "AI AGENT ORCHESTRATOR": "KI-AGENTEN-ORCHESTRATOR",
    "A SWARM\nOF AGENTS.\nONE QUEEN.": "EIN SCHWARM\nVON AGENTEN.\nEINE QUEEN.",
    "No live output yet (%s is missing or empty).": "Noch keine Live-Ausgabe (%s fehlt oder ist leer).",
    "active": "aktiv", "online": "online", "in / out (today)": "ein / aus (heute)", "average today": "Schnitt heute",
    "APIARIES": "APIARIES", "HIVES": "HIVES", "QUEENS": "QUEENS", "BUSY BEES": "AKTIVE BEES", "TOTAL I/O": "GESAMT I/O", "START CONTEXT": "STARTKONTEXT",
    "… %d more lines": "… %d weitere Zeilen",
    "%d hives across %d apiaries": "%d Hives in %d Apiaries", "%d hives, %d apiaries": "%d Hives, %d Apiaries",
    "ACTIVE HIVES": "AKTIVE HIVES", "live": "live", "pause/resume": "Pause/Weiter", "plan": "Plan", "apiary": "Apiary", "table": "Tabelle", "Space": "Leer", "Enter": "Enter", "Tab": "Tab",
    "HIVE": "HIVE", "APIARY": "APIARY", "QUEEN": "QUEEN", "BEES": "BEES", "PROGRESS": "FORTSCHRITT", "STATUS": "STATUS", "RUNTIME": "LAUFZEIT",
    "%s | %s | packages %d done, %d implemented%s%s of %d | hive total in/out %s/%s | %s": "%s | %s | Pakete %d fertig, %d umgesetzt%s%s von %d | Hive gesamt ein/aus %s/%s | %s",
    ", %d rejected": ", %d abgelehnt", ", %d waiting (%s)": ", %d wartend (%s)",
    "no active hives": "keine aktiven Hives",
    "QUEENS AND BEES": "QUEENS UND BEES", "%d running, %d waiting across %d hive": "%d laufend, %d wartend in %d Hive", "%d running, %d waiting across %d hives": "%d laufend, %d wartend in %d Hives",
    "stop": "stoppen", "queen": "Queen",
    "BEE": "BEE", "MODEL": "MODELL", "TASK": "AUFGABE", "CONTEXT": "KONTEXT", "TOKENS": "TOKENS",
    "no queens or bees": "keine Queens oder Bees",
    "HIVE ACTIVITY": "HIVE-AKTIVITÄT", "tokens in/out, last 7 days": "Tokens ein/aus, 7 Tage", "no usage yet": "noch keine Nutzung",
    "SYSTEM": "SYSTEM", "(%d cores)": "(%d Kerne)",
    "APIARY QUEUE": "WARTESCHLANGE", "questions: %d": "Fragen: %d", "queued: %d": "wartend: %d",
    "LOGS": "LOG", "all": "alle", "save": "speichern", "clear": "leeren", "Auto-scroll": "Auto-Scroll", "On": "An", "Off": "Aus", "no log lines": "keine Log-Zeilen",
    "MODEL LIMITS": "MODELL-LIMITS", "used share, resets and slots": "Verbrauch, Rücksetzung und Slots", "n/a": "k. A.", "no limit data": "keine Limit-Daten",
    "state, usage, plans and questions": "Zustand, Nutzung, Pläne und Fragen",
    "apiaries": "Apiaries", "apiary:%s": "Apiary:%s", "hives": "Hives", "questions": "Fragen", "config": "Einstellungen", "help": "Hilfe",
    "BUILD  •  ORCHESTRATE  •  EVOLVE": "BAUEN  •  STEUERN  •  ENTWICKELN",
    "Queen %s | %s": "Queen %s | %s", "Bee %s | %s": "Bee %s | %s",
    "This row has no agent process yet, so there is no live output.": "Diese Zeile hat noch keinen Agenten-Prozess, daher gibt es keine Live-Ausgabe.",
    "Resume HaiveQueen?": "HaiveQueen fortsetzen?",
    "Pause HaiveQueen? Running agents keep working, no new agent starts.": "HaiveQueen pausieren? Laufende Agenten arbeiten weiter, kein neuer Agent startet.",
    "Restart Queen of %s": "Queen von %s neu starten", "Restart Queen of %s on %s": "Queen von %s auf %s neu starten", "End Queen and Bees of %s": "Queen und Bees von %s beenden",
    "Start again": "Erneut starten", "Pause": "Pausieren", "Stop": "Stoppen", "Reject": "Ablehnen", "Start": "Starten", "Back to draft": "Zurück zum Entwurf",
    "Pause %s? Running agents keep working, no new Queen or Bee starts.": "%s pausieren? Laufende Agenten arbeiten weiter, keine neue Queen oder Bee startet.",
    "Stop %s? The Queen and her Bees are ended and the plan is paused. Handoff, packages, branch and worktrees stay.": "%s stoppen? Die Queen und ihre Bees werden beendet und der Plan pausiert. Übergabe, Pakete, Branch und Worktrees bleiben.",
    "Reject %s? The plan never starts again by itself; starting it again makes it a draft. Nothing is deleted.": "%s ablehnen? Der Plan startet nie wieder von selbst; ein erneuter Start macht ihn zum Entwurf. Nichts wird gelöscht.",
    "Start %s again?": "%s erneut starten?", "Start %s?": "%s starten?", "Put %s back to draft? Nothing is deleted.": "%s zurück zum Entwurf setzen? Nichts wird gelöscht.",
    "Could not change the plan: %s": "Der Plan konnte nicht geändert werden: %s",
    "Restart the Queen of %s? A replacement Queen takes over.": "Die Queen von %s neu starten? Eine Ersatz-Queen übernimmt.",
    "End the Queen and the Bees of %s? The hive stays queenless.": "Die Queen und die Bees von %s beenden? Der Hive bleibt ohne Queen.",
    "Restart the Queen of %s on %s? The Hive moves to %s and keeps it until you change it again.": "Die Queen von %s auf %s neu starten? Der Hive wechselt zu %s und bleibt dort, bis du es wieder änderst.",
    "%s failed: %s": "%s fehlgeschlagen: %s", "exit code %d": "Exit-Code %d",
    "X stops Bees only. Use the Queen menu (M, or a right click on the Queen) to restart or end a Queen.": "X stoppt nur Bees. Nutze das Queen-Menü (M oder Rechtsklick auf die Queen), um eine Queen neu zu starten oder zu beenden.",
    "This Bee has not started yet, so there is nothing to stop.": "Diese Bee ist noch nicht gestartet, daher gibt es nichts zu stoppen.",
    "Stop bee %s (%s)?": "Bee %s (%s) stoppen?",
    "Save the log as": "Log speichern unter",
    "Stop HaiveQueen gracefully? Running agents finish their task first.": "HaiveQueen geordnet stoppen? Laufende Agenten beenden zuerst ihre Aufgabe.",
    "Keys": "Tasten",
    "P  pause / resume\nQ  answer the open questions\nSpace  pause / resume the selected plan\nF  next apiary filter\nM / right click  queen menu of the hive or the Queen row\nTab  switch between the Hives and the Queens and Bees table\nUp/Down  select a row of that table\nClick  select a row\nEnter / double click  live output of the row\nX  stop the selected bee\nC  clear log   S  save log   A  auto-scroll\nL / double click on LOGS  big log   N  routine round lines   Esc  remove the log filter":
        "P  Pause / Weiter\nQ  offene Fragen beantworten\nLeertaste  gewählten Plan pausieren / fortsetzen\nF  nächster Apiary-Filter\nM / Rechtsklick  Queen-Menü des Hives oder der Queen-Zeile\nTab  zwischen der Hive- und der Queens-und-Bees-Tabelle wechseln\nHoch/Runter  Zeile dieser Tabelle wählen\nKlick  Zeile wählen\nEnter / Doppelklick  Live-Ausgabe der Zeile\nX  gewählte Bee stoppen\nC  Log leeren   S  Log speichern   A  Auto-Scroll\nL / Doppelklick auf LOG  großes Log   N  Routine-Rundenzeilen   Esc  Log-Filter entfernen",
    "Start HaiveQueen": "HaiveQueen starten", "Stopping: running agents finish their task first": "Stoppt: laufende Agenten beenden zuerst ihre Aufgabe",
    "Resume new starts": "Neue Starts fortsetzen", "Pause new starts": "Neue Starts pausieren", "Stop HaiveQueen": "HaiveQueen stoppen",
    # ---- hive states and blockers
    "Hive limit %s reached": "Hive-Limit %s erreicht", "Not enough free RAM: %s": "Zu wenig freier RAM: %s", "No free provider with a Queen model": "Kein freier Anbieter mit Queen-Modell",
    "Waiting for provider %s": "Wartet auf Anbieter %s", "Plan branch is used elsewhere": "Plan-Branch wird anderswo verwendet", "Plan worktree could not be prepared": "Plan-Worktree konnte nicht vorbereitet werden",
    "HaiveQueen stopped": "HaiveQueen gestoppt", "HaiveQueen paused": "HaiveQueen pausiert", "Waiting for integration": "Wartet auf Integration", "Handoff: Bees still running": "Übergabe: Bees laufen noch",
    "Waiting after interrupted Queen": "Wartet nach unterbrochener Queen", "Plan branch or worktree missing": "Plan-Branch oder Worktree fehlt", "Start already reserved": "Start bereits reserviert",
    "Waiting for owner check": "Wartet auf Owner-Prüfung", "Waiting for answer": "Wartet auf Antwort", "No active Queen": "Keine aktive Queen", "; open questions %s": "; offene Fragen %s", "Done": "Fertig", "Resting until %s": "Ruht bis %s",
    "Queenless": "Ohne Queen", "Waiting for Codex": "Wartet auf Codex", "Waiting for provider": "Wartet auf Anbieter", "Handoff": "Übergabe", "Waiting to start": "Wartet auf Start",
    "Plan rejected": "Plan abgelehnt", "Running, plan paused": "Läuft, Plan pausiert", "Plan paused": "Plan pausiert", "Running": "Läuft", "Idle": "Untätig",
    # ---- limits and status panel
    "resets %s": "Rücksetzung %s", "new window": "neues Fenster", "quota %.2fx": "Kontingent %.2fx", "reset unknown": "Rücksetzung unbekannt", "no medium model": "kein medium-Modell", "  ◀ next": "  ◀ nächster", "%s slots": "%s Slots", "%s 7d": "%s 7T",
    "%s  today %s  plans %s  total %s": "%s  heute %s  Pläne %s  gesamt %s",
    "no new agents": "keine neuen Agenten", "  Apiary: %s": "  Apiary: %s", "Beekeeper %s": "Beekeeper %s", "  (draft: %s)": "  (Entwurf: %s)",
    "STATE": "ZUSTAND", "USAGE": "NUTZUNG", "PLANS": "PLÄNE", "QUESTIONS": "FRAGEN", "ROUND ERRORS": "RUNDENFEHLER", "SELF-UPDATE": "SELBST-UPDATE",
    "%s %d / %d integrated (%d%%)": "%s %d / %d integriert (%d%%)", "open: %d  %s": "offen: %d  %s", "none open": "keine offen",
    "idle since %s": "untätig seit %s", "idle": "untätig", "self-update ": "Selbst-Update ", "ok": "ok", "rollback": "Rollback", " (back to %s)": " (zurück auf %s)",
    "Build jobs %s of %d cores (running)": "Build-Jobs %s von %d Kernen (läuft)", "Build jobs %d of %d cores (next build)": "Build-Jobs %d von %d Kernen (nächster Build)",
    " | per job %s": " | pro Job %s", "not measured yet": "noch nicht gemessen", " | active: %d cores": " | aktiv: %d Kerne",
    # ---- overlays
    "%s (now)": "%s (jetzt)", "model | in / out | fresh in | cached in": "Modell | ein / aus | frisch ein | Cache ein", "Apiary | in / out today": "Apiary | ein / aus heute",
    "Hive | in / out today | per finished package": "Hive | ein / aus heute | pro fertigem Paket", "Queen | Hive | run in / out": "Queen | Hive | Lauf ein / aus", "Bee | Hive | run in / out": "Bee | Hive | Lauf ein / aus",
    "%s | average %s over %d start today": "%s | Schnitt %s über %d Start heute", "%s | average %s over %d starts today": "%s | Schnitt %s über %d Starts heute",
    "  Apiary %s: %s | %s": "  Apiary %s: %s | %s", "  Hive %s (%s): %s": "  Hive %s (%s): %s",
    "  Queens: average %s%s": "  Queens: Schnitt %s%s", "  Bees: average %s%s": "  Bees: Schnitt %s%s", " (parts of HaiveQueen approximate)": " (Anteile von HaiveQueen ungefähr)",
    "    CLI's own part %s": "    Eigenanteil der CLI %s", "role": "Rolle", "task": "Aufgabe", "rules %s": "Regeln %s",
    # ---- start context benchmark
    "no benchmark yet": "noch kein Benchmark", "HaiveQueen %s, own profile %s, %+d %%": "HaiveQueen %s, eigenes Profil %s, %+d %%",
    "%s with the HaiveQueen profile": "%s mit dem HaiveQueen-Profil", "%s without it": "%s ohne es", "%s: no code folder": "%s: kein Code-Ordner",
    # ---- hives window
    "HaiveQueen Hives": "HaiveQueen Hives", "Apiary": "Apiary", "Hive": "Hive", "Status": "Status", "Has Hive": "Hat Hive",
    # ---- questions window
    "Write an answer first.": "Schreibe zuerst eine Antwort.", "The answer says ok, but steps are marked as rejected.": "Die Antwort sagt ok, aber Schritte sind als abgelehnt markiert.",
    "HaiveQueen questions": "HaiveQueen Fragen", "Your answer": "Deine Antwort", "This answer applies to the whole Apiary": "Diese Antwort gilt für die ganze Apiary",
    "Not accepted steps (marked rejected, the Queen reworks them)": "Nicht akzeptierte Schritte (als abgelehnt markiert, die Queen arbeitet sie nach)",
    "Use recommendation": "Empfehlung übernehmen", "Answer": "Antworten", "No open questions.": "Keine offenen Fragen.", "%s\n\nRecommendation:\n%s": "%s\n\nEmpfehlung:\n%s",
    "question %s is already answered": "Frage %s ist bereits beantwortet", "unknown question %s": "unbekannte Frage %s",
    # ---- settings window
    "CPU cores per build": "CPU-Kerne pro Build", "Parallel builds": "Parallele Builds", "Minimum free RAM before a build": "Mindestens freier RAM vor einem Build",
    "How many of your processor cores builds may use in total; a build uses fewer when the free RAM is short.": "Wie viele deiner Prozessorkerne Builds insgesamt nutzen dürfen; ein Build nutzt weniger, wenn der freie RAM knapp ist.",
    "How many targets the build command builds at the same time (the number after --parallel); each target gets the cores divided by this number.": "Wie viele Ziele der Build-Befehl gleichzeitig baut (die Zahl nach --parallel); jedes Ziel bekommt die Kerne geteilt durch diese Zahl.",
    "A build only starts while at least this many GB of RAM are free, otherwise it waits.": "Ein Build startet nur, solange mindestens so viele GB RAM frei sind, sonst wartet er.",
    "%s: a whole number is expected": "%s: eine ganze Zahl wird erwartet", "%s: a whole number from %d to %d is expected": "%s: eine ganze Zahl von %d bis %d wird erwartet", "%s: a whole number from %d up is expected": "%s: eine ganze Zahl ab %d wird erwartet",
    "Minimum free RAM before a build: a number is expected": "Mindestens freier RAM vor einem Build: eine Zahl wird erwartet", "Minimum free RAM before a build: a number above 0 is expected": "Mindestens freier RAM vor einem Build: eine Zahl über 0 wird erwartet",
    "the settings cannot be written as TOML without changing them": "die Einstellungen lassen sich nicht unverändert als TOML schreiben", "roundMinutes must be greater than zero": "roundMinutes muss größer als null sein", "provider names must be unique": "Anbieternamen müssen eindeutig sein",
    "a provider entry has no name": "ein Anbieter-Eintrag hat keinen Namen",
    "provider %s: use must be preferred, fallback or capability-only, not '%s'": "Anbieter %s: use muss preferred, fallback oder capability-only sein, nicht '%s'",
    "provider %s: beeMode must be subagent or process, not '%s'": "Anbieter %s: beeMode muss subagent oder process sein, nicht '%s'",
    "a number must be finite": "eine Zahl muss endlich sein", "cannot write %r as TOML": "%r lässt sich nicht als TOML schreiben", "a number is expected": "eine Zahl wird erwartet", "valid JSON is expected": "gültiges JSON wird erwartet",
    "config.toml (machine)": "config.toml (Rechner)", "project.toml (%s)": "project.toml (%s)", "true or false expected": "true oder false erwartet",
    "HaiveQueen settings": "HaiveQueen Einstellungen", "File:": "Datei:", "Add provider": "Anbieter hinzufügen", "Add project": "Projekt hinzufügen", "Remove entry": "Eintrag entfernen",
    "Revert": "Verwerfen", "Open folder": "Ordner öffnen", "Changes apply to the running HaiveQueen without a restart. The old file is kept as <name>.bak.": "Änderungen gelten ohne Neustart für das laufende HaiveQueen. Die alte Datei bleibt als <name>.bak erhalten.",
    "Custom CLI": "Eigene CLI", "General": "Allgemein", "provider: %s": "Anbieter: %s", "project: %s": "Projekt: %s", "Build": "Build", "login": "Login", "Login": "Login", "Benchmark Start Context": "Startkontext messen",
    "Login present": "Login vorhanden", "Login missing": "Login fehlt",
    "No login is set up for this provider. For a CLI that is not built in, set the profile variables, the login command and the login files on this page.": "Für diesen Anbieter ist kein Login eingerichtet. Lege für eine nicht eingebaute CLI die Profilvariablen, den Login-Befehl und die Login-Dateien auf dieser Seite fest.",
    "Open a terminal window and run `%s` for provider %s in the HaiveQueen profile?": "Ein Terminalfenster öffnen und `%s` für Anbieter %s im HaiveQueen-Profil ausführen?",
    "The login window could not be opened (no terminal program found).": "Das Login-Fenster konnte nicht geöffnet werden (kein Terminalprogramm gefunden).",
    "Start context: no benchmark yet": "Startkontext: noch kein Benchmark",
    "Measure the start context of provider %s in every registered Apiary, twice: once with the HaiveQueen profile and once with your own configuration? This starts the CLI %d times and uses a few tokens of your login.": "Den Startkontext von Anbieter %s in jeder registrierten Apiary zweimal messen: einmal mit dem HaiveQueen-Profil und einmal mit deiner eigenen Konfiguration? Das startet die CLI %d-mal und verbraucht einige Tokens deines Logins.",
    "measuring …": "misst …", "Provider %s is excluded (excludedProviders): nothing was measured.": "Anbieter %s ist ausgeschlossen (excludedProviders): es wurde nichts gemessen.",
    "new setting": "neue Einstellung", "Remove this provider from the settings?": "Diesen Anbieter aus den Einstellungen entfernen?", "Remove this project from the settings?": "Dieses Projekt aus den Einstellungen entfernen?", "Saved.": "Gespeichert.",
    # ---- owner questions the program files
    "Provider %s (protocol %s) cannot be kept to the HaiveQueen profile, so it would read private files. This package is stopped. Which provider may run it?": "Anbieter %s (Protokoll %s) lässt sich nicht auf das HaiveQueen-Profil beschränken und würde private Dateien lesen. Dieses Paket ist gestoppt. Welcher Anbieter darf es ausführen?",
    "Give the package to a provider that can be kept to the profile.": "Gib das Paket einem Anbieter, der sich auf das Profil beschränken lässt.",
    "The package remains unsolved at the highest available class. How should it continue?": "Das Paket bleibt auf der höchsten verfügbaren Klasse ungelöst. Wie soll es weitergehen?",
    "Review the failed attempts and choose the next approach.": "Prüfe die gescheiterten Versuche und wähle das weitere Vorgehen.",
    "No configured provider offers the capability %s. How should this package continue?": "Kein eingerichteter Anbieter bietet die Fähigkeit %s. Wie soll dieses Paket weitergehen?",
    "No configured provider offers the required class or a higher class. How should this package continue?": "Kein eingerichteter Anbieter bietet die nötige oder eine höhere Klasse. Wie soll dieses Paket weitergehen?",
    "Configure an eligible provider.": "Richte einen geeigneten Anbieter ein.",
    "Provider %s has %d slot(s), but its Queen plus a Bee that runs as a separate process needs %d, so no Bee of this Hive can ever start. Raise the slots of %s, or set beeMode = \"subagent\" for it so its Bees need no slot of their own.": "Anbieter %s hat %d Slot(s), aber seine Queen und eine Bee als eigener Prozess brauchen %d, daher kann nie eine Bee dieses Hives starten. Erhöhe die Slots von %s oder setze dort beeMode = \"subagent\", damit seine Bees keinen eigenen Slot brauchen.",
    "Raise slots to %d, or set beeMode to subagent if the provider can run subagents.": "Erhöhe die Slots auf %d oder setze beeMode auf subagent, wenn der Anbieter Subagenten ausführen kann.",
    "Plan %s is integrated and deployed. Please test the integrated build in the game: answer ok, or say what is not right and the next Queen fixes exactly that.": "Plan %s ist integriert und ausgeliefert. Bitte teste den integrierten Build im Spiel: antworte ok oder sage, was nicht stimmt, und die nächste Queen behebt genau das.",
    "\n\nReport of the Queen:\n": "\n\nBericht der Queen:\n",
    "Test it in the game, then answer ok or describe what is wrong.": "Teste es im Spiel und antworte dann ok oder beschreibe, was nicht stimmt.",
    "no final text (status %s)": "kein Abschlusstext (Status %s)",
    "Queen %s of this Hive ended without new work (no commit, no changed file, no question). Her last text: %s How should the Hive continue?": "Queen %s dieses Hives endete ohne neue Arbeit (kein Commit, keine geänderte Datei, keine Frage). Ihr letzter Text: %s Wie soll der Hive weitermachen?",
    "Answer what blocks her, or say how she should count or decide it; then the next Queen starts.": "Antworte, was sie blockiert, oder sage, wie sie es zählen oder entscheiden soll; dann startet die nächste Queen.",
}

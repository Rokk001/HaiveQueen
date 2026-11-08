"""Texts of the GUI, the desktop messages and the owner texts the program writes itself, in English and German.

The English text is the key: `tr("Close")` gives it back unchanged for English and for every language without a catalog (fallback). GUI and desktop
messages use the language of the machine settings (General page of config.toml); owner texts of a Project pass that Project's language. Fixed protocol
words (plan statuses, the words in plan and state files) and the product names HaiveQueen and Queen are never translated.
Project, Plan, Agent and Orchestrator are translated normally.
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
    if re.search(r'\b(?:Apiar(?:y|ies)|Hives?|Beekeepers?|Bees?)\b', text, re.I):
        return text  # Historical event text stays as stored.
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


def _branch_cleanup(match):
    """The result of a branch cleanup in German words instead of its JSON; a text that is not such a result stays as written behind the translated head."""
    head = "Branch-Bereinigung: "
    places = {"local": "lokal", "remote": "Remote", "all": "alle"}
    try:
        result = json.loads(match.group(1))
        deleted = ["%s %s" % (places.get(item.split(" ", 1)[0], item.split(" ", 1)[0]), item.split(" ", 1)[1]) for item in result["deleted"]]
        kept = ["%s %s (%s)" % (places.get(row["where"], row["where"]), row["branch"], _worktree_reason(row["reason"])) for row in result["kept"]]
    except (ValueError, KeyError, TypeError, IndexError):
        return head + match.group(1)
    return head + ("; ".join("%s: %s" % (label, ", ".join(items)) for label, items in (("gelöscht", deleted), ("behalten", kept)) if items) or "nichts zu tun")


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
    ("used by assigned Agent", "von einem zugewiesenen Agenten verwendet"),
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
    ("protected branch", "geschützter Branch"),
    ("belongs to another open plan", "gehört zu einem anderen offenen Plan"),
    ("branch has an open queue row", "Branch hat einen offenen Eintrag in der Warteschlange"),
    ("has commits that are not in the integrated tip", "hat Commits, die nicht im integrierten Stand sind"),
    ("checked out in a worktree", "in einem Worktree ausgecheckt"),
    ("git branch -D failed", "git branch -D fehlgeschlagen"),
    ("push failed: ", "Push fehlgeschlagen: "),
    ("an agent of the plan is running", "ein Agent des Plans läuft"),
    ("remote not readable: ", "Remote nicht lesbar: "),
    ("the integrated tip is not in the integration branch", "der integrierte Stand ist nicht im Integrations-Branch"),
)


_LOG_LINES = [(re.compile(pattern), replacement) for pattern, replacement in (
    # ---- worktree cleanup, before the general patterns: its JSON holds free text
    (r"^Worktree cleanup failed: (.*)$", lambda m: "Worktree-Bereinigung fehlgeschlagen: " + _worktree_reason(m.group(1))),
    (r"^Worktree cleanup: (.*)$", _worktree_cleanup),
    (r"^Branch cleanup failed: (.*)$", lambda m: "Branch-Bereinigung fehlgeschlagen: " + _worktree_reason(m.group(1))),
    (r"^Branch cleanup: (.*)$", _branch_cleanup),
    # ---- rounds and the loop
    (r"^round done: (\d+) Queen\(s\) started((?:; .*)?)$", _round_done),
    (r"^round skipped \((.*)\)$", r"Runde übersprungen (\1)"),
    (r"^(.*) not started: (.*)$", lambda m: "%s nicht gestartet: %s" % (m.group(1), _german_line(m.group(2)))),
    (r"^ROUND ERROR ", "RUNDENFEHLER "),
    (r"^SERVER TEST ERROR ", "SERVERTEST-FEHLER "),
    (r"^PROVIDER CHECK ERROR ", "PROVIDERPRÜFUNG-FEHLER "),
    (r"^Round error: ", "Rundenfehler: "),
    (r"^RETENTION ERROR ", "AUFBEWAHRUNGSFEHLER "),
    (r"^UPDATE ERROR ", "UPDATE-FEHLER "),
    (r"^release step failed: ", "Release-Schritt fehlgeschlagen: "),
    (r"^release (\S+) of (\S+) published at (\S+)$", r"Release \1 von \2 veröffentlicht bei \3"),
    (r"^release (\S+) of (\S+) not published: ", r"Release \1 von \2 nicht veröffentlicht: "),
    (r"^release of (\S+) not prepared: ", r"Release von \1 nicht vorbereitet: "),
    (r"^EXPORT ERROR ", "EXPORTFEHLER "),
    (r"^TECHNICAL NOTE: (.*?): (.*), marked failed$", lambda m: "TECHNISCHER HINWEIS: %s: %s, als fehlgeschlagen markiert" % (m.group(1), _german_line(m.group(2)))),
    (r"^TECHNICAL NOTE: ", "TECHNISCHER HINWEIS: "),
    (r"^loop started \(roundMinutes=(.*)\)$", r"Steuerung gestartet (roundMinutes=\1)"),
    (r"^Orchestrator started \(round every (.*) minutes\)$", r"Orchestrator gestartet (Runde alle \1 Minuten)"),
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
    (r"^plan (.+?): (\S+) -> (\S+)$", r"Plan \1: \2 -> \3"),
    (r"^plan (.+?): status change failed: ", r"Plan \1: Statuswechsel fehlgeschlagen: "),
    (r"^plan (.+?): status change to (\S+) failed: ", r"Plan \1: Statuswechsel auf \2 fehlgeschlagen: "),
    (r"^plan status not updated: ", r"Planstatus nicht aktualisiert: "),
    (r"^Package (\S+) stopped by the owner; it stays stopped until the owner resumes it\. Its subagent Agent keeps its Agent place until its Queen confirms that it ended$", r"Paket \1 vom Besitzer gestoppt; es bleibt gestoppt, bis der Besitzer es fortsetzt. Sein Subagent behält seinen Agenten-Platz, bis seine Queen bestätigt, dass er beendet ist"),
    (r"^The subagent Agent (\S+) \((\S+)\) ended with its Queen (.+); its Agent place is free$", r"Der Subagent \1 (\2) ist mit seiner Queen \3 beendet; sein Agenten-Platz ist frei"),
    (r"^ended subagent Agents not closed: (.+)$", r"beendete Subagenten nicht geschlossen: \1"),
    (r"^The stopped subagent Agent of package (\S+) has ended; its Agent place is free$", r"Der gestoppte Subagent von Paket \1 ist beendet; sein Agenten-Platz ist frei"),
    (r"^New Queen of (.+): subagent Agents (.+) keep their Agent places until their end is confirmed$", r"Neue Queen von \1: die Subagenten \2 behalten ihre Agenten-Plätze, bis ihr Ende bestätigt ist"),
    (r"^the subagent Agent (\S+) of this package has not confirmed its end; record its result with bzz agent result first$", r"der Subagent \1 dieses Pakets hat sein Ende nicht bestätigt; melde zuerst sein Ergebnis mit bzz agent result"),
    (r"^the subagent Agent (\S+) of the earlier Queen (\S+) has not confirmed its end; record its result with bzz agent result --ended-evidence first$", r"der Subagent \1 der früheren Queen \2 hat sein Ende nicht bestätigt; melde zuerst sein Ergebnis mit bzz agent result --ended-evidence"),
    (r"^the stopped subagent Agent (\S+) of this package has not confirmed its end; once it has ended, report it with bzz agent result --outcome interrupted$", r"der gestoppte Subagent \1 dieses Pakets hat sein Ende nicht bestätigt; sobald er beendet ist, melde es mit bzz agent result --outcome interrupted"),
    (r"^stopped packages not reported: ", r"gestoppte Pakete nicht gemeldet: "),
    (r"^Package (\S+) stopped by the owner; it stays stopped until the owner resumes it$",r"Paket \1 vom Besitzer gestoppt; es bleibt gestoppt, bis der Besitzer es fortsetzt"),
    (r"^Package (\S+) resumed by the owner$", r"Paket \1 vom Besitzer fortgesetzt"),
    (r"^the owner stopped this package; it starts again only after the owner resumes it$", r"der Besitzer hat dieses Paket gestoppt; es startet erst wieder, wenn der Besitzer es fortsetzt"),
    (r"^Queen (\S+) told to start the startable packages (.+)$", r"Queen \1 aufgefordert, die startbaren Pakete \2 zu starten"),
    (r"^startable packages not reported: ", r"startbare Pakete nicht gemeldet: "),
    (r"^Agent slots not reserved: ", r"Agent-Slots nicht reserviert: "),
    (r"^plan (.+?): the owner takes over the existing branch (.+)$", r"Plan \1: der Besitzer übernimmt den bestehenden Branch \2"),
    (r"^plan (.+?): the owner wants a new branch; (\S+) is created instead of (.+)$", r"Plan \1: der Besitzer will einen neuen Branch; \2 wird statt \3 angelegt"),
    (r"^Fault loop_vanished resolved: ", r"Fehler loop_vanished behoben: "),
    (r"^Package (\S+) raised to Queen class (\S+) after failures$", r"Paket \1 nach Fehlschlägen auf die Queen-Klasse \2 angehoben"),
    (r"^Additional text for question (\d+): ", r"Zusätzlicher Text zu Frage \1: "),
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
    (r"^no free slot$", r"kein freier Slot"),
    (r"^the provider has no slots$", r"der Anbieter hat keine Slots"),
    (r"^usage unknown, no recent reading \((.*)\)$", r"Verbrauch unbekannt, keine aktuelle Messung (\1)"),
    (r'^Plan state "(.*?)"(?: \(questions (.*)\))?$', lambda m: "Plan-Zustand %s%s" % (m.group(1), " (Fragen %s)" % m.group(2) if m.group(2) else "")),
    (r"^waiting for the build: (.*)$", lambda m: "Wartet auf den Build: " + _german_line(m.group(1))),
    (r"^waiting for provider (\S+): (.*)$", lambda m: "Wartet auf Anbieter %s: %s" % (m.group(1), _german_line(m.group(2)))),
    (r"^waiting for the integration place$", "Wartet auf den Integrationsplatz"),
    (r"^handing off, Agent processes are still running$", "Übergabe, Agent-Prozesse laufen noch"),
    (r"^retry after an interrupted Queen is not due yet$", "Neuer Versuch nach unterbrochener Queen ist noch nicht fällig"),
    (r"^Plan limit (\d+) reached$", r"Plan-Limit \1 erreicht"),
    (r"^free RAM (\S+) GB is below the minimum free RAM before a build of (\S+) GB$", lambda m: decimal("freier RAM %s GB liegt unter dem Mindestwert vor einem Build von %s GB" % m.groups(), "German")),
    (r"^free RAM (\S+) GB is below the minimum (\S+) GB$", lambda m: decimal("freier RAM %s GB liegt unter dem Mindestwert %s GB" % m.groups(), "German")),
    (r"^no provider with a free slot and a free limit offers class (\S+)$", r"kein Anbieter mit freiem Slot und freiem Limit bietet Klasse \1"),
    (r"^no provider with a free slot offers class (\S+)$", r"kein Anbieter mit freiem Slot bietet Klasse \1"),
    (r"^its branch (.+) is held by work outside the Orchestrator$", r"sein Branch \1 wird von Arbeit außerhalb des Orchestrators gehalten"),
    (r"^its branch (.+) exists without run history: waiting for the owner to take it over or create a new one$", r"sein Branch \1 besteht ohne Laufverlauf: wartet auf die Entscheidung, ihn zu übernehmen oder einen neuen anzulegen"),
    (r"^the Plan is already claimed$", "der Plan ist bereits reserviert"),
    (r"^worktree for branch (.+?) could not be prepared \((.*)\)$", r"Worktree für Branch \1 konnte nicht vorbereitet werden (\2)"),
    (r"^no branch or worktree named in the plan or its state file$", "kein Branch oder Worktree in Plan oder State-Datei genannt"),
    # ---- why a Bee did not start
    (r"^Agent start refused for package (\S+): (.*)$", lambda m: "Agent-Start für Paket %s abgelehnt: %s" % (m.group(1), _german_line(m.group(2)))),
    (r"^dependency (\S+) of package (\S+) not in plan$", r"Abhängigkeit \1 von Paket \2 ist nicht im Plan"),
    (r"^dependency (\S+) of package (\S+) not done$", r"Abhängigkeit \1 von Paket \2 ist nicht fertig"),
    (r"^the Plan may not start an Agent now \(Plan state (\S+), context limit or HANDOFF reached\)$", r"der Plan darf jetzt keinen Agenten starten (Plan-Zustand \1, Kontextgrenze oder HANDOFF erreicht)"),
    (r"^no running Queen of this plan$", "keine laufende Queen dieses Plans"),
    (r"^the name (\S+) is already held by a running Queen or Agent; use the free name (\S+)$", r"der Name \1 gehört bereits einer laufenden Queen oder einem Agenten; nutze den freien Namen \2"),
    (r"^the Queen already has (\S+) Agents running$", r"die Queen hat bereits \1 laufende Agenten"),
    (r"^the plan is not cut yet: write its packages into the header and run bzz plan check first$", "der Plan ist noch nicht aufgeteilt: schreibe zuerst seine Pakete in den Kopf und führe bzz plan check aus"),
    (r"^the package is not in the plan$", "das Paket ist nicht im Plan"),
    (r"^no configured provider offers the required class or the capability (.*) \(question filed\)$", r"kein eingerichteter Anbieter bietet die nötige Klasse oder die Fähigkeit \1 (Frage gestellt)"),
    (r"^no configured provider offers the required class \(question filed\)$", "kein eingerichteter Anbieter bietet die nötige Klasse (Frage gestellt)"),
    (r"^the package failed (\d+) times at the Queen class (\S+) and no higher class is offered$", r"das Paket scheiterte \1-mal auf der Queen-Klasse \2 und keine höhere Klasse wird angeboten"),
    (r"^the package failed (\d+) times and no higher class is offered$", r"das Paket scheiterte \1-mal und keine höhere Klasse wird angeboten"),
    (r"^class (\S+) was requested but the system computes class (\S+) \(plan class (\S+), no stored escalation\)$", r"Klasse \1 wurde angefordert, das System berechnet aber Klasse \2 (Planklasse \3, keine gespeicherte Eskalation)"),
    (r"^the worktree (.+) is not a valid separate worktree with a branch of this repository$", r"der Worktree \1 ist kein gültiger eigener Worktree mit einem Branch dieses Repositorys"),
    (r"^an Agent process may not start now \(starts paused or stopped, too little free RAM, or no free slot of provider (\S+)\)$", r"ein Agent-Prozess darf jetzt nicht starten (Starts pausiert oder gestoppt, zu wenig freier RAM oder kein freier Slot von Anbieter \1)"),
    (r"^no valid separate worktree for the Agent was given$", "für den Agenten wurde kein gültiger eigener Worktree angegeben"),
    (r"^the agent runner could not start the Agent process$", "der Agent-Runner konnte den Agent-Prozess nicht starten"),
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
    "%s 5h": "%s 5 Std.",
    "Reading age: %d minutes": "Alter des Messwerts: %d Minuten",
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
    "PROJECTS": "PROJEKTE", "PLANS": "PLÄNE", "QUEENS": "QUEENS", "BUSY AGENTS": "AKTIVE AGENTEN", "TOTAL I/O": "GESAMT I/O", "START CONTEXT": "STARTKONTEXT",
    "… %d more lines": "… %d weitere Zeilen",
    "%d plans across %d projects": "%d Pläne in %d Projekte", "%d plans, %d projects": "%d Pläne, %d Projekte",
    "ACTIVE PLANS": "AKTIVE PLÄNE", "live": "live", "pause/resume": "Pause/Weiter", "plan": "Plan", "project": "Projekt", "table": "Tabelle", "Space": "Leer", "Enter": "Enter", "Tab": "Tab",
    "PLAN": "PLAN", "PROJECT": "PROJEKT", "QUEEN": "QUEEN", "AGENTS": "AGENTEN", "PROGRESS": "FORTSCHRITT", "STATUS": "STATUS", "RUNTIME": "LAUFZEIT",
    "%s | %s | packages %d done, %d implemented%s%s of %d | plan total in/out %s/%s | %s": "%s | %s | Pakete %d fertig, %d umgesetzt%s%s von %d | Plan gesamt ein/aus %s/%s | %s",
    ", %d rejected": ", %d abgelehnt", ", %d waiting (%s)": ", %d wartend (%s)",
    "no active plans": "keine aktiven Pläne",
    "QUEENS AND AGENTS": "QUEENS UND AGENTEN", "%d running, %d waiting across %d plan": "%d laufend, %d wartend in %d Plan", "%d running, %d waiting across %d plans": "%d laufend, %d wartend in %d Pläne",
    "stop": "stoppen", "queen": "Queen",
    "AGENT": "AGENT", "MODEL": "MODELL", "TASK": "AUFGABE", "CONTEXT": "KONTEXT", "TOKENS": "TOKENS",
    "no queens or agents": "keine Queens oder Agenten",
    "PLAN ACTIVITY": "PLAN-AKTIVITÄT", "tokens in/out, last 7 days": "Tokens ein/aus, 7 Tage", "no usage yet": "noch keine Nutzung",
    "SYSTEM": "SYSTEM", "(%d cores)": "(%d Kerne)",
    "PROJECT QUEUE": "WARTESCHLANGE", "questions: %d": "Fragen: %d", "queued: %d": "wartend: %d",
    "LOGS": "LOG", "all": "alle", "save": "speichern", "clear": "leeren", "Auto-scroll": "Auto-Scroll", "On": "An", "Off": "Aus", "no log lines": "keine Log-Zeilen",
    "MODEL LIMITS": "MODELL-LIMITS", "reserve, slots and weekly limit": "Reserve, Slots und Wochenlimit", "reserve, slots and limits": "Reserve, Slots und Limits", "n/a": "k. A.", "no limit data": "keine Limit-Daten",
    "state, usage, plans and questions": "Zustand, Nutzung, Pläne und Fragen",
    "projects": "Projekte", "project:%s": "Projekt:%s", "plans": "Pläne", "questions": "Fragen", "config": "Einstellungen", "help": "Hilfe",
    "BUILD  •  ORCHESTRATE  •  EVOLVE": "BAUEN  •  STEUERN  •  ENTWICKELN",
    "Queen %s | %s": "Queen %s | %s", "Agent %s | %s": "Agent %s | %s",
    "This row has no agent process yet, so there is no live output.": "Diese Zeile hat noch keinen Agenten-Prozess, daher gibt es keine Live-Ausgabe.",
    "Resume HaiveQueen?": "HaiveQueen fortsetzen?",
    "Pause HaiveQueen? Running agents keep working, no new agent starts.": "HaiveQueen pausieren? Laufende Agenten arbeiten weiter, kein neuer Agent startet.",
    "Restart Queen of %s": "Queen von %s neu starten", "Restart Queen of %s on %s": "Queen von %s auf %s neu starten", "End Queen and Agents of %s": "Queen und Agenten von %s beenden",
    "Start again": "Erneut starten", "Pause": "Pausieren", "Stop": "Stoppen", "Reject": "Ablehnen", "Start": "Starten", "Back to draft": "Zurück zum Entwurf",
    "Pause %s? Running agents keep working, no new Queen or Agent starts.": '%s pausieren? Laufende Agenten arbeiten weiter, keine neue Queen und kein neuer Agent startet.',
    "Stop %s? The Queen and her Agents are ended and the plan is paused. Handoff, packages, branch and worktrees stay.": "%s stoppen? Die Queen und ihre Agenten werden beendet und der Plan pausiert. Übergabe, Pakete, Branch und Worktrees bleiben.",
    "Reject %s? The plan never starts again by itself; starting it again makes it a draft. Nothing is deleted.": "%s ablehnen? Der Plan startet nie wieder von selbst; ein erneuter Start macht ihn zum Entwurf. Nichts wird gelöscht.",
    "Start %s again?": "%s erneut starten?", "Start %s?": "%s starten?", "Put %s back to draft? Nothing is deleted.": "%s zurück zum Entwurf setzen? Nichts wird gelöscht.",
    "Could not change the plan: %s": "Der Plan konnte nicht geändert werden: %s",
    "Restart the Queen of %s? A replacement Queen takes over.": "Die Queen von %s neu starten? Eine Ersatz-Queen übernimmt.",
    "End the Queen and the Agents of %s? The plan stays queenless.": "Die Queen und die Agenten von %s beenden? Der Plan bleibt ohne Queen.",
    "Restart the Queen of %s on %s? The Plan moves to %s and keeps it until you change it again.": "Die Queen von %s auf %s neu starten? Der Plan wechselt zu %s und bleibt dort, bis du es wieder änderst.",
    "%s failed: %s": "%s fehlgeschlagen: %s", "exit code %d": "Exit-Code %d",
    "X stops Agents only. Use the Queen menu (M, or a right click on the Queen) to restart or end a Queen.": "X stoppt nur Agenten. Nutze das Queen-Menü (M oder Rechtsklick auf die Queen), um eine Queen neu zu starten oder zu beenden.",
    "This Agent has not started yet, so there is nothing to stop.": "Dieser Agent ist noch nicht gestartet, daher gibt es nichts zu stoppen.",
    "Stop agent %s (%s)?": "Agent %s (%s) stoppen?",
    "The branch %s of this plan already exists, but the plan has no run history. Take over the existing branch or create a new one?": 'Der Branch %s dieses Plans besteht bereits, aber der Plan hat keinen Laufverlauf. Den bestehenden Branch übernehmen oder einen neuen anlegen?',
    "Answer \"take over\" to continue on %s, or \"new\" to create a new branch from the integration branch.": "Antworte \"übernehmen\", um auf %s weiterzuarbeiten, oder \"neu\", um einen neuen Branch vom Integrationsbranch anzulegen.",
    "Stop agent %s (%s)? The package stays stopped until you resume it.": "Agent %s (%s) stoppen? Das Paket bleibt gestoppt, bis du es fortsetzt.",
    "Resume package": "Paket fortsetzen", "Resume package %s?": "Paket %s fortsetzen?", "Could not resume the package: %s": "Das Paket konnte nicht fortgesetzt werden: %s",
    "stopping": "wird gestoppt",
    "The stop of %s (%s) is pending: the Agent keeps its place until its Queen confirms that it has ended.": "Das Stoppen von %s (%s) steht aus: Der Agent behält seinen Platz, bis seine Queen bestätigt, dass er beendet ist.",
    "startable, not started": "startbar, nicht gestartet", "Cancel plan": "Plan abbrechen", "Plan cancelled": "Plan abgebrochen",
    "Cancel %s? The plan never starts again by itself; starting it again makes it a draft. Nothing is deleted.": "%s abbrechen? Der Plan startet nie wieder von selbst; ein erneuter Start macht ihn zum Entwurf. Nichts wird gelöscht.",
    "Save the log as": "Log speichern unter",
    "Stop HaiveQueen gracefully? Running agents finish their task first.": "HaiveQueen geordnet stoppen? Laufende Agenten beenden zuerst ihre Aufgabe.",
    "Keys": "Tasten",
    "P  pause / resume\nQ  answer the open questions\nSpace  pause / resume the selected plan\nF  next project filter\nM / right click  queen menu of the plan or the Queen row\nTab  switch between the Plans and the Queens and Agents table\nUp/Down  select a row of that table\nClick  select a row\nEnter / double click  live output of the row\nX  stop the selected agent\nC  clear log   S  save log   A  auto-scroll\nL / double click on LOGS  big log   N  routine round lines   Esc  remove the log filter":
        'P  Pause / Weiter\nQ  offene Fragen beantworten\nLeertaste  gewählten Plan pausieren / fortsetzen\nF  nächster Projekt-Filter\nM / Rechtsklick  Queen-Menü des Plans oder der Queen-Zeile\nTab  zwischen der Plan- und der Queens-und-Agenten-Tabelle wechseln\nHoch/Runter  Zeile dieser Tabelle wählen\nKlick  Zeile wählen\nEnter / Doppelklick  Live-Ausgabe der Zeile\nX  gewählte Agent stoppen\nC  Log leeren   S  Log speichern   A  Auto-Scroll\nL / Doppelklick auf LOG  großes Log   N  Routine-Rundenzeilen   Esc  Log-Filter entfernen',
    "Start HaiveQueen": "HaiveQueen starten", "Stopping: running agents finish their task first": "Stoppt: laufende Agenten beenden zuerst ihre Aufgabe",
    "Resume new starts": "Neue Starts fortsetzen", "Pause new starts": "Neue Starts pausieren", "Stop HaiveQueen": "HaiveQueen stoppen",
    # ---- hive states and blockers
    "Plan limit %s reached": "Plan-Limit %s erreicht", "Not enough free RAM: %s": "Zu wenig freier RAM: %s", "No free provider with a Queen model": "Kein freier Anbieter mit Queen-Modell",
    "Waiting for provider %s": "Wartet auf Anbieter %s", "Plan branch is used elsewhere": "Plan-Branch wird anderswo verwendet", "Plan worktree could not be prepared": "Plan-Worktree konnte nicht vorbereitet werden",
    "HaiveQueen stopped": "HaiveQueen gestoppt", "HaiveQueen paused": "HaiveQueen pausiert", "Waiting for integration": "Wartet auf Integration", "Handoff: Agents still running": "Übergabe: Agenten laufen noch",
    "Waiting after interrupted Queen": "Wartet nach unterbrochener Queen", "Plan branch or worktree missing": "Plan-Branch oder Worktree fehlt", "Start already reserved": "Start bereits reserviert",
    "Check the integrated result": "Das integrierte Ergebnis prüfen", "Integration step: %s": "Integrationsschritt: %s",
    "Topics that always go to the owner (one per line)": "Themen, die immer an den Owner gehen (eines pro Zeile)", "Owner-check question (empty: default text)": "Frage der Owner-Prüfung (leer: Standardtext)",
    "Run the compile check": "Compile-Check ausführen", "Run the build": "Build ausführen", "Run the deploy": "Deployment ausführen",
    "Integration branch": "Integrations-Branch", "Remote name": "Name des Remotes", "Worktree location (empty: beside the repository)": "Ort der Worktrees (leer: neben dem Repository)",
    "Delete finished worktrees automatically": "Fertige Worktrees automatisch löschen", "Delete integrated branches (local and remote)": "Integrierte Branches löschen (lokal und auf dem Remote)",
    "Publish release at integration": "Release bei der Integration veröffentlichen", "Next version (empty: count the last digit up)": "Nächste Version (leer: letzte Stelle hochzählen)",
    "Next version: three numbers like 2.1.0 are expected": "Nächste Version: drei Zahlen wie 2.1.0 erwartet",
    "update.checkHours: a number of hours from 0 up is expected (0 = no daily check)": "update.checkHours: eine Stundenzahl ab 0 erwartet (0 = keine tägliche Prüfung)",
    "Check now": "Jetzt prüfen", "checking for updates…": "suche nach Updates…", "New version available: %s – Update": "Neue Version verfügbar: %s – Aktualisieren",
    "Install version %s now? HaiveQueen starts no new Queen or Agent, waits until every running agent has ended, installs the new version and restarts.":
        'Version %s jetzt installieren? HaiveQueen startet keine neue Queen und keinen neuen Agenten mehr, wartet, bis jeder laufende Agent beendet ist, installiert die neue Version und startet neu.',
    "installing version %s – waiting for running agents": "installiere Version %s – warte auf laufende Agenten", "update to %s failed, see STATUS": "Update auf %s fehlgeschlagen, siehe STATUS",
    "up to date": "aktuell", "update check failed": "Update-Prüfung fehlgeschlagen", "no release published yet": "noch kein Release veröffentlicht",
    "%s: letters, digits and . / - _ are expected, starting with a letter or digit": "%s: Buchstaben, Ziffern und . / - _ werden erwartet, am Anfang ein Buchstabe oder eine Ziffer",
    "Waiting for owner check": "Wartet auf Owner-Prüfung", "Waiting for answer": "Wartet auf Antwort", "No active Queen": "Keine aktive Queen", "; open questions %s": "; offene Fragen %s", "Done": "Fertig", "Resting until %s": "Ruht bis %s",
    "Queenless": "Ohne Queen", "Waiting for Codex": "Wartet auf Codex", "Waiting for provider": "Wartet auf Anbieter", "Handoff": "Übergabe", "Waiting to start": "Wartet auf Start",
    "Plan rejected": "Plan abgelehnt", "Running, plan paused": "Läuft, Plan pausiert", "Plan paused": "Plan pausiert", "Running": "Läuft", "Idle": "Untätig",
    # ---- limits and status panel
    "resets %s": "Rücksetzung %s", "new window": "neues Fenster", "no medium model": "kein Medium-Modell",
    "Agent slots of %s: %d of %d in use. A filled cell is a running agent, an empty cell a free slot.": "Agent-Slots von %s: %d von %d belegt. Eine volle Wabe ist ein laufender Agent, eine leere ein freier Slot.",
    "NEXT": "NEXT", "Quota reserve": "Quota-Reserve", "no limit": "kein Limit", "no weekly limit": "kein Wochenlimit", "no slots": "keine Slots", "%d running": "%d laufen",
    "reset now": "Reset jetzt", "unknown": "unbekannt", "reset unknown": "Reset unbekannt", "reset %s": "Reset %s", "7d unknown": "7d unbekannt", "5h new window": "5h neues Fenster", "5h reset now": "5h Reset jetzt",
    "%d %% more quota left than time until the reset (%s)": "%d %% mehr Quota übrig als Zeit bis zum Reset (%s)",
    "%d %% less quota left than time until the reset (%s)": "%d %% weniger Quota übrig als Zeit bis zum Reset (%s)",
    "%s  today %s  plans %s  total %s": "%s  heute %s  Pläne %s  gesamt %s",
    "no new agents": "keine neuen Agenten", "  Project: %s": "  Projekt: %s", "Orchestrator %s": "Orchestrator %s", "  (draft: %s)": "  (Entwurf: %s)",
    "STATE": "ZUSTAND", "USAGE": "NUTZUNG", "PLANS": "PLÄNE", "QUESTIONS": "FRAGEN", "ROUND ERRORS": "RUNDENFEHLER", "FAULTS": "FEHLER", "SELF-UPDATE": "SELBST-UPDATE",
    "%s %d / %d integrated (%d%%)": "%s %d / %d integriert (%d%%)", "open: %d  %s": "offen: %d  %s", "none open": "keine offen",
    "idle since %s": "untätig seit %s", "idle": "untätig", "self-update ": "Selbst-Update ", "ok": "ok", "rollback": "Rollback", " (back to %s)": " (zurück auf %s)",
    "Build jobs %s of %d cores (running)": "Build-Jobs %s von %d Kernen (läuft)", "Build jobs %d of %d cores (next build)": "Build-Jobs %d von %d Kernen (nächster Build)",
    " | per job %s": " | pro Job %s", "not measured yet": "noch nicht gemessen", " | active: %d cores": " | aktiv: %d Kerne",
    # ---- overlays
    "%s (now)": "%s (jetzt)", "model | in / out | fresh in | cached in": "Modell | ein / aus | frisch ein | Cache ein", "Project | in / out today": "Projekt | ein / aus heute",
    "Plan | in / out today | per finished package": "Plan | ein / aus heute | pro fertigem Paket", "Queen | Plan | run in / out": "Queen | Plan | Lauf ein / aus", "Agent | Plan | run in / out": "Agent | Plan | Lauf ein / aus",
    "%s | average %s over %d start today": "%s | Schnitt %s über %d Start heute", "%s | average %s over %d starts today": "%s | Schnitt %s über %d Starts heute",
    "  Project %s: %s | %s": "  Projekt %s: %s | %s", "  Plan %s (%s): %s": "  Plan %s (%s): %s",
    "  Queens: average %s%s": "  Queens: Schnitt %s%s", "  Agents: average %s%s": "  Agenten: Schnitt %s%s", " (parts of HaiveQueen approximate)": " (Anteile von HaiveQueen ungefähr)",
    "    CLI's own part %s": "    Eigenanteil der CLI %s", "role": "Rolle", "task": "Aufgabe", "rules %s": "Regeln %s",
    # ---- start context benchmark
    "no benchmark yet": "noch kein Benchmark", "no benchmark": "kein Benchmark", "not current, %s ago": "nicht aktuell, vor %s", "HaiveQueen %s, own profile %s, %+d %%": "HaiveQueen %s, eigenes Profil %s, %+d %%",
    "%s with the HaiveQueen profile": "%s mit dem HaiveQueen-Profil", "%s without it": "%s ohne es", "%s: no code folder": "%s: kein Code-Ordner",
    # ---- hives window
    "HaiveQueen Plans": "HaiveQueen Pläne", "Project": "Projekt", "Plan": "Plan", "Status": "Status", "Started": "Gestartet",
    # ---- questions window
    "Write an answer first.": "Schreibe zuerst eine Antwort.", "The answer says ok, but steps are marked as rejected.": "Die Antwort sagt ok, aber Schritte sind als abgelehnt markiert.",
    "HaiveQueen questions": "HaiveQueen Fragen", "Your answer": "Deine Antwort", "This answer applies to the whole Project": 'Diese Antwort gilt für das ganze Projekt',
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
    "Access type": "Zugangsart", "Subscription": "Abo", "Local LLM": "Lokales LLM",
    "How this provider reaches its models: with the login of a subscription, with an API key, or with a local LLM of your own.": "Wie dieser Anbieter seine Modelle erreicht: mit dem Login eines Abos, mit einem API-Schlüssel oder mit einem eigenen lokalen LLM.",
    "Where the local LLM answers, for example http://192.168.1.20:1234, without /v1.": "Wo das lokale LLM antwortet, zum Beispiel http://192.168.1.20:1234, ohne /v1.",
    "The key of the local LLM; optional, shown masked and kept only in config.toml of this machine.": "Der Schlüssel des lokalen LLM; optional, maskiert angezeigt und nur in der config.toml dieses Rechners gespeichert.",
    "Your API key; shown masked, kept only in config.toml of this machine and handed only to the agents HaiveQueen starts.": "Dein API-Schlüssel; maskiert angezeigt, nur in der config.toml dieses Rechners gespeichert und nur an die Agenten übergeben, die HaiveQueen startet.",
    "How many tokens the model of the local LLM can hold; empty means nothing is handed to the CLI.": "Wie viele Tokens das Modell des lokalen LLM fassen kann; leer heißt, der CLI wird nichts übergeben.",
    "the API key is missing": "der API-Schlüssel fehlt", "this CLI cannot run with an API key": "diese CLI läuft nicht mit einem API-Schlüssel",
    "the address of the Local LLM is missing": "die Adresse des lokalen LLM fehlt",
    "Not ready: %s": "Nicht bereit: %s", "Testing ...": "Teste ...", "Own settings of class %s": "Eigene Einstellungen der Klasse %s",
    "CLI": "CLI", "Models": "Modelle", "Use": "Nutzung", "Advanced": "Erweitert",
    "This login is shared by all providers with this CLI.": "Dieser Login wird von allen Anbietern mit dieser CLI gemeinsam genutzt.",
    "Name": "Name", "Protocol": "Protokoll", "Command": "Befehl", "Output format": "Ausgabeformat", "Login command": "Login-Befehl", "Login files": "Login-Dateien", "Profile variables": "Profil-Variablen",
    "Server": "Server", "Light": "Leicht", "Medium": "Mittel", "Heavy": "Schwer", "Benchmark": "Benchmark", "Simultaneous agents": "Gleichzeitige Agenten", "Priority": "Priorität",
    "Capabilities": "Fähigkeiten", "Agent mode": "Agent-Modus", "Subagent": "Subagent", "Process": "Prozess", "Allowed tools": "Erlaubte Tools", "Arguments": "Argumente", "Own settings": "Eigene Einstellungen",
    "Address": "Adresse", "API key": "API-Schlüssel", "Context window (tokens)": "Kontextfenster (Tokens)", "Test connection": "Verbindung testen",
    "How this provider is called in HaiveQueen; it follows the CLI and the access type until you change it yourself.": "So heißt dieser Anbieter in HaiveQueen; der Name folgt CLI und Zugangsart, bis du ihn selbst änderst.",
    "The program HaiveQueen starts for the agents of this provider.": "Das Programm, das HaiveQueen für die Agenten dieses Anbieters startet.",
    "How HaiveQueen talks to your own CLI and reads its output: claude, codex, opencode or kilo.": "Wie HaiveQueen mit deiner eigenen CLI spricht und ihre Ausgabe liest: claude, codex, opencode oder kilo.",
    "The command that starts your own CLI.": "Der Befehl, der deine eigene CLI startet.",
    "Which output format your own CLI writes: claude, codex or opencode.": "Welches Ausgabeformat deine eigene CLI schreibt: claude, codex oder opencode.",
    "The command that makes the login of your own CLI.": "Der Befehl, der den Login deiner eigenen CLI macht.",
    "The login files of your own CLI, relative to its profile folder, one per line.": "Die Login-Dateien deiner eigenen CLI, relativ zu ihrem Profilordner, eine pro Zeile.",
    "Variables that point your own CLI to its profile folder; {home} stands for that folder.": "Variablen, die deine eigene CLI auf ihren Profilordner zeigen lassen; {home} steht für diesen Ordner.",
    "The login of the HaiveQueen profile of this CLI; use the Login button when it is missing.": "Der Login im HaiveQueen-Profil dieser CLI; fehlt er, nimm den Login-Knopf.",
    "Model and effort for small tasks; leave the model empty and this provider does not offer this class.": "Modell und Aufwand für kleine Aufgaben; ohne Modell bietet dieser Anbieter diese Klasse nicht an.",
    "Model and effort for normal tasks; leave the model empty and this provider does not offer this class.": "Modell und Aufwand für normale Aufgaben; ohne Modell bietet dieser Anbieter diese Klasse nicht an.",
    "Model and effort for the hardest tasks; leave the model empty and this provider does not offer this class.": "Modell und Aufwand für die schwersten Aufgaben; ohne Modell bietet dieser Anbieter diese Klasse nicht an.",
    "Measures how much context an agent of this provider can use; only for a saved provider that is ready.": "Misst, wie viel Kontext ein Agent dieses Anbieters nutzen kann; nur für einen gespeicherten, bereiten Anbieter.",
    "How many agents of this provider may work at the same time.": "Wie viele Agenten dieses Anbieters gleichzeitig arbeiten dürfen.",
    "A preferred provider gets work first; a fallback provider only when the preferred ones are busy or not available.": "Ein bevorzugter Anbieter bekommt zuerst Arbeit; ein Ausweich-Anbieter nur, wenn die bevorzugten ausgelastet oder nicht verfügbar sind.",
    "What this provider may be used for; a task only goes to a provider that has the capability it needs.": "Wofür dieser Anbieter genutzt werden darf; eine Aufgabe geht nur an einen Anbieter, der die nötige Fähigkeit hat.",
    "Subagent: the Queen runs her Agents inside her own process on the same provider; Process: every Agent is a separate agent and goes to the provider with the best reserve.": "Subagent: die Queen führt ihre Agenten im eigenen Prozess auf demselben Anbieter aus; Process: jeder Agent ist ein eigener Agent und geht zum Anbieter mit der besten Reserve.",
    "The tools an agent may use without asking, one per line.": "Die Tools, die ein Agent ohne Nachfrage nutzen darf, eines pro Zeile.",
    "The arguments HaiveQueen gives to the CLI, one per line; {model}, {effort} and the like are filled in at the start.": "Die Argumente, die HaiveQueen der CLI mitgibt, eines pro Zeile; {model}, {effort} und Ähnliches werden beim Start eingesetzt.",
    "Other settings of this provider exactly as they stand in config.toml; a new setting is written as typed.": "Weitere Einstellungen dieses Anbieters genau wie in config.toml; eine neue Einstellung wird so geschrieben, wie sie getippt wird.",
    "Checks the address, reads the model list and tests the interface each CLI needs; the result is kept.": "Prüft die Adresse, liest die Modellliste und testet die Schnittstelle, die jede CLI braucht; das Ergebnis bleibt erhalten.",
    "provider %s: server %s does not exist": "Anbieter %s: der Server %s existiert nicht",
    "The server does not answer at %s": "Der Server antwortet nicht unter %s", "The server answers but refuses the API key": "Der Server antwortet, lehnt aber den API-Schlüssel ab",
    "%s: usable (%s)": "%s: nutzbar (%s)", "%s: not usable, the server does not offer %s": "%s: nicht nutzbar, der Server bietet %s nicht an",
    "Not tested yet": "Noch nicht getestet", "Tested at %s": "Getestet um %s",
    "the connection to the Local LLM %s has not been tested yet": "die Verbindung zum lokalen LLM %s wurde noch nicht getestet", "the Local LLM %s does not answer": "das lokale LLM %s antwortet nicht",
    "the Local LLM %s does not offer %s, which %s needs": "das lokale LLM %s bietet %s nicht an, das %s braucht", "the login is missing": "der Login fehlt", "model %s is not in the model list": "das Modell %s steht nicht in der Modellliste",
    "provider %s: use must be preferred, fallback or capability-only, not '%s'": "Anbieter %s: use muss preferred, fallback oder capability-only sein, nicht '%s'",
    "provider %s: beeMode must be subagent or process, not '%s'": "Anbieter %s: beeMode muss subagent oder process sein, nicht '%s'",
    "a number must be finite": "eine Zahl muss endlich sein", "cannot write %r as TOML": "%r lässt sich nicht als TOML schreiben", "a number is expected": "eine Zahl wird erwartet", "valid JSON is expected": "gültiges JSON wird erwartet",
    "true or false expected": "true oder false erwartet",
    "HaiveQueen settings": "HaiveQueen Einstellungen", "Providers": "Anbieter", "Projects": "Projekte", "Add Project": "Projekt hinzufügen", "%s: the workspace folder does not exist": "%s: der Arbeitsordner existiert nicht", "Add provider": "Anbieter hinzufügen", "Remove entry": "Eintrag entfernen",
    "Revert": "Verwerfen", "Open folder": "Ordner öffnen", "Changes apply to the running HaiveQueen without a restart. The old file is kept as <name>.bak.": "Änderungen gelten ohne Neustart für das laufende HaiveQueen. Die alte Datei bleibt als <name>.bak erhalten.",
    "Custom CLI": "Eigene CLI", "General": "Allgemein", "Build": "Build", "login": "Login", "Login": "Login", "Benchmark Start Context": "Startkontext messen",
    "Install /bzz": "/bzz installieren", "installed": "installiert", "skipped: literal /bzz is not supported": "übersprungen: direktes /bzz wird nicht unterstützt", "existing command was not replaced": "bestehender Befehl wurde nicht ersetzt", "skipped: command mechanism is unknown": "übersprungen: Befehlsmechanismus unbekannt",
    "Login present": "Login vorhanden", "Login missing": "Login fehlt",
    "No login is set up for this provider. For a CLI that is not built in, set the profile variables, the login command and the login files on this page.": "Für diesen Anbieter ist kein Login eingerichtet. Lege für eine nicht eingebaute CLI die Profilvariablen, den Login-Befehl und die Login-Dateien auf dieser Seite fest.",
    "Open a terminal window and run `%s` for provider %s in the HaiveQueen profile?": "Ein Terminalfenster öffnen und `%s` für Anbieter %s im HaiveQueen-Profil ausführen?",
    "The login window could not be opened (no terminal program found).": "Das Login-Fenster konnte nicht geöffnet werden (kein Terminalprogramm gefunden).",
    "Start context: no benchmark yet": "Startkontext: noch kein Benchmark",
    "Measure the start context of provider %s in every registered Project, twice: once with the HaiveQueen profile and once with your own configuration? This starts the CLI %d times and uses a few tokens of your login.": "Den Startkontext von Anbieter %s in jeder registrierten Projekt zweimal messen: einmal mit dem HaiveQueen-Profil und einmal mit deiner eigenen Konfiguration? Das startet die CLI %d-mal und verbraucht einige Tokens deines Logins.",
    "measuring …": "misst …", "Provider %s is excluded (excludedProviders): nothing was measured.": "Anbieter %s ist ausgeschlossen (excludedProviders): es wurde nichts gemessen.",
    "new setting": "neue Einstellung", "Remove this provider from the settings?": "Diesen Anbieter aus den Einstellungen entfernen?", "Remove this Project from the settings?": "Dieses Projekt aus den Einstellungen entfernen?", "Saved.": "Gespeichert.",
    # ---- owner questions the program files
    "Provider %s (protocol %s) cannot be kept to the HaiveQueen profile, so it would read private files. This package is stopped. Which provider may run it?": "Anbieter %s (Protokoll %s) lässt sich nicht auf das HaiveQueen-Profil beschränken und würde private Dateien lesen. Dieses Paket ist gestoppt. Welcher Anbieter darf es ausführen?",
    "Give the package to a provider that can be kept to the profile.": "Gib das Paket einem Anbieter, der sich auf das Profil beschränken lässt.",
    "The package remains unsolved at the highest available class. How should it continue?": "Das Paket bleibt auf der höchsten verfügbaren Klasse ungelöst. Wie soll es weitergehen?",
    "Review the failed attempts and choose the next approach.": "Prüfe die gescheiterten Versuche und wähle das weitere Vorgehen.",
    "No longer needed: the step is switched off or a provider can do it now.": "Nicht mehr nötig: Der Schritt ist ausgeschaltet oder ein Anbieter kann ihn jetzt ausführen.",
    "No configured provider offers the capability %s. How should this package continue?": "Kein eingerichteter Anbieter bietet die Fähigkeit %s. Wie soll dieses Paket weitergehen?",
    "No configured provider offers the required class or a higher class. How should this package continue?": "Kein eingerichteter Anbieter bietet die nötige oder eine höhere Klasse. Wie soll dieses Paket weitergehen?",
    "Configure an eligible provider.": "Richte einen geeigneten Anbieter ein.",
    "\n\nReport of the Queen:\n": "\n\nBericht der Queen:\n",
    "no final text (status %s)": "kein Abschlusstext (Status %s)",
    "Queen %s of this Plan ended without new work (no commit, no changed file, no question). Her last text: %s How should the Plan continue?": 'Queen %s dieses Plans endete ohne neue Arbeit (kein Commit, keine geänderte Datei, keine Frage). Ihr letzter Text: %s Wie soll der Plan weitermachen?',
    "Answer what blocks her, or say how she should count or decide it; then the next Queen starts.": "Antworte, was sie blockiert, oder sage, wie sie es zählen oder entscheiden soll; dann startet die nächste Queen.",
    # Owner decisions 741 and 742: the Apiary page with labels, explanations and the section Advanced.
    "Name": "Name",
    "Repository": "Repository",
    "Plans folder (empty: docs/plans of the repository)": "Plan-Ordner (leer: docs/plans des Repositorys)",
    "Colour": "Farbe",
    "Priority": "Priorität",
    "Owner name": "Name des Owners",
    "Language of owner texts (empty: General language)": "Sprache der Texte an den Owner (leer: allgemeine Sprache)",
    "Failed attempts before a stronger model (empty: General value)": "Fehlversuche bis zum stärkeren Modell (leer: allgemeiner Wert)",
    "Tools available to Claude agents": "Werkzeuge für Claude-Agenten",
    "Commands agents may run without asking, one per line.": "Befehle, die Agenten ohne Nachfrage ausführen dürfen, einer pro Zeile.",
    "Commands allowed without asking": "Befehle ohne Nachfrage erlauben",
    "The tools available to a Claude agent, separated by commas.": "Die Werkzeuge für einen Claude-Agenten, durch Kommas getrennt.",
    "Overrides the General value for this Project.": "Überschreibt den allgemeinen Wert für dieses Projekt.",
    "Commands agents of this Project may run without asking.": "Befehle, die Agenten dieses Projekts ohne Nachfrage ausführen dürfen.",
    "Interface language": "Sprache der Oberfläche",
    "Agent slots": "Agentenplätze",
    "Round length (minutes)": "Rundenlänge (Minuten)",
    "Failed attempts before a stronger model": "Fehlversuche bis zum stärkeren Modell",
    "No limit: this provider never runs out and gets work first.": "Kein Limit: dieser Provider geht nie aus und bekommt zuerst Arbeit.",
    "offline": "offline", "Offline since %s. HaiveQueen checks again and uses this provider as soon as it answers.": "Offline seit %s. HaiveQueen prüft erneut und nutzt diesen Provider, sobald er antwortet.",
    "Enabled": "Aktiviert", "A disabled provider gets no new work and shows nowhere in the dashboard; its settings stay.": "Ein deaktivierter Provider bekommt keine neue Arbeit und erscheint nirgends im Dashboard; seine Einstellungen bleiben.",
    "%s (disabled)": "%s (deaktiviert)",
    "The language of the HaiveQueen interface.": "Die Sprache der HaiveQueen-Oberfläche.",
    "The number of agents that may run at once.": "Die Anzahl der Agenten, die gleichzeitig laufen dürfen.",
    "The time between coordinator rounds in minutes.": "Die Zeit zwischen Koordinatorrunden in Minuten.",
    "After this many failed attempts of an Agent, HaiveQueen uses the next stronger model class.": "Nach so vielen Fehlversuchen eines Agenten nimmt HaiveQueen die nächste stärkere Modellklasse.",
    "Language of the owner texts": "Sprache der Texte an den Owner",
    "Plan files (patterns, one per line)": "Plan-Dateien (Muster, eines pro Zeile)",
    "Files that are not plans (patterns, one per line)": "Dateien, die keine Pläne sind (Muster, eines pro Zeile)",
    "Build output to ignore (one per line)": "Zu ignorierende Build-Ausgaben (eine pro Zeile)",
    "Build instructions document": "Dokument mit der Build-Anleitung",
    "Extra instructions for the integration": "Zusätzliche Anweisungen für die Integration",
    "Build program names (one per line)": "Namen der Build-Programme (einer pro Zeile)",
    "Compile check command": "Befehl des Compile-Checks",
    "Tools allowed in Claude Code": "In Claude Code erlaubte Werkzeuge",
    "Protected worktrees (one per line)": "Geschützte Worktrees (einer pro Zeile)",
    "Command after each event": "Befehl nach jedem Ereignis",
    "Command": "Befehl",
    "Working folder": "Arbeitsordner",
    "Time limit (seconds)": "Zeitlimit (Sekunden)",
    "Generated overviews": "Erzeugte Übersichten",
    "Queue overview file": "Datei der Warteschlangen-Übersicht",
    "Open questions file": "Datei der offenen Fragen",
    "Decisions file": "Datei der Entscheidungen",
    "Own prompt texts": "Eigene Prompt-Texte",
    "The name of the Project in HaiveQueen; new worktree names start with it.": "Der Name des Projekts in HaiveQueen; neue Worktree-Namen beginnen damit.",
    "The Git repository of the code; releases, version, branches and worktrees always use it.": "Das Git-Repository des Codes; Releases, Version, Branches und Worktrees verwenden immer dieses.",
    "Only for plans kept outside the repository; this folder also holds the settings of the Project.": "Nur für Pläne außerhalb des Repositorys; dieser Ordner enthält auch die Einstellungen des Projekts.",
    "The colour of the Project in the main window.": "Die Farbe des Projekts im Hauptfenster.",
    "Projects with a higher number get free places first.": "Projekte mit höherer Zahl bekommen freie Plätze zuerst.",
    "How the Queens and Agents call you in their texts.": "Wie Queens und Agenten dich in ihren Texten nennen.",
    "The language of the texts HaiveQueen writes to you for this Project.": "Die Sprache der Texte, die HaiveQueen dir zu diesem Projekt schreibt.",
    "Questions about these topics are never answered automatically.": "Fragen zu diesen Themen werden nie automatisch beantwortet.",
    "The question HaiveQueen asks you when a plan is integrated.": "Die Frage, die HaiveQueen dir stellt, wenn ein Plan integriert ist.",
    "Check that the code compiles and the tests pass before integration.": "Vor der Integration prüfen, dass der Code kompiliert und die Tests bestehen.",
    "Build the product after integration.": "Das Produkt nach der Integration bauen.",
    "Deploy the built product after integration.": "Das gebaute Produkt nach der Integration ausliefern.",
    "The branch finished plans are merged into.": "Der Branch, in den fertige Pläne gemergt werden.",
    "The Git remote HaiveQueen pushes to and fetches from.": "Das Git-Remote, zu dem HaiveQueen pusht und von dem es holt.",
    "The folder that holds the worktrees of the Agents; a relative path counts from the repository.": "Der Ordner mit den Worktrees der Agenten; ein relativer Pfad zählt ab dem Repository.",
    "Remove a worktree once its work is integrated and nothing is lost.": "Einen Worktree entfernen, sobald seine Arbeit integriert ist und nichts verloren geht.",
    "Remove plan branches once they are integrated and nothing is lost.": "Plan-Branches entfernen, sobald sie integriert sind und nichts verloren geht.",
    "Publish a GitHub release of the repository at each integration.": "Bei jeder Integration ein GitHub-Release des Repositorys veröffentlichen.",
    "The version of the next release, for example 2.1.0.": "Die Version des nächsten Releases, zum Beispiel 2.1.0.",
    "Where the plans are found, relative to the folder with the settings.": "Wo die Pläne liegen, relativ zum Ordner mit den Einstellungen.",
    "Files matching a plan pattern that are not plans.": "Dateien, die zu einem Plan-Muster passen, aber keine Pläne sind.",
    "Files the build writes that do not count as changes.": "Dateien, die der Build schreibt und die nicht als Änderung zählen.",
    "A document that describes how the product is built.": "Ein Dokument, das beschreibt, wie das Produkt gebaut wird.",
    "Text added to the instructions of the integrating Queen.": "Text, der den Anweisungen der integrierenden Queen angefügt wird.",
    "Running programs that show a build is still busy.": "Laufende Programme, die zeigen, dass ein Build noch arbeitet.",
    "The command that compiles the code and runs the tests.": "Der Befehl, der den Code kompiliert und die Tests ausführt.",
    "The tools Claude Code may use without asking, separated by commas.": "Die Werkzeuge, die Claude Code ohne Rückfrage nutzen darf, durch Kommas getrennt.",
    "Worktrees that are never removed automatically.": "Worktrees, die nie automatisch entfernt werden.",
    "A program started after each event, for example to sync files.": "Ein Programm, das nach jedem Ereignis startet, zum Beispiel zum Abgleichen von Dateien.",
    "The folder the command runs in.": "Der Ordner, in dem der Befehl läuft.",
    "The command is stopped after this many seconds.": "Der Befehl wird nach so vielen Sekunden gestoppt.",
    "A Markdown file that shows the queue.": "Eine Markdown-Datei, die die Warteschlange zeigt.",
    "A Markdown file that shows the open questions.": "Eine Markdown-Datei, die die offenen Fragen zeigt.",
    "A Markdown file that shows the decisions.": "Eine Markdown-Datei, die die Entscheidungen zeigt.",
    "Advanced": "Erweitert",
    "Repository: the folder of the Git repository is expected": "Repository: der Ordner des Git-Repositorys wird erwartet",
    "The settings of this Project are kept in %s: enter that folder as plans folder or, when the plans are in the repository, as repository": "Die Einstellungen dieses Projekts liegen in %s: diesen Ordner als Plan-Ordner eintragen oder, wenn die Pläne im Repository liegen, als Repository",
    "%s: the repository or plans folder does not exist": "%s: der Ordner des Repositorys oder der Pläne existiert nicht",
    "Instructions of the integrating Queen": "Anweisungen der integrierenden Queen",
    "Replaces the built-in instructions of the Queen that integrates a plan.": "Ersetzt die eingebauten Anweisungen der Queen, die einen Plan integriert.",
    "A further setting of this Project, as written in its settings file.": "Eine weitere Einstellung dieses Projekts, wie sie in seiner Einstellungsdatei steht."
}

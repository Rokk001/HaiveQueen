"""H-2 to H-5: GUI and desktop texts follow the machine language, agent instructions and protocol words stay English, dates follow the system."""
import ast
import json
import os
import re
import tempfile
import types
import unittest
from datetime import datetime
from pathlib import Path
from unittest import mock

from bzztest import RoundCase, ROOT
from haivequeen import bees, cli_presets, db, desktop, gui, gui_config, gui_data, gui_dialog, gui_hives, gui_overlays, gui_questions, hives, i18n, inbox, localtime, loop, overview, readygate, routine, startctx, views

PACKAGE = ROOT / "haivequeen"
GUI_FILES = ("gui.py", "gui_data.py", "gui_config.py", "gui_provider.py", "gui_dialog.py", "gui_hives.py", "gui_overlays.py", "gui_questions.py")
TRANSLATED_FILES = GUI_FILES + ("desktop.py", "overview.py", "buildjobs.py", "startctx.py", "bees.py", "hives.py", "agent_runner.py", "scheduler.py", "loop.py", "cli_presets.py")
LOG_CALLS = {"log": 0, "event": 0, "say": 0, "refuse": 2, "not_started": 2, "set_stalled": 2, "append_event": 2, "add_event": 3, "_transition": 4}   # calls that store a line: index of its text
NOT_LOG_TEXT = {"Test milestone 1: this is a long line that should wrap in a narrow window so that the line wrapping becomes visible.",   # only with the fake agent of the tests
                # the example cannot show the real line (a field holds a head, a word or a list); test_lines_built_from_a_variable and the round line test use real ones
                "x1, build process RAM peak=1 MB, use per job=1 MB (median 1 MB)", "x1, no build process seen, nothing stored", "question 1 waits for x1", "round done: 1 Queen(s) startedx1", 'Hive state "x1"x1'}
CONTROL = "[" + chr(0) + "-" + chr(8) + chr(11) + chr(12) + chr(14) + "-" + chr(31) + "]"
NOT_TEXT = {"bzz", "HaiveQueen", "Rokk001/HaiveQueen", "OK", "CPU", "RAM", "Disk", "cli", "v%s  |  bzz  |  "}   # names, the same in every language


def literal(node):
    """The string of a constant node, else None."""
    return node.value if isinstance(node, ast.Constant) and isinstance(node.value, str) else None


def wrapped_texts():
    """(file, text) of every literal handed to i18n.tr or i18n.N_ in the translated modules."""
    for name in TRANSLATED_FILES:
        for node in ast.walk(ast.parse((PACKAGE / name).read_text(encoding="utf-8"))):
            if isinstance(node, ast.Call) and getattr(node.func, "id", getattr(node.func, "attr", "")) in ("tr", "N_") and node.args and literal(node.args[0]) is not None:
                yield name, literal(node.args[0])


def has_words(text):
    """True when the text holds a word once format fields are removed."""
    return bool(re.search(r"[A-Za-zÄÖÜäöü]{2}", re.sub(r"%[-+0-9.]*[a-zA-Z]", "", text))) and text not in NOT_TEXT


def sample(node):
    """Example lines a text argument can produce: both sides of a condition, format fields filled and a piece for an added variable; none for a plain variable."""
    if isinstance(node, ast.IfExp):
        return sample(node.body) + sample(node.orelse)
    if isinstance(node, ast.BinOp):
        added = " x1" if isinstance(node.op, ast.Add) and literal(node.left) and literal(node.left).endswith(" ") else "x1" if isinstance(node.op, ast.Add) else ""
        return [text + added for text in sample(node.left)]
    text = literal(node)
    if text is None:
        return []
    text = re.sub(r"%0?\d*d", "1", text.replace("%%", "%"))
    return [re.sub(r"%g|%\.\d+f", "1.50", text).replace("%s", "x1")]


def stored_lines():
    """(file, line, example) of every literal line the program stores as an event or a live line, in all modules."""
    for path in PACKAGE.glob("*.py"):
        for node in ast.walk(ast.parse(path.read_text(encoding="utf-8"))):
            if isinstance(node, ast.Call):
                func = getattr(node.func, "attr", getattr(node.func, "id", ""))
                if func in LOG_CALLS and len(node.args) > LOG_CALLS[func]:
                    for text in sample(node.args[LOG_CALLS[func]]):
                        if has_words(text):
                            yield path.name, node.lineno, text


def worktree_reasons():
    """Every literal reason worktrees.py can report: returned by _candidate_reason, put under 'reason' or into `reason`, or raised as an exception (its text becomes the reason)."""
    tree = ast.parse((PACKAGE / "worktrees.py").read_text(encoding="utf-8"))
    found = []
    for node in ast.walk(tree):
        if isinstance(node, ast.FunctionDef) and node.name == "_candidate_reason":
            found += [item.value for item in ast.walk(node) if isinstance(item, ast.Return) and item.value is not None]
        elif isinstance(node, ast.Dict):
            found += [value for key, value in zip(node.keys, node.values) if literal(key) == "reason"]
        elif isinstance(node, ast.Assign) and getattr(node.targets[0], "id", "") == "reason":
            found.append(node.value)
        elif isinstance(node, ast.Raise) and isinstance(node.exc, ast.Call) and node.exc.args:
            found.append(node.exc.args[0])
    for value in found:
        value = value.values[-1] if isinstance(value, ast.BoolOp) else value
        while isinstance(value, ast.BinOp):
            value = value.left
        if literal(value):
            yield literal(value)


class CatalogTest(unittest.TestCase):
    def test_every_marked_text_has_a_german_entry_with_the_same_fields(self):
        missing, broken = [], []
        for name, text in wrapped_texts():
            if text not in i18n.GERMAN:
                missing.append((name, text))
            elif re.findall(r"%[-+0-9.]*[a-zA-Z%]", text) != re.findall(r"%[-+0-9.]*[a-zA-Z%]", i18n.GERMAN[text]):
                broken.append(text)
        self.assertEqual(missing, [])
        self.assertEqual(broken, [])

    def test_no_literal_goes_to_a_display_call_without_translation(self):
        """A plain English literal handed to a drawing call, a widget text, a window title or a message is a missed translation."""
        calls = {"text": 2, "title": 0, "inform": 0, "complain": 0, "confirm": 0, "ask_path": 1, "info": 2, "error": 2, "ask": 2, "panel": 4, "insert": 1}
        found = []
        for name in GUI_FILES:
            for node in ast.walk(ast.parse((PACKAGE / name).read_text(encoding="utf-8"))):
                if not isinstance(node, ast.Call):
                    continue
                func = getattr(node.func, "attr", getattr(node.func, "id", ""))
                args = list(node.args[calls[func]:calls[func] + 1]) if func in calls else []
                args += [kw.value for kw in node.keywords if kw.arg == "text"]
                for arg in args:
                    for part in ([arg] if not isinstance(arg, ast.BinOp) else [arg.left]):
                        text = literal(part)
                        if text and has_words(text) and text not in ("end", "1.0"):
                            found.append((name, node.lineno, text))
        self.assertEqual(found, [])

    def test_table_texts_are_in_the_catalog(self):
        tables = [label for _key, label, _text in gui_config.BUILD_FIELDS] + [text for _key, _label, text in gui_config.BUILD_FIELDS]
        tables += list(gui_hives.LABELS.values()) + list(gui_hives.ASK.values()) + list(gui_overlays.TITLES.values())
        self.assertEqual([text for text in tables if text not in i18n.GERMAN], [])


class LanguageChoiceTest(unittest.TestCase):
    def setUp(self):
        folder = tempfile.TemporaryDirectory()
        self.addCleanup(folder.cleanup)
        self.path = Path(folder.name) / "config.toml"
        patcher = mock.patch.dict(os.environ, {"BZZ_CONFIG_DIR": folder.name})
        patcher.start()
        self.addCleanup(patcher.stop)

    def write(self, text):
        self.path.write_text(text, encoding="utf-8")
        os.utime(self.path, (self.path.stat().st_atime, self.path.stat().st_mtime + len(text)))

    def test_machine_setting_selects_the_language_with_english_fallback(self):
        self.assertEqual(i18n.tr("Close"), "Close")   # no setting
        for value in ("German", "Deutsch", "de", "de-CH", "de_AT"):
            self.write('language = "%s"\n' % value)
            self.assertEqual(i18n.tr("Close"), "Schließen", value)
        for value in ("French", "en-US", ""):
            self.write('language = "%s"\n' % value)
            self.assertEqual(i18n.tr("Close"), "Close", value)

    def test_broken_settings_file_gives_english(self):
        self.write("language = [")
        self.assertEqual(i18n.tr("Close"), "Close")

    def test_explicit_language_wins_for_owner_texts_of_an_apiary(self):
        self.write('language = "English"\n')
        self.assertEqual(i18n.tr("Close", language="German"), "Schließen")
        self.write('language = "German"\n')
        self.assertEqual(i18n.tr("Close", language="English"), "Close")

    def test_unknown_text_and_fields_pass_through(self):
        self.write('language = "German"\n')
        self.assertEqual(i18n.tr("not in the catalog"), "not in the catalog")
        self.assertEqual(i18n.tr("Stop bee %s (%s)?", "Ada", "K1"), "Bee Ada (K1) stoppen?")

    def test_desktop_uses_the_machine_language_also_for_error_prefix_and_shortcut_texts(self):
        self.write('language = "German"\n')
        self.assertEqual(desktop.result_text("start", {"ok": True}), "HQ wurde gestartet.")
        self.assertEqual(desktop.result_text("start", {"ok": False, "error": "boom"}), "Fehler: boom")
        self.assertEqual(desktop.result_text("stop", {"ok": True, "running_agents": 2}), "Stopp angefordert. HQ wartet auf 2 laufende Agenten; sie arbeiten ununterbrochen weiter.")
        self.assertIn("Dashboard", desktop.result_text("start", {"ok": True, "running": True}))


class GermanTextsTest(unittest.TestCase):
    def setUp(self):
        patcher = mock.patch.object(i18n, "machine_language", return_value="German")
        patcher.start()
        self.addCleanup(patcher.stop)

    def test_hive_status_words(self):
        self.assertEqual(gui_data.status_text({"state": "queenless"}), "Ohne Queen")
        self.assertEqual(gui_data.status_text({"state": "answer", "pending_questions": [3]}), "Wartet auf Antwort (#3)")
        self.assertEqual(gui_data.blocker_text("Hive limit 2 of something"), "Hive-Limit 2 erreicht")
        self.assertEqual(gui_data.blocker_text("free RAM 1.00 GB is below the minimum 2.00 GB"), "Zu wenig freier RAM: 1,00 GB")
        self.assertEqual(gui_data.blocker_text("STOP"), "HaiveQueen gestoppt")
        self.assertEqual(gui_data.blocker_text("waiting for provider codex: quota used up"), "Wartet auf Anbieter codex: Kontingent aufgebraucht")
        self.assertEqual(gui_data.blocker_text("waiting for the build: x"), "Wartet auf den Build: x")

    def test_idle_kind_does_not_depend_on_the_language(self):
        row = gui_data.hive_row({"project": "A", "plan_path": "docs/plans/X-PLAN.md", "state": "other"}, [], {}, datetime(2026, 1, 1))
        self.assertEqual((row["status"], row["kind"]), ("Untätig", "idle"))

    def test_menus_keep_their_actions_and_translate_their_labels(self):
        self.assertEqual(gui.state_actions("STOPPED"), [("HaiveQueen starten", "start")])
        self.assertEqual(gui_hives.actions({"status": "DRAFT"})[0], ("Starten", "start"))
        self.assertEqual(gui.Dashboard.control_items({"paused": False})[0], ("Pausieren", "pause"))

    def test_overlay_lines(self):
        snap = {"overlays": {"queens": {"rows": []}, "apiaries": {"rows": [{"name": "A", "tin": 1, "tout": 2}]}}}
        self.assertEqual(gui_overlays.lines(snap, "queens"), [("QUEENS (jetzt)", "gold"), ("keine", "dim")])
        self.assertEqual(gui_overlays.lines(snap, "apiaries")[1][0], "Apiary | ein / aus heute")

    def test_benchmark_comparison(self):
        self.assertEqual(startctx.comparison(None, None), "noch kein Benchmark")
        self.assertIn("eigenes Profil", startctx.comparison(18000, 60000))

    def test_settings_errors(self):
        with self.assertRaises(ValueError) as caught:
            gui_config.apply_build({}, "x", "1", "2", cores=8)
        self.assertEqual(str(caught.exception), "CPU-Kerne pro Build: eine ganze Zahl wird erwartet")

    def test_typed_settings_errors(self):
        with self.assertRaises(ValueError) as caught:
            gui_config.parse_typed("x", 1)
        self.assertEqual(str(caught.exception), "eine Zahl wird erwartet")
        self.assertEqual(gui_questions.error_text(KeyError("question 4 is already answered")), "Frage 4 ist bereits beantwortet")

    def test_questions_window_errors(self):
        with self.assertRaises(ValueError) as caught:
            gui_questions.answer(None, 1, "  ")
        self.assertEqual(str(caught.exception), "Schreibe zuerst eine Antwort.")

    def test_program_log_lines_are_translated_on_display_and_noise_stays_recognised(self):
        event = {"ts": "2026-01-01T10:00:00", "agent": None, "project": "A", "kind": "rounds", "text": "round done: 0 Queen(s) started"}
        self.assertEqual(gui_data.log_line(event)[2], "Runde fertig: 0 Queen(s) gestartet")
        self.assertTrue(gui_data.is_noise(event))
        self.assertEqual(i18n.log_text("an agent note in any language"), "an agent note in any language")
        self.assertEqual(i18n.log_text("ROUND ERROR boom"), "RUNDENFEHLER boom")
        self.assertEqual(i18n.log_text("no free slot"), "kein freier Slot")
        self.assertEqual(i18n.log_text("restart: re-attached 3 running agent(s), 1 gone"), "Neustart: 3 laufende(r) Agent(en) wieder verbunden, 1 verschwunden")
        self.assertEqual(i18n.log_text("Provider codex needs its login in the HaiveQueen profile (once). Run `x y` with the environment A=b. Its packages wait until then, all others go on."),
                         "Anbieter codex braucht einmalig seinen Login im HaiveQueen-Profil. Führe `x y` mit der Umgebung A=b aus. Seine Pakete warten bis dahin, alle anderen laufen weiter.")

    def test_every_line_the_program_stores_has_a_german_form(self):
        """Scans the source: a new program log line without a pattern fails here, so the log cannot fall back to English unnoticed."""
        seen = list(stored_lines())
        self.assertGreater(len(seen), 40)   # the scan really finds the lines
        untranslated = [line for line in seen if line[2] not in NOT_LOG_TEXT and i18n.log_text(line[2]) == line[2]]
        self.assertEqual(untranslated, [])

    def test_lines_built_from_a_variable_have_a_german_form(self):
        """Lines the scan cannot see because the text is held in a variable first."""
        for line, german in (("dependency K1 of package K2 not done", "Abhängigkeit K1 von Paket K2 ist nicht fertig"),
                             ("dependency K1 of package K2 not in plan", "Abhängigkeit K1 von Paket K2 ist nicht im Plan"),
                             ("process gone after orchestrator restart", "Prozess nach Neustart der Steuerung verschwunden"),
                             ("build a: jobs=2, duration=00:01:02, no build process seen, nothing stored", "Build a: Jobs=2, Dauer=00:01:02, kein Build-Prozess gesehen, nichts gespeichert"),
                             ("build a: jobs=2, duration=00:01:02, build process RAM peak=900 MB, use per job=450 MB (median 400 MB)", "Build a: Jobs=2, Dauer=00:01:02, RAM-Spitze der Build-Prozesse=900 MB, Nutzung pro Job=450 MB (Median 400 MB)"),
                             ("TECHNICAL NOTE: Ada: runner process vanished (ended by executor or crashed), marked failed", "TECHNISCHER HINWEIS: Ada: Runner-Prozess verschwunden (von außen beendet oder abgestürzt), als fehlgeschlagen markiert"),
                             ("self-update ok: updated to abc123", "Selbst-Update ok: aktualisiert auf abc123"),
                             ("self-update rollback: boom; rolled back to abc123", "Selbst-Update rollback: boom; zurückgesetzt auf abc123"),
                             ("no result event, exit 3 some stderr", "kein Ergebnis-Ereignis, Exit 3 some stderr")):
            self.assertEqual(i18n.log_text(line), german)

    def test_round_line_translates_each_reason_behind_the_head(self):
        line = ('round done: 1 Queen(s) started; A-PLAN not started: Hive limit 2 reached; B-PLAN not started: waiting for provider codex: quota used up'
                '; C-PLAN not started: free RAM 1.50 GB is below the minimum 2.00 GB; D-PLAN not started: Hive state "resting"')
        self.assertEqual(i18n.log_text(line), 'Runde fertig: 1 Queen(s) gestartet; A-PLAN nicht gestartet: Hive-Limit 2 erreicht; B-PLAN nicht gestartet: Wartet auf Anbieter codex: Kontingent aufgebraucht'
                                              '; C-PLAN nicht gestartet: freier RAM 1,50 GB liegt unter dem Mindestwert 2,00 GB; D-PLAN nicht gestartet: Hive-Zustand resting')
        self.assertEqual(i18n.log_text('Hive state "answer" (questions 3, 4)'), "Hive-Zustand answer (Fragen 3, 4)")

    def test_round_line_keeps_a_reason_with_a_semicolon_together(self):
        line = "round done: 0 Queen(s) started; A-PLAN not started: worktree for branch a could not be prepared (git: one; two); B-PLAN not started: quota used up"
        self.assertEqual(i18n.log_text(line), "Runde fertig: 0 Queen(s) gestartet; A-PLAN nicht gestartet: Worktree für Branch a konnte nicht vorbereitet werden (git: one; two); B-PLAN nicht gestartet: Kontingent aufgebraucht")

    def test_worktree_cleanup_line_has_no_english_or_json_left(self):
        result = {"removed": ["C:\\wt\\a"], "kept": [{"path": "C:\\wt\\b", "reason": "protected worktree"}, {"path": "C:\\wt\\c", "reason": "ignored file: out/x.bin"}],
                  "notRemovable": [{"path": "C:\\wt\\d", "reason": "fatal: git\nsays no"}], "dryRun": False}
        self.assertEqual(i18n.log_text("Worktree cleanup: " + json.dumps(result, ensure_ascii=False)),
                         "Worktree-Bereinigung: entfernt: C:\\wt\\a; behalten: C:\\wt\\b (geschützter Worktree), C:\\wt\\c (ignorierte Datei: out/x.bin); nicht entfernbar: C:\\wt\\d (fatal: git says no)")
        self.assertEqual(i18n.log_text('Worktree cleanup: {"removed": [], "kept": [], "notRemovable": [], "dryRun": false}'), "Worktree-Bereinigung: nichts zu tun")
        self.assertEqual(i18n.log_text("Worktree cleanup: [1]"), "Worktree-Bereinigung: [1]")   # not a result: as written
        self.assertEqual(i18n.log_text("Worktree cleanup failed: worktree cleanup is busy"), "Worktree-Bereinigung fehlgeschlagen: Worktree-Bereinigung läuft bereits")
        self.assertEqual(i18n.log_text("Worktree cleanup failed: fatal: x"), "Worktree-Bereinigung fehlgeschlagen: fatal: x")

    def test_worktree_cleanup_line_stays_stored_in_english_and_noise_is_recognised_in_german(self):
        text = 'Worktree cleanup: {"removed": [], "kept": [{"path": "p", "reason": "locked or missing"}], "notRemovable": [], "dryRun": false}'
        event = {"ts": "2026-01-01T10:00:00", "agent": None, "project": "A", "kind": "rounds", "text": text}
        self.assertTrue(gui_data.is_noise(event))
        self.assertEqual(gui_data.log_line(event)[2], "Worktree-Bereinigung: behalten: p (gesperrt oder fehlt)")
        self.assertEqual(i18n.log_text(text, "English"), text)

    def test_every_worktree_reason_has_a_german_form(self):
        """Scans worktrees.py: a new reason without a German form fails here."""
        seen = set(worktree_reasons())
        self.assertGreater(len(seen), 25)   # the scan really finds the reasons
        self.assertEqual(sorted(reason for reason in seen if i18n._worktree_reason(reason) == reason), [])

    def test_words_of_agents_and_owner_stay_as_written_even_when_they_look_like_program_lines(self):
        line = "Ada started (the build)"
        self.assertEqual(i18n.log_text(line), "Ada gestartet (the build)")   # the pattern fits: only the source of the line can protect it
        for kind, agent in (("note", "Ada"), ("question", None), ("answer", None), ("done", "Ada"), ("ready", None)):
            event = {"ts": "2026-01-01T10:00:00", "agent": agent, "project": "A", "kind": kind, "text": line}
            self.assertEqual(gui_data.log_line(event)[2], line, kind)
        program = {"ts": "2026-01-01T10:00:00", "agent": None, "project": "A", "kind": "events", "text": line}
        self.assertEqual(gui_data.log_line(program)[2], "Ada gestartet (the build)")
        waits = {"ts": "2026-01-01T10:00:00", "agent": None, "project": "A", "kind": "note", "text": "question 4 waits for triage"}
        self.assertEqual(gui_data.log_line(waits)[2], "Frage 4 wartet auf die Vorprüfung")

    def test_live_view_translates_program_lines_and_keeps_the_words_of_the_agent(self):
        with tempfile.TemporaryDirectory() as folder:
            path = Path(folder) / "milestones.txt"
            path.write_text("\n".join(("White\t16:07 Stalled: no output for 5 s. The agent is still running.",
                                       "Yellow\t16:07 Stalled: no output for 5 s. The agent is still running.",
                                       "Cyan\t4:07 PM changed: src/a.py",
                                       "Green\t16:08 Commit: Fix - it (abc1234) - Exit 0: [main abc1234] Fix - it",
                                       "Green\t16:08 Compile test - OK: Ran 3 tests",
                                       "Red\t16:08 Push - Error: rejected",
                                       "Green\t16:09 Final message: Open requirements: 0",
                                       "Red\t16:09 End: failed (exit 1), total input 1.5M / output 12k.",
                                       "Red\t16:09 anything else",
                                       "no tab line")), encoding="utf-8")
            self.assertEqual([text for _colour, text in gui_data.live_lines(path)], [
                "16:07 Stalled: no output for 5 s. The agent is still running.",
                "16:07 Hängt: keine Ausgabe seit 5 s. Der Agent läuft noch.",
                "4:07 PM geändert: src/a.py",
                "16:08 Commit: Fix - it (abc1234) - Exit 0: [main abc1234] Fix - it",
                "16:08 Kompiliertest - OK: Ran 3 tests",
                "16:08 Push - Fehler: rejected",
                "16:09 Abschlussnachricht: Open requirements: 0",
                "16:09 Ende: fehlgeschlagen (Exit 1), gesamt Eingabe 1,5M / Ausgabe 12k.",
                "16:09 anything else",
                "no tab line"])

    def test_task_column_shows_the_last_program_line_in_german(self):
        row = gui_data.bee_row({"name": "Ada", "last_line": "queued", "status": "starting"}, datetime(2026, 1, 1))
        self.assertEqual(row["task"], "in der Warteschlange")

    def test_cli_choice_has_a_german_custom_entry_and_product_names_unchanged(self):
        self.assertEqual(cli_presets.labels(), ["Claude Code", "Codex CLI", "OpenCode", "Kilo Code", "Eigene CLI"])
        self.assertEqual(cli_presets.label_of("custom"), "Eigene CLI")
        self.assertEqual(cli_presets.label_of("codex"), "Codex CLI")
        self.assertEqual([cli_presets.key_of_label(label) for label in cli_presets.labels()], ["claude", "codex", "opencode", "kilo", "custom"])

    def test_numbers_get_a_decimal_comma(self):
        self.assertEqual(gui_data.fmt_tokens(1_500_000), "1,5M")
        self.assertEqual(overview.fmt_tok0(1_500_000), "1,5M")
        self.assertEqual(overview.fmt_tok0(2_500_000_000), "2,5G")
        self.assertEqual(i18n.decimal("RAM 80% 25.2/32 GB"), "RAM 80% 25,2/32 GB")
        self.assertEqual(i18n.decimal("v1.2 a.b"), "v1,2 a.b")   # only for formatted numbers: callers pass nothing else
        self.assertEqual(i18n.decimal("25.2", "English"), "25.2")

    def test_catalog_has_no_control_characters(self):
        self.assertNotRegex((PACKAGE / "i18n.py").read_text(encoding="utf-8"), CONTROL)

    def test_plan_status_words_stay_the_protocol_words(self):
        self.assertNotIn("DRAFT", i18n.GERMAN)
        self.assertNotIn("IN_PROGRESS", i18n.GERMAN)


class LogicIndependentOfLanguageTest(RoundCase):
    def test_dashboard_draws_german_only(self):
        """The whole main window in German, drawn on a real canvas: no text of the catalog stays English."""
        import tkinter as tk
        try:
            root = tk.Tk()
            root.attributes("-alpha", 0.0)   # fully transparent: the test window must not flash on the user's screen
        except tk.TclError:
            self.skipTest("no display")
        self.addCleanup(root.destroy)
        with mock.patch.object(i18n, "machine_language", return_value="German"), mock.patch.object(gui, "REFRESH_MS", 10 ** 9):
            root.geometry("1672x912")
            root.update()
            dash = gui.Dashboard(root, self.conn)
            dash.snap = gui_data.snapshot(self.conn, cfg={"slots": 2, "providers": [{"name": "claude"}]},
                                          sysinfo={"cores": 8, "cpu": 38, "ram_used_gb": 25.2, "ram_total_gb": 32.0, "ram_pct": 80, "disk_used_tb": 1.2, "disk_total_tb": 2.7, "disk_pct": 45})
            dash.draw()
            drawn = [dash.canvas.itemcget(item, "text") for item in dash.canvas.find_all() if dash.canvas.type(item) == "text"]
            for key in ("hives", "start"):
                dash.overlay_key = key
                dash.draw_overlay()
        changed = {english for english, german in i18n.GERMAN.items() if english != german and "%" not in english and len(english) > 2}
        self.assertEqual(sorted(set(drawn) & changed), [])
        self.assertIn("AKTIVE HIVES", drawn)
        self.assertIn("MODELL-LIMITS", drawn)
        self.assertIn("KI-AGENTEN-ORCHESTRATOR", drawn)

    def test_german_covers_hide_the_english_words_of_the_header_picture_at_every_window_size(self):
        """The picture scales by a coarser fraction than the layout (at some sizes about 12 % smaller): the covers must still lie over its English words."""
        import tkinter as tk
        try:
            root = tk.Tk()
            root.attributes("-alpha", 0.0)   # fully transparent: the test window must not flash on the user's screen
        except tk.TclError:
            self.skipTest("no display")
        self.addCleanup(root.destroy)
        with mock.patch.object(i18n, "machine_language", return_value="German"), mock.patch.object(gui, "REFRESH_MS", 10 ** 9):
            dash = gui.Dashboard(root, self.conn)
            dash.snap = gui_data.snapshot(self.conn, cfg={"slots": 2, "providers": [{"name": "claude"}]},
                                          sysinfo={"cores": 8, "cpu": 38, "ram_used_gb": 25.2, "ram_total_gb": 32.0, "ram_pct": 80, "disk_used_tb": 1.2, "disk_total_tb": 2.7, "disk_pct": 45})
            for factor in (0.5, 0.6, 0.77, 0.9, 0.99, 1.0, 1.1):
                width, height = int(gui.DESIGN_W * factor), int(gui.DESIGN_H * factor)
                with mock.patch.object(dash.canvas, "winfo_width", return_value=width), mock.patch.object(dash.canvas, "winfo_height", return_value=height):
                    dash.draw()
                picture = dash.fraction[0] / dash.fraction[1]
                covers = [dash.canvas.coords(item) for item in dash.canvas.find_all() if dash.canvas.type(item) == "rectangle" and dash.canvas.itemcget(item, "fill") == "#02060a"]
                for left, top, right, bottom in ((340, 100, 795, 124), (1462, 32, 1630, 128)):   # the English words in pixels of the picture
                    self.assertTrue(any(c[0] <= left * picture and c[1] <= top * picture and c[2] >= right * picture and c[3] >= bottom * picture for c in covers), (factor, left, covers))

    def test_other_windows_show_german_only_and_no_text_is_cut(self):
        """Hives, questions and settings window built for real in German: no changed catalog text stays English, and no label or button is narrower than its text."""
        import tkinter as tk
        try:
            root = tk.Tk()
            root.attributes("-alpha", 0.0)   # fully transparent: the test window must not flash on the user's screen
        except tk.TclError:
            self.skipTest("no display")
        self.addCleanup(root.destroy)

        def widgets(widget):
            for child in widget.winfo_children():
                yield child
                yield from widgets(child)

        changed = {english for english, german in i18n.GERMAN.items() if english != german and "%" not in english and len(english) > 2}
        db.add_question(self.conn, "A", "docs/plans/X-PLAN.md", "item", "A question?", "A recommendation.")
        with tempfile.TemporaryDirectory() as folder, mock.patch.object(i18n, "machine_language", return_value="German"), mock.patch.dict(os.environ, {"BZZ_CONFIG_DIR": folder}):
            (Path(folder) / "config.toml").write_text('language = "German"\nslots = 2\n\n[[providers]]\nname = "codex"\nprotocol = "codex"\ncommand = "codex"\n', encoding="utf-8")
            windows = {"hives": gui_hives.HivesWindow(root, self.conn, "Consolas"), "questions": gui_questions.QuestionsWindow(root, self.conn), "settings": gui_config.ConfigWindow(root, self.conn, Path(folder))}
            settings = windows["settings"]
            root.update()
            form_right = settings.form.winfo_rootx() + settings.form.winfo_width()
            explanations = [w for w in widgets(settings.form) if w.winfo_class() == "Label" and w.cget("text") in [i18n.GERMAN[f[2]] for f in gui_config.BUILD_FIELDS]]
            self.assertEqual(len(explanations), 3)
            self.assertEqual([w.cget("text") for w in explanations if w.winfo_rootx() + w.winfo_reqwidth() > form_right], [])   # the whole sentence is visible
            settings.select(next(index for index, node in enumerate(settings.entries) if node[0] == "providers"))   # the page with the CLI choice and the Login row
            root.update()
            found, cut, provider_labels = [], [], []
            for name, window in windows.items():
                top = window.win
                found.append((name, top.title()))
                for widget in [top] + list(widgets(top)):
                    if widget.winfo_class() in ("Label", "Button", "Labelframe", "Menubutton", "Checkbutton") and widget.cget("text"):
                        text = widget.cget("text")
                        found.append((name, text))
                        if widget.winfo_ismapped() and widget.winfo_class() in ("Button", "Menubutton", "Checkbutton") and widget.winfo_width() < widget.winfo_reqwidth():
                            cut.append((name, text))
                    if widget.winfo_class() == "Menubutton" and name == "settings":
                        provider_labels.append(widget.cget("text"))
                    if widget.winfo_class() == "Treeview":
                        found.extend((name, widget.heading(column, "text")) for column in widget["columns"])
                    if widget.winfo_class() == "Listbox":
                        found.extend((name, item) for item in widget.get(0, "end"))
        self.assertEqual(sorted({entry for entry in found if entry[1] in changed}), [])
        self.assertEqual(cut, [])
        self.assertIn(("settings", "HaiveQueen Einstellungen"), found)
        self.assertIn(("settings", "Speichern"), found)
        self.assertIn(("questions", "Deine Antwort"), found)
        self.assertTrue(any(label.startswith("Codex CLI") for label in provider_labels), provider_labels)

    def test_german_dialog_yes_button_still_answers_true(self):
        import tkinter as tk
        try:
            root = tk.Tk()
            root.attributes("-alpha", 0.0)   # fully transparent: the test window must not flash on the user's screen
        except tk.TclError:
            self.skipTest("no display")
        self.addCleanup(root.destroy)
        pressed = {}

        def press():
            window = [w for w in root.winfo_children() if isinstance(w, tk.Toplevel)][0]
            found = []

            def walk(widget):
                for child in widget.winfo_children():
                    if isinstance(child, tk.Button):
                        found.append(child)
                    walk(child)
            walk(window)
            pressed["labels"] = [b.cget("text") for b in found]
            yes = next((b for b in found if b.cget("text") == "Ja"), None)
            if yes is None:
                window.destroy()   # a missing button must fail the test, not leave the modal dialog waiting
            else:
                yes.invoke()
        with mock.patch.object(i18n, "machine_language", return_value="German"):
            root.after(50, press)
            self.assertTrue(gui_dialog.ask(root, "T", "Text", "Consolas"))
        self.assertEqual(sorted(pressed["labels"]), ["Ja", "Nein"])


class DateTest(unittest.TestCase):
    NOW = datetime(2026, 3, 9, 16, 7, 45)

    def test_time_has_no_seconds_and_keeps_the_system_format(self):
        self.assertNotIn("45", localtime.time(self.NOW))
        self.assertIn("07", localtime.time(self.NOW))

    @unittest.skipUnless(os.name == "nt", "Windows regional API")
    def test_windows_uses_the_regional_short_date(self):
        import ctypes
        buffer = ctypes.create_unicode_buffer(80)
        self.assertTrue(ctypes.windll.kernel32.GetLocaleInfoEx(None, 0x1F, buffer, 80))   # LOCALE_SSHORTDATE
        pattern = buffer.value
        expected = pattern
        for token, value in (("yyyy", "2026"), ("yy", "26"), ("MM", "03"), ("M", "3"), ("dd", "09"), ("d", "9")):
            expected = expected.replace(token, value) if "ddd" not in pattern and "MMM" not in pattern else expected
        if "ddd" not in pattern and "MMM" not in pattern:
            self.assertEqual(localtime.date(self.NOW), expected.replace("'", ""))
        self.assertTrue(localtime.date_time(self.NOW).startswith(localtime.date(self.NOW)))

    def test_shown_stamps_use_the_system_format(self):
        self.assertEqual(gui_data.fmt_stamp("2026-03-09T16:07:45"), localtime.date_time(self.NOW))
        self.assertEqual(gui_data.fmt_stamp("broken"), "broken")
        self.assertEqual(gui_data.fmt_stamp(None), "")
        status = gui_data.status_text({"state": "resting", "reset_at": "2026-03-09T16:07:45"})
        self.assertEqual(status, "Resting until " + localtime.date_time(self.NOW))

    def test_views_show_the_system_format(self):
        conn = mock.Mock()
        row = {"id": 1, "plan_path": "p", "item": "i", "question": "q", "recommendation": "r", "created_at": "2026-03-09T16:07:45", "status": "open"}
        with mock.patch.object(db, "open_questions", return_value=[row]):
            self.assertIn(localtime.date_time(self.NOW), views.render_questions(conn, "A"))

    def test_no_shown_date_is_cut_from_an_iso_text_or_has_a_fixed_format(self):
        for name in GUI_FILES + ("agent_runner.py", "overview.py", "scheduler.py", "usage.py"):
            text = (PACKAGE / name).read_text(encoding="utf-8")
            self.assertNotRegex(text, r'strftime\("%H:%M|%d\.%m|replace\("T", " "\)', name)


class ProtocolTest(unittest.TestCase):
    def test_german_input_is_still_recognised(self):
        plan = "## Requirements\n- A-1: text\n"
        for word in ("done", "erledigt"):
            res = readygate.evaluate(plan, "## Completeness\n- A-1: %s | test_x\n" % word, [])
            self.assertEqual(res.blocking, [], word)
        for word in ("partial", "teilweise", "open"):
            self.assertTrue(readygate.evaluate(plan, "## Completeness\n- A-1: %s | test_x\n" % word, []).blocking, word)
        for text in ("- K1: partial", "- K1: teilweise"):
            self.assertRegex(text, r"(?im)^\s*[-*|].*(?:teilweise|\bpartial)")
        for reason in ("superseded", "ueberholt", "überholt"):
            self.assertIn(reason, inbox.__dict__.get("SUPERSEDED", ()))

    def test_no_german_instruction_is_left_for_agents(self):
        german = re.compile(r"Wurden alle|Offene Anforderungen|Prüfe nach|Pruefe nach|Arbeitsschritt|Übergabe|erledigt,", re.I)
        for path in list((ROOT / "roles").glob("*.md")) + [ROOT / "docs" / "AGENT-RULES.md", ROOT / "docs" / "S13-S14-INTERFACE.md", ROOT / "haivequeen" / "loop.py", ROOT / "haivequeen" / "bees.py"]:
            self.assertNotRegex(path.read_text(encoding="utf-8"), german, path.name)
        self.assertFalse(hasattr(routine, "HANDOFF_TAIL"))

    def test_desktop_start_refusal_follows_the_machine_language(self):
        args = types.SimpleNamespace(foreground=True, desktop=True)
        with mock.patch.object(i18n, "machine_language", return_value="English"), self.assertRaises(ValueError) as caught:
            loop.cmd_start(args, None)
        self.assertEqual(str(caught.exception), "The desktop message supports only the background start.")
        with mock.patch.object(i18n, "machine_language", return_value="German"), self.assertRaises(ValueError) as caught:
            loop.cmd_start(args, None)
        self.assertIn("Hintergrundstart", str(caught.exception))

    def test_handoff_instruction_is_english(self):
        text = (PACKAGE / "loop.py").read_text(encoding="utf-8")
        self.assertIn("After each completed step", text)

    def test_owner_questions_of_the_program_follow_the_apiary_language(self):
        env = types.SimpleNamespace(name="A", settings={"language": "German"})
        with mock.patch.object(db, "add_question", return_value=7) as add:
            hives.ask_about_end(None, env, "p", {"name": "Q", "status": "failed"}, "last")
        self.assertIn("ohne neue Arbeit", add.call_args[0][4])
        env.settings["language"] = "English"
        with mock.patch.object(db, "add_question", return_value=7) as add:
            hives.ask_about_end(None, env, "p", {"name": "Q", "status": "failed"}, "last")
        self.assertIn("without new work", add.call_args[0][4])

    def test_agent_instructions_stay_english_in_a_german_apiary(self):
        self.assertIn("Language: write all free text", bees.queen_instructions("medium", 3, 3, 1000, "German"))
        self.assertIn("in German", bees.queen_instructions("medium", 3, 3, 1000, "German"))
        self.assertIn("Were all tasks in the plan implemented?", bees.queen_instructions("medium", 3, 3, 1000, "German"))


class ViewsOverviewStillEnglishTest(unittest.TestCase):
    def test_english_default_is_unchanged(self):
        self.assertEqual(overview.window_usage({"fiveHour": {"utilization": 0.1, "resetsAt": 1}}, datetime(2026, 1, 1))["FiveHour"]["reset"], "new window")


if __name__ == "__main__":
    unittest.main()

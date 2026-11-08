"""Rework of the provider form: texts, the Saved message, the ready line after a test, no Custom CLI for a wrapper command,
providers without limit first, the switch Enabled, offline providers and the mouse wheel over comboboxes."""
import json
import os
import sys
import tempfile
import time
import unittest
from datetime import datetime, timedelta
from pathlib import Path
from unittest import mock

sys.path.insert(0, str(Path(__file__).resolve().parent))
import test_agent_runner as tar  # noqa: E402
import test_hives_h2 as h2  # noqa: E402
import test_k5_apiary_page as apiary_page  # noqa: E402
import test_provider_form as pf  # noqa: E402
import test_provider_page as base  # noqa: E402
import test_servers as srv  # noqa: E402
from haivequeen import bees, cli_presets, db, gui_config, gui_data, i18n, loop, offline, overview, runtime, servers  # noqa: E402
from haivequeen import scheduler as sch  # noqa: E402


def local(name="loc", address="http://127.0.0.1:9", **extra):
    """A Local LLM provider entry of the Claude Code CLI."""
    return srv.Base.entry(name, "claude", access="llm", address=address, **extra)


def lim(entry, source):
    """The entry with a usage check source."""
    entry["limit"] = {"source": source}
    return entry


# ---------------------------------------------------------------- items 1 to 4


class Texts(pf.FormCase):
    def test_item1_name_explanation_says_access_type(self):
        for language, wanted, unwanted in (("English", "access type", "model source"), ("German", "Zugangsart", "Modellquelle")):
            with self.subTest(language=language), mock.patch("haivequeen.i18n.machine_language", return_value=language):
                win = self.window()
                self.provider(win, "claude")
                self.assertIn(wanted, win.notes["name"])
                self.assertNotIn(unwanted, win.notes["name"])
                win.win.destroy()

    def test_item2_general_escalate_after_texts(self):
        for language in ("English", "German"):
            with self.subTest(language=language), mock.patch("haivequeen.i18n.machine_language", return_value=language):
                win = self.window()
                label, help_ = ("Failed attempts before a stronger model", "After this many failed attempts of a Bee, HaiveQueen uses the next stronger model class.")
                if language == "German":
                    label, help_ = ("Fehlversuche bis zum stärkeren Modell", "Nach so vielen Fehlversuchen einer Bee nimmt HaiveQueen die nächste stärkere Modellklasse.")
                texts = base.label_texts(win.form)
                self.assertIn(label, texts)
                self.assertIn(help_, texts)
                self.assertNotIn("Default escalation delay", texts)
                self.assertFalse([t for t in texts if "escalated to the owner" in t or "Eskalation" in t])
                win.win.destroy()
        for old in ("Default escalation delay", "Default delay before a question is escalated to the owner."):
            self.assertNotIn(old, i18n.GERMAN)
            self.assertNotIn(old, [str(v) for v in list(gui_config.GENERAL_LABELS.values()) + list(gui_config.GENERAL_HELP.values())])

    def test_item3_saved_shows_beside_the_save_button(self):
        win = self.window()
        win.save()
        self.assertEqual(win.bottom_note.cget("text"), "Saved.")
        self.assertEqual(str(win.bottom_note.cget("fg")), self.col_green())
        self.assertEqual(win.status.cget("text"), "")
        win.reload()
        self.assertIn("Changes apply", win.bottom_note.cget("text"))

    def col_green(self):
        from haivequeen.gui import COL
        return str(COL["green"])

    def test_item4_ready_line_refreshes_after_the_test_and_never_says_server(self):
        self.only()
        stub = srv.Stub(paths=srv.ALL)
        self.addCleanup(stub.close)
        win = self.window()
        self.provider(win, "x")
        win.choose_access("Local LLM")
        win.target()["address"] = stub.address
        win.reshow()
        win.refresh_state()
        self.assertIn("has not been tested", win.ready_label.cget("text"))
        win.test_button.invoke()
        for _ in range(200):
            win.win.update()
            if win.ready_label.cget("text") == "":
                break
            time.sleep(0.05)
        self.assertEqual(win.ready_label.cget("text"), "")
        win.target()["address"] = "http://127.0.0.1:9"   # nothing answers there
        win.reshow()
        win.test_button.invoke()
        for _ in range(200):
            win.win.update()
            if "does not answer" in win.ready_label.cget("text"):
                break
            time.sleep(0.05)
        self.assertIn("does not answer", win.ready_label.cget("text"))
        self.assertIn("Local LLM", win.ready_label.cget("text"))

    def test_item4_no_ready_message_uses_the_word_server(self):
        for language in ("English", "German"):
            with mock.patch("haivequeen.i18n.machine_language", return_value=language):
                entry = local(address="http://127.0.0.1:9")
                texts = [sch.get_providers({"providers": [entry]})[0]["notReady"]]
                servers._write_tests({"loc": {"time": "t", "fingerprint": servers.fingerprint(servers.server_of(entry)), "reachable": False, "status": None,
                                              "models": [], "interfaces": {}, "lines": []}})
                texts.append(sch.get_providers({"providers": [entry]})[0]["notReady"])
                servers._write_tests({"loc": {"time": "t", "fingerprint": servers.fingerprint(servers.server_of(entry)), "reachable": True, "status": 200,
                                              "models": [], "interfaces": {"claude": {"ok": False, "path": "/v1/messages"}}, "lines": []}})
                texts.append(sch.get_providers({"providers": [entry]})[0]["notReady"])
                for text in texts:
                    self.assertTrue(text, language)
                    self.assertNotIn("server", text.lower(), text)
                    self.assertNotIn("Server", text, text)

    def test_f7_dropped_a_wrapper_command_is_not_a_custom_cli(self):
        self.assertEqual(cli_presets.key_for({"protocol": "claude", "command": "claude-local.cmd"}), "claude")
        self.assertEqual(cli_presets.key_for({"protocol": "claude", "command": "claude", "outputFormat": "claude"}), cli_presets.CUSTOM)
        win = self.window()
        self.provider(win, "wrapped")
        self.assertEqual(win.cli_var.get(), "Claude Code")
        self.assertNotIn(("command",), self.paths(win))
        win.save()   # nothing changed: the command stays as written
        self.assertTrue([q for q in self.raw()["providers"] if q["name"] == "wrapped"][0]["command"].endswith("claude-local.cmd"))


# ---------------------------------------------------------------- item 5


class NoLimitFirst(srv.Base):
    def providers(self):
        return sch.get_providers({"providers": [
            lim(srv.Base.entry("claude", "claude", use="preferred"), "claude"),
            lim(srv.Base.entry("codex", "codex", use="preferred"), "codex"),
            srv.Base.entry("loc", "claude", access="llm", address="http://h:1", use="preferred", slots=1, limit={"source": "none"}),
        ]})

    LIMITS = {"claude": {"usedPercent": 0, "resetHours": 120}, "codex": {"usedPercent": 0, "resetHours": 126}}   # reserve +40 % and +33 %

    def pick(self, free, providers=None, **kw):
        p = sch.select_provider(providers or self.providers(), "medium", [], free, limits=self.LIMITS, **kw)
        return p["name"] if p else None

    def test_local_llm_with_free_slot_is_chosen_before_subscriptions_with_a_reserve(self):
        self.assertEqual(self.pick({"claude": 2, "codex": 2, "loc": 1}), "loc")

    def test_without_free_slot_class_model_or_capability_the_subscription_is_chosen(self):
        self.assertEqual(self.pick({"claude": 2, "codex": 2, "loc": 0}), "claude")
        providers = self.providers()
        providers[2]["classes"] = {"light": providers[2]["classes"]["light"]}
        self.assertEqual(self.pick({"claude": 2, "codex": 2, "loc": 1}, providers), "claude")
        providers = self.providers()
        providers[2]["capabilities"] = ["Queen"]
        self.assertEqual(self.pick({"claude": 2, "codex": 2, "loc": 1}, providers), "claude")

    def test_among_providers_without_limit_a_local_one_comes_first_then_list_order_and_fallback_stays_fallback(self):
        providers = sch.get_providers({"providers": [srv.Base.entry("a", "opencode"), srv.Base.entry("b", "claude", access="llm", address="http://h:1"),
                                                      lim(srv.Base.entry("c", "claude"), "claude")]})
        self.assertEqual(sch.select_provider(providers, "medium", [], {"a": 1, "b": 1, "c": 1}, limits=self.LIMITS)["name"], "b")   # b is local: equal reserves (+99 %) go to the local provider first
        providers = self.providers()
        providers[2]["use"] = "fallback"
        self.assertEqual(self.pick({"claude": 2, "codex": 2, "loc": 1}, providers), "claude")   # a fallback provider only when the preferred ones are busy
        self.assertEqual(self.pick({"claude": 0, "codex": 0, "loc": 1}, providers), "loc")


class PanelNoLimit(unittest.TestCase):
    def row(self, language="English"):
        import test_limits_panel as lp
        with mock.patch("haivequeen.i18n.machine_language", return_value=language):
            return gui_data.limit_rows({"provider_limits": [lp.prow(has_limit=False, used_pct=None, ratio=None, hours=None, reset_at=None)]})[0]

    def test_reserve_is_plus_99_green_with_hover_and_weekly_line_keeps_text(self):
        row = self.row()
        self.assertEqual((row["reserve"]["text"], row["reserve"]["level"]), ("+99 %", "ok"))
        self.assertEqual(row["reserve"]["hover"], "No limit: this provider never runs out and gets work first.")
        self.assertEqual(row["weekly"]["text"], "no limit")
        self.assertEqual(self.row("German")["reserve"]["hover"], "Kein Limit: dieser Provider geht nie aus und bekommt zuerst Arbeit.")


# ---------------------------------------------------------------- item 6


class Enabled(pf.FormCase):
    def test_switch_at_the_top_default_on_and_saved(self):
        for language, label in (("English", "Enabled"), ("German", "Aktiviert")):
            with self.subTest(language=language), mock.patch("haivequeen.i18n.machine_language", return_value=language):
                win = self.window()
                self.provider(win, "claude")
                texts = [t for t in base.label_texts(win.top) if t in (label, "Name", "Name")]
                self.assertEqual(texts[0], label)
                self.assertIn(("enabled",), self.paths(win))
                getter = {p: g for p, g, _l in win.getters}[("enabled",)]
                self.assertIs(getter(), True)
                win.win.destroy()
        for entry in sch.get_providers(gui_config.load_migrated(self.folder / "config.toml")):
            self.assertTrue(entry["enabled"])
        win = self.window()
        self.provider(win, "claude")
        win.target()["enabled"] = False
        win.save()
        self.assertIs(self.raw()["providers"][0]["enabled"], False)
        self.assertEqual(self.raw()["providers"][0]["slots"], 5)   # all values kept
        self.assertFalse(sch.get_providers(self.raw())[0]["enabled"])

    def test_list_keeps_a_disabled_provider_dimmed(self):
        text = (self.folder / "config.toml").read_text(encoding="utf-8").replace('name = "claude"', 'name = "claude"\nenabled = false', 1)
        (self.folder / "config.toml").write_text(text, encoding="utf-8")
        for language, mark in (("English", "(disabled)"), ("German", "(deaktiviert)")):
            with self.subTest(language=language), mock.patch("haivequeen.i18n.machine_language", return_value=language):
                win = self.window()
                win.pick_tab("providers")
                shown = list(win.nodes.get(0, "end"))
                self.assertEqual(shown[0], "claude " + mark)
                self.assertFalse(any(mark in s for s in shown[1:]))
                self.assertEqual(str(win.nodes.itemcget(0, "fg")), str(win.col["dim"]))
                win.win.destroy()


class StateBase(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        env = mock.patch.dict(os.environ, {"BZZ_DATA_DIR": str(Path(self.tmp.name) / "data"), "BZZ_CONFIG_DIR": str(Path(self.tmp.name) / "cfg")})
        env.start()
        self.addCleanup(env.stop)
        self.conn = db.connect(Path(self.tmp.name) / "o.db")
        self.addCleanup(self.conn.close)

    def settings(self):
        a = srv.Base.entry("a", "claude", slots=2)
        b = srv.Base.entry("b", "claude", slots=2, enabled=False)
        return {"providers": [a, b]}

    def env(self, settings=None):
        return sch.ProjectEnv(self.conn, "p", self.tmp.name, settings or self.settings())


class DisabledProvider(StateBase):
    def test_not_counted_for_slots_choice_and_capabilities(self):
        env = self.env()
        by = {p["name"]: p for p in env.providers}
        self.assertEqual((by["b"]["slots"], by["b"]["enabled"]), (0, False))
        self.assertEqual(sch.provider_free(env.providers, [])["b"], 0)
        free = {"a": 2, "b": 2}
        self.assertEqual(sch.select_provider(env.providers, "medium", [], free, limits={})["name"], "a")
        self.assertIsNone(sch.select_provider([by["b"]], "medium", [], free, limits={}))
        self.assertFalse(sch.capability_available([by["b"]], ["Coding"]))
        self.assertIsNone(bees.select_route(env.providers[1:], free, "a", "medium", [], [], True))

    def test_running_agents_of_a_disabled_provider_finish(self):
        env = self.env()
        runtime.upsert_agent(self.conn, "p", "Ada", {"provider": "b", "status": "running", "phase": "bee"})
        running = runtime.list_agents(self.conn, "p", "active")
        self.assertEqual(sch.provider_free(env.providers, running)["b"], 0)
        self.assertEqual(sch.get_provider(env.providers, "b")["name"], "b")   # still found by name, for its running agent

    def test_nowhere_in_the_dashboard(self):
        rows = overview.provider_lines(self.conn, self.settings(), [])
        self.assertEqual([r["name"] for r in rows], ["a"])
        self.assertEqual([r["name"] for r in gui_data.limit_rows({"provider_limits": rows})], ["A"])

    def test_no_usage_check_for_a_disabled_provider(self):
        machine = {"providers": [lim(srv.Base.entry("c", "claude"), "claude"), lim(srv.Base.entry("d", "claude", enabled=False), "claude")]}
        seen = []
        with mock.patch("haivequeen.usage.refresh_claude_usage", side_effect=lambda conn, providers: seen.append([p["name"] for p in providers])), \
                mock.patch("haivequeen.hives.schedule_round", return_value={}), mock.patch("haivequeen.profile.notify_missing"):
            loop.run_round(self.conn, machine, [], settle=0)
        self.assertEqual(seen, [["c"]])


# ---------------------------------------------------------------- item 7


class OfflineState(StateBase):
    def test_mark_clear_and_apply(self):
        now = datetime(2026, 1, 2, 10, 0, 0)
        offline.mark(self.conn, "a", "API Error: 500", now=now)
        offline.mark(self.conn, "a", "again", now=now + timedelta(minutes=3))   # the first time stays
        data = offline.read(self.conn)
        self.assertEqual(data["a"]["since"], now.isoformat(timespec="seconds"))
        env = self.env({"providers": [srv.Base.entry("a", "claude", slots=2), srv.Base.entry("b", "claude", slots=2)]})
        a = sch.get_provider(env.providers, "a")
        self.assertEqual((a["slots"], a["offline"]), (0, now.isoformat(timespec="seconds")))
        self.assertEqual(sch.select_provider(env.providers, "medium", [], {"a": 2, "b": 2}, limits={})["name"], "b")
        self.assertIsNone(bees.select_route([a], {"a": 2}, "a", "medium", [], [], True))
        offline.clear(self.conn, "a")
        offline.apply_state(env.providers, self.conn)   # the list of a running loop is refreshed in place
        self.assertEqual((a["slots"], a["offline"]), (2, ""))

    def test_panel_shows_offline_in_red_with_since(self):
        offline.mark(self.conn, "a", "x", now=datetime(2026, 1, 2, 10, 5, 0))
        cfg = {"providers": [srv.Base.entry("a", "claude", slots=2), srv.Base.entry("b", "claude", slots=2)]}
        rows = overview.provider_lines(self.conn, cfg, [])
        self.assertTrue(rows[0]["offline"])
        self.assertFalse(rows[1]["offline"])
        self.assertFalse(rows[0]["next"])
        shown = gui_data.limit_rows({"provider_limits": rows})
        self.assertEqual(shown[0]["slots"]["offline"], "offline")
        self.assertIn(rows[0]["offline"], shown[0]["slots"]["hover"])
        self.assertTrue(shown[0]["slots"]["hover"].startswith("Offline since"))
        self.assertFalse(shown[1]["slots"].get("offline"))
        with mock.patch("haivequeen.i18n.machine_language", return_value="German"):
            self.assertEqual(gui_data.limit_rows({"provider_limits": rows})[0]["slots"]["offline"], "offline")

    def test_recheck_interval_local_llm_and_cli(self):
        now = datetime(2026, 1, 2, 10, 0, 0)
        stub = srv.Stub(paths=srv.ALL)
        self.addCleanup(stub.close)
        providers = sch.get_providers({"providers": [srv.Base.entry("sub", "claude"), local("up", stub.address), local("down", "http://127.0.0.1:9")]})
        for name in ("sub", "up", "down"):
            offline.mark(self.conn, name, "x", now=now)
        with mock.patch.object(offline, "run_cli", return_value=True) as cli:
            self.assertEqual(offline.recheck(self.conn, providers, 5, now=now + timedelta(minutes=4)), [])   # not due yet
            cli.assert_not_called()
            back = offline.recheck(self.conn, providers, 5, now=now + timedelta(minutes=5))
        self.assertEqual(sorted(back), ["sub", "up"])   # the CLI answered, the Local LLM test passed; the closed address stays offline
        self.assertEqual(list(offline.read(self.conn)), ["down"])
        with mock.patch.object(servers, "run_test") as test:
            offline.recheck(self.conn, providers, 5, now=now + timedelta(minutes=6))   # checked a minute ago
            test.assert_not_called()

    def test_cli_failure_keeps_the_provider_offline_and_default_minutes(self):
        now = datetime(2026, 1, 2, 10, 0, 0)
        providers = sch.get_providers({"providers": [srv.Base.entry("sub", "claude")]})
        offline.mark(self.conn, "sub", "x", now=now)
        with mock.patch.object(offline, "run_cli", return_value=False):
            self.assertEqual(offline.recheck(self.conn, providers, 5, now=now + timedelta(minutes=9)), [])
        self.assertIn("sub", offline.read(self.conn))
        self.assertEqual(sch.build_settings({}, {})["providerRecheckMinutes"], 5)
        self.assertEqual(sch.build_settings({"providerRecheckMinutes": 2}, {"providerRecheckMinutes": 9})["providerRecheckMinutes"], 2)   # machine setting only

    def test_run_cli_succeeds_on_exit_code_zero(self):
        p = sch.get_providers({"providers": [srv.Base.entry("sub", "claude")]})[0]
        done = mock.Mock(returncode=0)
        with mock.patch("subprocess.run", return_value=done) as run:
            self.assertTrue(offline.run_cli(p))
        self.assertLessEqual(run.call_args.kwargs["timeout"], 120)
        with mock.patch("subprocess.run", return_value=mock.Mock(returncode=1)):
            self.assertFalse(offline.run_cli(p))
        with mock.patch("subprocess.run", side_effect=OSError("gone")):
            self.assertFalse(offline.run_cli(p))


class OfflineAssignment(h2.QueenIntegrationTests):
    pass


for _name in dir(h2.QueenIntegrationTests):   # only the test below runs here, not the tests of the base class again
    if _name.startswith("test_"):
        setattr(OfflineAssignment, _name, None)


class _Assignment(OfflineAssignment):
    def test_stored_assignment_to_an_offline_provider_is_dropped(self):
        from haivequeen import hives
        from bzztest import REL
        self.add_local_provider()
        self.plan('IN_PROGRESS', [{'id': 'P1', 'title': 'First', 'class': 'heavy'}])
        hives.create(self.conn, 'testproj', REL, 'claude', '123456')
        self.agent('queen', phase='queen', status='running', provider='claude', pid=42)
        bees.ensure_attempts(self.conn)
        self.conn.execute('INSERT INTO bee_assignments VALUES (?,?,?,?,?)', ('testproj', REL, 'P1', 'heavy', 'local'))
        offline.mark(self.conn, 'local', 'ECONNRESET')
        env = self.env()
        bee_wt = self.base / 'bee-wt'
        bee_wt.mkdir()
        with mock.patch('haivequeen.usage.limit_windows', return_value=[]), mock.patch('haivequeen.bees.valid_bee_worktree', return_value=True):
            started = bees.start_process(env, REL, 'P1', 'Ada', 'heavy', 'task', ['one'], [], [], cwd=str(bee_wt))
        self.assertTrue(started)
        self.assertNotEqual(started['provider'], 'local')   # the Bee runs on another provider
        self.assertNotEqual(self.conn.execute('SELECT provider FROM bee_assignments WHERE package=?', ('P1',)).fetchone()[0], 'local')


class OfflineFromRunner(tar.RunnerBase):
    def end_with(self, name, text, **kw):
        stream = json.dumps({"type": "result", "is_error": True, "result": text}) + "\n"
        self.prepare(name)
        self.run_runner(name, "claude", stream, exit_code=1, **kw)

    def test_5xx_and_connection_errors_set_the_provider_offline_a_rate_limit_does_not(self):
        self.end_with("R-1", "API Error: 429 rate limit reached")
        self.assertEqual(offline.read(self.conn), {})
        self.end_with("R-2", "API Error: 503 Service Unavailable")
        self.assertIn("claude", offline.read(self.conn))
        offline.clear(self.conn, "claude")
        self.end_with("R-3", "fetch failed: ECONNRESET")
        self.assertIn("claude", offline.read(self.conn))
        offline.clear(self.conn, "claude")
        self.end_with("R-4", "task failed")
        self.assertEqual(offline.read(self.conn), {})


# ---------------------------------------------------------------- item 8


class WheelOverComboboxes(pf.FormCase):
    def combos(self, win):
        return [w for w in base.descendants(win.form) if w.winfo_class() == "TCombobox"]

    def check(self, win, where):
        found = self.combos(win)
        win.win.update()
        for box in found:
            before = box.get()
            values = list(box.cget("values"))
            if len(values) > 1:
                box.set(values[1])
                before = box.get()
            with mock.patch.object(win.canvas, "yview_scroll") as scroll:
                box.event_generate("<MouseWheel>", delta=-120, x=2, y=2)
                box.event_generate("<MouseWheel>", delta=120, x=2, y=2)
                win.win.update()
            self.assertEqual(box.get(), before, where)
            self.assertTrue(scroll.called, where)   # the page scrolls instead
        return len(found)

    def test_every_combobox_of_every_tab_leaves_its_value_and_scrolls_the_page(self):
        win = self.window()
        total = self.check(win, "general")
        for tab in ("providers", "apiaries"):
            win.pick_tab(tab)
            for node in list(win.visible):
                win.select(win.entries.index(node))
                if node[0] == "providers":
                    for access in ("Subscription", "API key", "Local LLM"):
                        win.choose_access(access)
                        total += self.check(win, (node, access))
                else:
                    total += self.check(win, node)
        self.assertGreater(total, 0)


class WheelOverApiaryComboboxes(apiary_page.ApiaryPageCase):
    def test_apiary_pages(self):
        win = self.window("Plain")
        for box in [w for w in apiary_page.descendants(win.form) if w.winfo_class() == "TCombobox"]:
            before = box.get()
            box.event_generate("<MouseWheel>", delta=-120, x=2, y=2)
            win.win.update()
            self.assertEqual(box.get(), before)


class ApiaryTexts(apiary_page.ApiaryPageCase):
    def test_item2_apiary_field_and_help(self):
        for language in ("English", "German"):
            with mock.patch("haivequeen.i18n.machine_language", return_value=language):
                win = self.window("Plain")
                label, help_ = ("Failed attempts before a stronger model (empty: General value)", "Overrides the General value for this Apiary.")
                if language == "German":
                    label, help_ = ("Fehlversuche bis zum stärkeren Modell (leer: allgemeiner Wert)", "Überschreibt den allgemeinen Wert für diese Apiary.")
                texts = [w.cget("text") for w in apiary_page.descendants(win.form) if w.winfo_class() == "Label"]
                self.assertIn(label, texts)
                self.assertIn(help_, texts)
                self.assertNotIn("Escalate after (empty: General value)", texts)
                win.win.destroy()


if __name__ == "__main__":
    unittest.main()


class FoldKeepsTestResult(unittest.TestCase):
    def test_last_test_moves_to_provider_with_other_name(self):
        from haivequeen import servers
        entry = {"name": "lms", "address": "http://localhost:1234", "apiKey": ""}
        stored = {}
        stored["lms"] = {"fingerprint": servers.fingerprint(entry), "models": ["m1"], "reachable": True}
        with mock.patch.object(servers, "_read_tests", side_effect=lambda: dict(stored)), \
             mock.patch.object(servers, "_write_tests", side_effect=lambda d: stored.update(d)):
            out = servers.fold_servers({"servers": [entry], "providers": [{"name": "local", "server": "lms"}]})
        self.assertEqual(stored["local"]["models"], ["m1"])
        self.assertEqual(out["providers"][0]["address"], "http://localhost:1234")

"""Provider page: the sections, models, use, advanced, explanations, name proposal, ready line."""
import gc
import json
import os
import sys
import tempfile
import unittest
from pathlib import Path
from unittest import mock

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
import bzztest  # noqa: E402,F401  (isolates the data folders)
from haivequeen import cli_presets, db, gui_config, i18n, profile, servers  # noqa: E402

CONFIG = '''slots = 8

[[servers]]
name = "lms"
address = "http://192.168.1.20:1234"
apiKey = "secret-key"
contextWindow = 120000

[[providers]]
name = "claude"
protocol = "claude"
command = "claude"
args = ["--output-format", "stream-json", "--allowedTools", "Read", "--effort", "{effort}"]
slots = 5
use = "preferred"
limit = { "source" = "claude" }
[providers.classes]
light = { "model" = "claude-haiku-5-5", "effort" = "medium", "contextLimitTokens" = 100000 }
medium = { "model" = "claude-sonnet-5-5", "effort" = "medium" }

[[providers]]
name = "codex"
protocol = "codex"
command = "codex"
args = ["exec", "--json"]
slots = 1
use = "fallback"
server = "lms"
[providers.classes]
medium = { "model" = "qwen/qwen3-27b", "effort" = "medium" }

[[providers]]
name = "wrapped"
protocol = "claude"
command = "claude-local.cmd"
loginCommand = "claude auth login"
endpoint = { id = "x", name = "x", baseUrl = "http://192.168.1.20:1234" }
'''


def descendants(widget):
    for child in widget.winfo_children():
        yield child
        yield from descendants(child)


def label_texts(widget):
    return [w.cget("text") for w in descendants(widget) if w.winfo_class() == "Label"]


class PageCase(unittest.TestCase):
    def setUp(self):
        import tkinter as tk
        try:
            self.root = tk.Tk()
            self.root.attributes("-alpha", 0.0)
        except tk.TclError:
            self.skipTest("no display")

        class Quiet(tk.Toplevel):
            def __init__(self, *args, **kw):
                super().__init__(*args, **kw)
                self.attributes("-alpha", 0.0)

        patch = mock.patch("tkinter.Toplevel", Quiet)
        patch.start()
        self.addCleanup(patch.stop)
        self.tmp = tempfile.TemporaryDirectory()
        self.folder = Path(self.tmp.name)
        (self.folder / "config.toml").write_text(CONFIG, encoding="utf-8")
        env = mock.patch.dict(os.environ, {"BZZ_DATA_DIR": str(self.folder / "data"), "BZZ_CONFIG_DIR": str(self.folder)})
        env.start()
        self.addCleanup(env.stop)
        os.environ.pop(profile.NO_CHECK_ENV, None)
        self.conn = db.connect(self.folder / "o.db")

    def tearDown(self):
        self.root.destroy()
        gc.collect()   # Tk objects are freed in the main thread, never by the collector in a reading thread
        self.conn.close()
        self.tmp.cleanup()

    def window(self):
        return gui_config.ConfigWindow(self.root, self.conn, self.folder, wait=True)

    def provider(self, win, name):
        win.select(1 + [p["name"] for p in win.data["providers"]].index(name))

    def paths(self, win):
        return {path for path, _g, _l in win.getters}

    def keep_test(self, name="codex", models=("qwen/qwen3-27b", "other"), ok=True):
        server = servers.get_server(gui_config.load_migrated(self.folder / "config.toml"), name)
        iface = {c: {"ok": ok, "path": "/x"} for c in servers.CLI_KEYS}
        data = servers._read_tests()
        data[name] = {"time": "2026-01-01 10:00:00", "fingerprint": servers.fingerprint(server), "reachable": ok, "status": 200, "models": list(models),
                      "interfaces": iface, "lines": ["Claude Code: usable"]}
        servers._write_tests(data)


class FiveSections(PageCase):
    def test_sections_in_order_with_advanced_closed_at_every_opening(self):
        """CLI, model source, models, use, advanced (closed each time the page opens)."""
        win = self.window()
        self.provider(win, "claude")
        self.assertEqual([k for k, _w in win.sections], ["cli", "source", "models", "use", "advanced"])
        self.assertFalse(win.advanced_open)
        self.assertEqual(win.advanced_frame.winfo_manager(), "")
        win.toggle_advanced()
        self.assertTrue(win.advanced_open)
        self.assertNotEqual(win.advanced_frame.winfo_manager(), "")
        self.provider(win, "codex")
        self.provider(win, "claude")
        self.assertFalse(win.advanced_open)
        self.assertEqual(win.advanced_frame.winfo_manager(), "")

    def test_protocol_and_command_only_for_custom_cli(self):
        """Provider page check."""
        win = self.window()
        self.provider(win, "claude")
        self.assertFalse({("protocol",), ("command",)} & self.paths(win))
        win.choose_cli("Custom CLI")
        self.assertTrue({("protocol",), ("command",)} <= self.paths(win))
        self.provider(win, "wrapped")   # a command that is not a built-in CLI
        self.assertTrue({("protocol",), ("command",)} <= self.paths(win))

    def test_subscription_shows_login_and_shared_sentence(self):
        """Subscription shows the status, the Login button and the shared-login sentence."""
        win = self.window()
        self.provider(win, "claude")
        self.assertEqual(win.access_var.get(), "Subscription")
        self.assertIsNotNone(win.login_button)
        self.assertEqual(win.login_status.cget("text"), "Login missing")
        self.assertIn("This login is shared by all providers with this CLI.", label_texts(win.form))

    def test_local_llm_shows_address_fields_and_no_login(self):
        """A provider that used a server is a Local LLM provider: address, key, window, no status and no Login button."""
        win = self.window()
        self.provider(win, "codex")
        self.assertEqual(win.access_var.get(), "Local LLM")
        self.assertIsNone(win.login_button)
        self.assertIsNone(win.login_status)
        self.assertEqual(win.target()["address"], "http://192.168.1.20:1234")

    def test_switch_access_keeps_ticks_and_derives_limit_source(self):
        """Switching an existing provider keeps capabilities; the limit source follows the new access type."""
        win = self.window()
        self.provider(win, "claude")
        before = win.collect() and (list(win.target().get("capabilities") or ["-"]), dict(win.target()["limit"]))
        win.choose_access("Local LLM")
        self.assertEqual(win.target()["access"], "llm")
        self.assertEqual(win.target()["limit"], {"source": "none"})
        self.assertEqual(win.target().get("capabilities") or ["-"], before[0] if before[0] != ["-"] else ["Coding", "Queen"])
        win.choose_access("Subscription")
        self.assertNotIn("access", win.target())

    def test_custom_cli_is_subscription(self):
        """A command that is no built-in CLI is a Custom CLI with the access type Subscription."""
        win = self.window()
        self.provider(win, "wrapped")
        self.assertEqual((win.access_var.get(), win.cli_var.get()), ("Subscription", "Custom CLI"))

class Models(PageCase):
    def test_three_rows_with_cli_list_and_own_entry(self):
        """Three class rows with a model list (data file of the CLI) and an effort list."""
        win = self.window()
        self.provider(win, "claude")
        self.assertEqual(list(win.model_boxes), ["light", "medium", "heavy"])
        values = list(win.model_boxes["light"].cget("values"))
        self.assertIn("claude-haiku-5-5", values)
        self.assertIn("claude-opus-5-5", values)
        self.assertEqual(win.model_vars["light"].get(), "claude-haiku-5-5")
        self.assertEqual(win.model_vars["heavy"].get(), "")
        self.assertEqual(win.effort_values, ["low", "medium", "high", "xhigh", "max"])
        self.provider(win, "codex")
        self.assertIn("medium", win.effort_values)

    def test_models_come_from_the_data_file_of_the_data_folder_when_there_is_one(self):
        (self.folder / "data").mkdir(parents=True, exist_ok=True)
        (self.folder / "data" / "cli-models.json").write_text(json.dumps({"claude": {"models": ["my-model"], "efforts": ["low"]}}), encoding="utf-8")
        win = self.window()
        self.provider(win, "claude")
        self.assertEqual(list(win.model_boxes["light"].cget("values")), ["", "my-model"])
        self.assertEqual(win.effort_values, ["low"])

    def test_own_server_lists_its_models_and_only_own_entry_without_test(self):
        """With own server the model list is the list of the last test; without a result only the own entry."""
        win = self.window()
        self.provider(win, "codex")
        self.assertEqual(list(win.model_boxes["medium"].cget("values")), [""])
        self.keep_test()
        self.provider(win, "claude")
        self.provider(win, "codex")
        self.assertEqual(list(win.model_boxes["medium"].cget("values")), ["", "qwen/qwen3-27b", "other"])

    def test_empty_model_means_class_not_offered_and_own_entry_is_marked(self):
        """An empty model stays empty; a typed own model is marked customModel."""
        win = self.window()
        self.provider(win, "claude")
        win.model_vars["medium"].set("")
        win.model_vars["heavy"].set("my-own-model")
        win.save()
        saved = gui_config.load_raw(self.folder / "config.toml")["providers"][0]["classes"]
        self.assertEqual(saved["medium"]["model"], "")
        self.assertEqual(saved["heavy"]["model"], "my-own-model")
        self.assertTrue(saved["heavy"]["customModel"])
        self.assertNotIn("customModel", saved["light"])
        self.assertNotIn("heavy", [c for c, e in saved.items() if not e.get("model")])

    def test_custom_cli_offers_only_the_own_entry(self):
        """Free text for model and effort."""
        win = self.window()
        self.provider(win, "wrapped")
        self.assertEqual(win.model_boxes, {})
        self.assertEqual(list(win.model_entries), ["light", "medium", "heavy"])
        self.assertEqual(win.effort_values, [])

    def test_other_class_settings_are_in_advanced(self):
        """Provider page check."""
        win = self.window()
        self.provider(win, "claude")
        self.assertIn(("classes", "light", "contextLimitTokens"), self.paths(win))
        advanced = [w for w in descendants(win.advanced_frame) if w.winfo_class() == "Entry" and w.get() == "100000"]
        self.assertEqual(len(advanced), 1)
        self.assertNotIn("customModel", {p[-1] for p in self.paths(win)})


class UseAndAdvanced(PageCase):
    def test_use_section(self):
        """Provider page check."""
        win = self.window()
        self.provider(win, "claude")
        inside = {path for path, _g, _l in win.getters if win.in_frame(path, win.use_frame)}
        self.assertEqual(inside, {("slots",), ("use",), ("capabilities",), ("beeMode",), ("contextLimit",)})
        texts = label_texts(win.use_frame)
        self.assertIn("Simultaneous agents", texts)
        self.assertIn("Priority", texts)

    def test_advanced_holds_bee_mode_tools_args_usage_check_and_own_settings(self):
        """Provider page check."""
        win = self.window()
        self.provider(win, "claude")
        inside = {path for path, _g, _l in win.getters if win.in_frame(path, win.advanced_frame)}
        self.assertTrue({("allowedTools",), ("args",)} <= inside)
        self.assertNotIn(("beeMode",), inside)   # In Use
        self.assertNotIn(("limit", "source"), inside)   # No Usage check field
        win.select(1 + 2)   # the custom CLI with a kept endpoint
        inside = {path for path, _g, _l in win.getters if win.in_frame(path, win.advanced_frame)}
        self.assertIn(("endpoint", "baseUrl"), inside)
        self.assertIn(("loginCommand",), self.paths(win))

    def test_no_value_twice_and_no_raw_key_outside_advanced(self):
        """Provider page check."""
        win = self.window()
        raw = {"protocol", "command", "slots", "use", "beeMode", "allowedTools", "args", "limit", "classes", "capabilities", "server", "name", "cli", "login", "nameEdited"}
        for name in ("claude", "codex", "wrapped"):
            self.provider(win, name)
            outside = [w for w in descendants(win.form) if not str(w).startswith(str(win.advanced_frame))]
            texts = [w.cget("text") for w in outside if w.winfo_class() in ("Label", "Labelframe", "Menubutton", "Button")]
            self.assertFalse(raw & set(texts), (name, raw & set(texts)))
            limit = [w for w in win.rows[("contextLimit",)].winfo_children()]   # the limit may equal the window of a Local LLM
            values = [w.get() for w in outside if w.winfo_class() == "Entry" and w.get() and w not in limit]
            self.assertEqual(len(values), len(set(values)), (name, values))
            if name != "wrapped":
                self.assertNotIn("claude-local.cmd", values)

    def test_every_field_has_a_one_line_explanation_in_english_and_german(self):
        """Provider page check."""
        for language in ("English", "German"):
            with self.subTest(language=language), mock.patch("haivequeen.i18n.machine_language", return_value=language):
                win = self.window()
                for name in ("claude", "codex", "wrapped"):
                    self.provider(win, name)
                    self.assertTrue(win.notes)
                    for key, text in win.notes.items():
                        self.assertTrue(text and "\n" not in text, (name, key))
                    wanted = {"cli", "access", "light", "medium", "heavy", "slots", "use", "capabilities", "beeMode", "allowedTools", "args", "name"}
                    self.assertTrue(wanted <= set(win.notes), wanted - set(win.notes))
                    for key, text in win.notes.items():
                        source = win.note_sources[key]
                        self.assertEqual(text, i18n.GERMAN[source] if language == "German" else source, key)
                win.win.destroy()


class NameAndAdd(PageCase):
    def test_add_provider_proposes_name_from_cli_and_access_type_and_follows(self):
        """Provider page check."""
        win = self.window()
        win.add_provider()
        entry = win.target()
        self.assertEqual((entry["protocol"], entry["name"]), ("claude", "claude-2"))   # "claude" is taken
        self.assertFalse(entry["nameEdited"])
        win.choose_cli("OpenCode")
        self.assertEqual(win.target()["name"], "opencode")
        win.choose_cli("Codex CLI")
        self.assertEqual(win.target()["name"], "codex-2")   # "codex" is taken
        win.choose_access("Local LLM")
        self.assertEqual(win.target()["name"], "codex-llm")
        self.assertEqual(win.target()["limit"], {"source": "none"})   # Created with a Local LLM
        win.choose_access("Subscription")
        self.assertEqual(win.target()["name"], "codex-2")
        self.assertIn("codex-2", win.nodes.get(0, "end"))

    def test_edited_name_stops_following_and_survives_save_and_restart(self):
        """Provider page check."""
        win = self.window()
        win.add_provider()
        self.type_name(win, "claude-2", "mine")
        win.choose_cli("Codex CLI")
        self.assertEqual(win.target()["name"], "mine")
        self.assertTrue(win.target()["nameEdited"])
        win.save()
        again = self.window()
        again.select(len(again.data["providers"]))
        self.assertEqual(again.target()["name"], "mine")
        again.choose_cli("OpenCode")
        self.assertEqual(again.target()["name"], "mine")
        # an existing provider never changes its name by itself
        self.provider(again, "claude")
        again.choose_cli("Codex CLI")
        self.assertEqual(again.target()["name"], "claude")

    def type_name(self, win, old, new):
        for widget in descendants(win.form):
            if widget.winfo_class() == "Entry" and widget.get() == old:
                widget.delete(0, "end")
                widget.insert(0, new)
                win.collect()
                return
        self.fail("no name entry")

    def test_renaming_a_local_llm_provider_keeps_its_test_result(self):
        win = self.window()
        self.keep_test()
        self.provider(win, "codex")
        self.type_name(win, "codex", "studio")
        self.assertIsNotNone(servers.kept(servers.server_of(win.target())))
        win.save()
        saved = gui_config.load_raw(self.folder / "config.toml")
        self.assertEqual([p["name"] for p in saved["providers"]], ["claude", "studio", "wrapped"])


class ReadyLine(PageCase):
    def ready(self, win):
        win.refresh_state(wait=True)   # the state is read in a thread
        return win.ready_label.cget("text")

    def bench(self, win):
        win.refresh_state(wait=True)
        return str(win.benchmark_button.cget("state"))

    def test_missing_login_failed_test_and_missing_model(self):
        """One line Not ready: ..."""
        win = self.window()
        self.provider(win, "claude")
        self.assertEqual(self.ready(win), "Not ready: the login is missing")
        self.provider(win, "codex")
        self.assertIn("Not ready: ", self.ready(win))
        self.assertIn("codex", self.ready(win))
        self.keep_test(ok=False)
        self.provider(win, "claude")
        self.provider(win, "codex")
        self.assertIn("does not answer", self.ready(win))
        self.keep_test(models=("a", "b"))
        self.provider(win, "claude")
        self.provider(win, "codex")
        self.assertIn("qwen/qwen3-27b", self.ready(win))
        self.keep_test()
        self.provider(win, "claude")
        self.provider(win, "codex")
        self.assertEqual(self.ready(win), "")

    def test_account_login_model_missing_in_the_owner_list_and_shipped_list_never_blocks(self):
        """With the owner's list a model outside it is missing; the shipped list only fills the choice (a working provider is never stopped by it)."""
        with mock.patch.object(profile, "has_login", return_value=True):
            win = self.window()
            self.provider(win, "claude")
            win.target()["classes"]["light"]["model"] = "not-in-the-docs"
            win.refresh_state(wait=True)
            self.assertEqual(self.ready(win), "")
            (self.folder / "data").mkdir(parents=True, exist_ok=True)
            (self.folder / "data" / "cli-models.json").write_text(json.dumps({"claude": ["opus"]}), encoding="utf-8")
            win.refresh_state(wait=True)
            self.assertIn("not-in-the-docs", self.ready(win))

    def test_benchmark_button_needs_saved_and_ready(self):
        """Provider page check."""
        win = self.window()
        self.keep_test()
        self.provider(win, "codex")
        self.assertEqual(self.bench(win), "normal")
        win.model_vars["light"].set("other")
        win.collect()
        win.refresh_state(wait=True)
        self.assertEqual(self.bench(win), "disabled")   # changed, not saved
        win.save()
        self.assertEqual(self.bench(win), "normal")
        self.provider(win, "claude")
        self.assertEqual(self.bench(win), "disabled")   # no login
        win.add_provider()
        win.choose_access("Local LLM")
        self.assertEqual(self.bench(win), "disabled")   # not saved yet


R3_CONFIG = '''slots = 8

[[providers]]
name = "claude"
protocol = "claude"
command = "claude"
args = ["--output-format", "stream-json"]
slots = 5
use = "preferred"
beeMode = "subagent"
limit = { source = "claude", interval = 120 }
[providers.classes]
medium = { model = "claude-sonnet-5-5", effort = "medium" }

[[providers]]
name = "claude-api"
protocol = "claude"
command = "claude"
access = "api"
apiKey = "key-of-the-test"
slots = 2
beeMode = "subagent"
limit = { source = "claude", interval = 120 }
[providers.classes]
medium = { model = "claude-sonnet-5-5", effort = "medium" }

[[providers]]
name = "codex"
protocol = "codex"
command = "codex"
args = ["exec", "--json"]
slots = 1
beeMode = "subagent"
limit = { source = "claude" }
[providers.classes]
medium = { model = "gpt-6.1-terra", effort = "medium" }

[[providers]]
name = "lms"
protocol = "claude"
command = "claude"
access = "llm"
address = "http://192.168.1.20:1234"
slots = 1
beeMode = "subagent"
limit = { source = "claude" }
[providers.classes]
medium = { model = "qwen/qwen3-27b", effort = "medium" }

[[providers]]
name = "wrapped"
protocol = "claude"
command = "claude-local.cmd"
loginCommand = "claude auth login"
slots = 1
beeMode = "subagent"
limit = { source = "claude" }
'''


class BeeModeAndLimitSource(PageCase):
    """Agent mode in Use with both values, Subagent greyed where unsupported, no Usage check field, the limit source derived."""

    def setUp(self):
        super().setUp()
        (self.folder / "config.toml").write_text(R3_CONFIG, encoding="utf-8")

    def bee_menu(self, win):
        return win.bee_button.nametowidget(win.bee_button["menu"])

    def bee_labels(self, win):
        menu = self.bee_menu(win)
        return [menu.entrycget(i, "label") for i in range(menu.index("end") + 1)]

    def bee_states(self, win):
        menu = self.bee_menu(win)
        return [menu.entrycget(i, "state") for i in range(menu.index("end") + 1)]

    def bee_getter(self, win):
        return next(getter for path, getter, _l in win.getters if path == ("beeMode",))

    def saved(self):
        return {p["name"]: p for p in gui_config.load_migrated(self.folder / "config.toml")["providers"]}

    def test_bee_mode_is_in_use_with_both_values_and_one_sentence(self):
        """The choice sits in the section Use, always visible, with Subagent and Process and one sentence of explanation."""
        win = self.window()
        self.provider(win, "claude")
        inside_use = {path for path, _g, _l in win.getters if win.in_frame(path, win.use_frame)}
        inside_advanced = {path for path, _g, _l in win.getters if win.in_frame(path, win.advanced_frame)}
        self.assertIn(("beeMode",), inside_use)
        self.assertNotIn(("beeMode",), inside_advanced)
        self.assertEqual(self.bee_labels(win), ["Subagent", "Process"])
        self.assertEqual(self.bee_getter(win)(), "subagent")
        self.assertIn("Agent mode", label_texts(win.use_frame))
        note = win.notes["beeMode"]
        self.assertIn("Subagent", note)
        self.assertIn("Process", note)
        self.assertIn("inside her own process", note)
        self.assertIn("best reserve", note)
        self.assertNotIn("\n", note)

    def test_subagent_is_greyed_out_for_codex_custom_cli_and_local_llm(self):
        """Subagent cannot be chosen for a CLI without embedded Agents and for a Local LLM provider; it shows as Process there."""
        win = self.window()
        for name in ("codex", "wrapped", "lms"):
            with self.subTest(provider=name):
                self.provider(win, name)
                self.assertEqual(self.bee_states(win), ["disabled", "normal"])
                self.assertEqual(self.bee_getter(win)(), "process")
                self.assertEqual(win.bee_var.get(), "Process")
        for name in ("claude", "claude-api"):
            with self.subTest(provider=name):
                self.provider(win, name)
                self.assertEqual(self.bee_states(win), ["normal", "normal"])
                self.assertEqual(self.bee_getter(win)(), "subagent")

    def test_subagent_greys_out_when_the_cli_or_access_type_changes(self):
        """Switching the shown provider to Codex CLI or to Local LLM greys Subagent at once."""
        win = self.window()
        self.provider(win, "claude")
        win.choose_cli("Codex CLI")
        self.assertEqual(self.bee_states(win), ["disabled", "normal"])
        self.assertEqual(self.bee_getter(win)(), "process")
        win.choose_cli("Claude Code")
        win.choose_access("Local LLM")
        self.assertEqual(self.bee_states(win), ["disabled", "normal"])
        self.assertEqual(self.bee_getter(win)(), "process")

    def test_stored_subagent_of_codex_and_local_llm_is_process_before_any_save(self):
        """Such a provider with subagent in config.toml counts as Process from the first load (scheduler, before any save) and is shown so."""
        from haivequeen import scheduler as sch
        providers = {p["name"]: p for p in sch.get_providers(gui_config.load_migrated(self.folder / "config.toml"))}
        self.assertEqual(providers["codex"]["beeMode"], "process")
        self.assertEqual(providers["lms"]["beeMode"], "process")
        self.assertEqual(providers["wrapped"]["beeMode"], "process")
        self.assertEqual(providers["claude"]["beeMode"], "subagent")
        self.assertEqual(providers["claude-api"]["beeMode"], "subagent")

    def test_stored_subagent_of_codex_and_local_llm_is_saved_as_process(self):
        """Shown as Process, so a save writes process for them."""
        win = self.window()
        win.save()
        saved = self.saved()
        self.assertEqual(saved["codex"]["beeMode"], "process")
        self.assertEqual(saved["lms"]["beeMode"], "process")
        self.assertEqual(saved["claude"]["beeMode"], "subagent")

    def test_no_usage_check_field_on_the_provider_page(self):
        """The field Usage check is removed from every provider page."""
        win = self.window()
        for name in ("claude", "claude-api", "codex", "lms", "wrapped"):
            with self.subTest(provider=name):
                self.provider(win, name)
                self.assertNotIn("Usage check", label_texts(win.form))
                self.assertNotIn(("limit", "source"), self.paths(win))
                self.assertNotIn("limit", win.notes)

    def test_limit_source_is_derived_for_every_cli_and_access_type(self):
        """Claude Code with Subscription -> claude, Codex with Subscription -> codex, every other combination -> none."""
        cases = [
            ({"protocol": "claude"}, "claude"),
            ({"protocol": "claude", "access": "api"}, "none"),
            ({"protocol": "claude", "access": "llm"}, "none"),
            ({"protocol": "codex"}, "codex"),
            ({"protocol": "codex", "access": "api"}, "none"),
            ({"protocol": "codex", "access": "llm"}, "none"),
            ({"protocol": "opencode"}, "none"),
            ({"protocol": "kilo"}, "none"),
            ({"protocol": "claude", "command": "claude-local.cmd", "loginCommand": "claude auth login"}, "none"),
            ({"protocol": "codex", "profileEnv": {"CODEX_HOME": "{home}"}}, "none"),
        ]
        for entry, wanted in cases:
            with self.subTest(entry=entry):
                self.assertEqual(servers.limit_source(entry), wanted)
                stored = dict(entry, limit={"source": "claude" if wanted != "claude" else "codex"})
                self.assertEqual(servers.limit_source(stored), wanted)

    def test_old_limit_source_is_replaced_at_load_and_save_and_the_interval_is_kept(self):
        """An existing limit source is replaced by the derived one at the next load and save; the check interval stays in config.toml."""
        from haivequeen import scheduler as sch
        loaded = {p["name"]: p for p in sch.get_providers(gui_config.load_migrated(self.folder / "config.toml"))}
        self.assertEqual(loaded["claude-api"]["limit"], "none")
        self.assertEqual(loaded["claude"]["limit"], "claude")
        self.assertEqual(loaded["codex"]["limit"], "codex")
        win = self.window()
        self.provider(win, "claude-api")
        self.assertEqual(win.target()["limit"], {"source": "none", "interval": 120})
        win.save()
        saved = self.saved()
        self.assertEqual(saved["claude-api"]["limit"], {"source": "none", "interval": 120})
        self.assertEqual(saved["claude"]["limit"], {"source": "claude", "interval": 120})
        self.assertEqual(saved["codex"]["limit"], {"source": "codex"})
        self.assertEqual(saved["lms"]["limit"], {"source": "none"})


class CliDataFile(unittest.TestCase):
    def test_shipped_data_file_has_a_source_for_every_cli(self):
        """Every CLI model and effort value stands with its source (official documentation)."""
        data = json.loads((ROOT / "haivequeen" / "cli-models.json").read_text(encoding="utf-8"))
        self.assertEqual(set(data), {"claude", "codex", "opencode", "kilo"})
        for key, entry in data.items():
            self.assertTrue(entry["source"].startswith("https://"), key)
            self.assertIsInstance(entry["models"], list)
            self.assertIsInstance(entry["efforts"], list)
        self.assertEqual(cli_presets.effort_list("claude"), ["low", "medium", "high", "xhigh", "max"])
        self.assertIn("gpt-6.1-sol", cli_presets.model_list("codex"))
        self.assertEqual(cli_presets.model_list("nonesuch"), [])


if __name__ == "__main__":
    unittest.main()

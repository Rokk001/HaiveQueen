"""Provider page rework (HQ-PROVIDER-SETUP S2): the five sections, model source, models, use, advanced, explanations, name proposal, ready line, server page."""
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
        self.conn.close()
        self.tmp.cleanup()

    def window(self):
        return gui_config.ConfigWindow(self.root, self.conn, self.folder)

    def provider(self, win, name):
        win.select(1 + [p["name"] for p in win.data["providers"]].index(name))

    def paths(self, win):
        return {path for path, _g, _l in win.getters}

    def keep_test(self, name="lms", models=("qwen/qwen3-27b", "other"), ok=True):
        server = servers.get_server(gui_config.load_migrated(self.folder / "config.toml"), name)
        iface = {c: {"ok": ok, "path": "/x"} for c in servers.CLI_KEYS}
        data = servers._read_tests()
        data[name] = {"time": "2026-01-01 10:00:00", "fingerprint": servers.fingerprint(server), "reachable": ok, "status": 200, "models": list(models),
                      "interfaces": iface, "lines": ["Claude Code: usable"]}
        servers._write_tests(data)


class FiveSections(PageCase):
    def test_sections_in_order_with_advanced_closed_at_every_opening(self):
        """P-5, P-10: CLI, model source, models, use, advanced (closed each time the page opens)."""
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
        """P-6."""
        win = self.window()
        self.provider(win, "claude")
        self.assertFalse({("protocol",), ("command",)} & self.paths(win))
        win.choose_cli("Custom CLI")
        self.assertTrue({("protocol",), ("command",)} <= self.paths(win))
        self.provider(win, "wrapped")   # a command that is not a built-in CLI
        self.assertTrue({("protocol",), ("command",)} <= self.paths(win))

    def test_account_login_shows_login_and_shared_sentence(self):
        """P-7: Account login shows the status, the Login button and the shared-login sentence."""
        win = self.window()
        self.provider(win, "claude")
        self.assertEqual(win.source_var.get(), "Account login")
        self.assertIsNotNone(win.login_button)
        self.assertEqual(win.login_status.cget("text"), "Login missing")
        self.assertIn("This login is shared by all providers with this CLI.", label_texts(win.form))

    def test_own_server_shows_server_choice_and_no_login(self):
        """P-7: Own server shows the server choice with New server, and neither status nor Login button."""
        win = self.window()
        self.provider(win, "codex")
        self.assertEqual(win.source_var.get(), "Own server")
        self.assertIsNone(win.login_button)
        self.assertIsNone(win.login_status)
        self.assertEqual(win.server_var.get(), "lms")
        self.assertEqual(win.server_values, ["lms", "New server"])
        win.choose_server("New server")
        self.assertEqual(win.node[0], "servers")   # the new server is set up on its page
        self.assertEqual(len(win.data["servers"]), 2)

    def test_switch_source_keeps_ticks_and_usage_check(self):
        """P-14, P-19 (g): switching an existing provider keeps capabilities and usage check."""
        win = self.window()
        self.provider(win, "claude")
        before = win.collect() and (list(win.target().get("capabilities") or ["-"]), dict(win.target()["limit"]))
        win.choose_source("Own server")
        self.assertEqual(win.target()["server"], "lms")
        self.assertEqual(win.target()["limit"], {"source": "claude"})
        self.assertEqual(win.target().get("capabilities") or ["-"], before[0] if before[0] != ["-"] else ["Coding", "Queen"])
        win.choose_source("Account login")
        self.assertNotIn("server", win.target())

    def test_custom_cli_offers_only_account_login_and_says_so(self):
        """P-7, P-19 (e)."""
        win = self.window()
        self.provider(win, "wrapped")
        self.assertEqual(win.source_values, ["Account login"])
        self.assertIn("A custom CLI takes only the account login: HaiveQueen does not know how it takes a server address.", label_texts(win.form))


class Models(PageCase):
    def test_three_rows_with_cli_list_and_own_entry(self):
        """P-8: three class rows with a model list (data file of the CLI) and an effort list."""
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
        """P-8: with own server the model list is the list of the last test; without a result only the own entry."""
        win = self.window()
        self.provider(win, "codex")
        self.assertEqual(list(win.model_boxes["medium"].cget("values")), [""])
        self.keep_test()
        self.provider(win, "claude")
        self.provider(win, "codex")
        self.assertEqual(list(win.model_boxes["medium"].cget("values")), ["", "qwen/qwen3-27b", "other"])

    def test_empty_model_means_class_not_offered_and_own_entry_is_marked(self):
        """P-8, P-19 (d): an empty model stays empty; a typed own model is marked customModel."""
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
        """P-19 (e): free text for model and effort."""
        win = self.window()
        self.provider(win, "wrapped")
        self.assertEqual(win.model_boxes, {})
        self.assertEqual(list(win.model_entries), ["light", "medium", "heavy"])
        self.assertEqual(win.effort_values, [])

    def test_other_class_settings_are_in_advanced(self):
        """P-19 (f)."""
        win = self.window()
        self.provider(win, "claude")
        self.assertIn(("classes", "light", "contextLimitTokens"), self.paths(win))
        advanced = [w for w in descendants(win.advanced_frame) if w.winfo_class() == "Entry" and w.get() == "100000"]
        self.assertEqual(len(advanced), 1)
        self.assertNotIn("customModel", {p[-1] for p in self.paths(win)})


class UseAndAdvanced(PageCase):
    def test_use_section(self):
        """P-9."""
        win = self.window()
        self.provider(win, "claude")
        inside = {path for path, _g, _l in win.getters if win.in_frame(path, win.use_frame)}
        self.assertEqual(inside, {("slots",), ("use",), ("capabilities",)})
        texts = label_texts(win.use_frame)
        self.assertIn("Simultaneous agents", texts)
        self.assertIn("Priority", texts)

    def test_advanced_holds_bee_mode_tools_args_usage_check_and_own_settings(self):
        """P-10, P-19 (i)."""
        win = self.window()
        self.provider(win, "claude")
        inside = {path for path, _g, _l in win.getters if win.in_frame(path, win.advanced_frame)}
        self.assertTrue({("beeMode",), ("allowedTools",), ("args",), ("limit", "source")} <= inside)
        win.select(1 + 2)   # the custom CLI with a kept endpoint
        inside = {path for path, _g, _l in win.getters if win.in_frame(path, win.advanced_frame)}
        self.assertIn(("endpoint", "baseUrl"), inside)
        self.assertIn(("loginCommand",), self.paths(win))

    def test_no_value_twice_and_no_raw_key_outside_advanced(self):
        """P-17."""
        win = self.window()
        raw = {"protocol", "command", "slots", "use", "beeMode", "allowedTools", "args", "limit", "classes", "capabilities", "server", "name", "cli", "login", "nameEdited"}
        for name in ("claude", "codex", "wrapped"):
            self.provider(win, name)
            outside = [w for w in descendants(win.form) if not str(w).startswith(str(win.advanced_frame))]
            texts = [w.cget("text") for w in outside if w.winfo_class() in ("Label", "Labelframe", "Menubutton", "Button")]
            self.assertFalse(raw & set(texts), (name, raw & set(texts)))
            values = [w.get() for w in outside if w.winfo_class() == "Entry" and w.get()]
            self.assertEqual(len(values), len(set(values)), (name, values))
            if name != "wrapped":
                self.assertNotIn("claude-local.cmd", values)

    def test_every_field_has_a_one_line_explanation_in_english_and_german(self):
        """P-11."""
        for language in ("English", "German"):
            with self.subTest(language=language), mock.patch("haivequeen.i18n.machine_language", return_value=language):
                win = self.window()
                for name in ("claude", "codex", "wrapped"):
                    self.provider(win, name)
                    self.assertTrue(win.notes)
                    for key, text in win.notes.items():
                        self.assertTrue(text and "\n" not in text, (name, key))
                    wanted = {"cli", "source", "light", "medium", "heavy", "slots", "use", "capabilities", "beeMode", "allowedTools", "args", "limit", "name"}
                    self.assertTrue(wanted <= set(win.notes), wanted - set(win.notes))
                    for key, text in win.notes.items():
                        source = win.note_sources[key]
                        self.assertEqual(text, i18n.GERMAN[source] if language == "German" else source, key)
                win.win.destroy()


class NameAndAdd(PageCase):
    def test_add_provider_proposes_name_from_cli_and_source_and_follows(self):
        """P-12."""
        win = self.window()
        win.add_provider()
        entry = win.target()
        self.assertEqual((entry["protocol"], entry["name"]), ("claude", "claude-2"))   # "claude" is taken
        self.assertFalse(entry["nameEdited"])
        win.choose_cli("OpenCode")
        self.assertEqual(win.target()["name"], "opencode")
        win.choose_cli("Codex CLI")
        self.assertEqual(win.target()["name"], "codex-2")   # "codex" is taken
        win.choose_source("Own server")
        self.assertEqual(win.target()["name"], "codex-lms")
        self.assertEqual(win.target()["limit"], {"source": "none"})   # P-14: created with an own server
        win.choose_source("Account login")
        self.assertEqual(win.target()["name"], "codex-2")
        self.assertIn("codex-2", win.nodes.get(0, "end"))

    def test_edited_name_stops_following_and_survives_save_and_restart(self):
        """P-12, P-19 (h)."""
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

    def test_server_rename_leaves_provider_names(self):
        """P-19 (h)."""
        win = self.window()
        win.select(win.entries.index(("servers", 0)))
        self.type_name(win, "lms", "studio")
        win.save()
        saved = gui_config.load_raw(self.folder / "config.toml")
        self.assertEqual(saved["servers"][0]["name"], "studio")
        self.assertEqual([p["name"] for p in saved["providers"]], ["claude", "codex", "wrapped"])
        self.assertEqual(saved["providers"][1]["server"], "studio")


class ReadyLine(PageCase):
    def ready(self, win):
        return win.ready_label.cget("text")

    def test_missing_login_failed_test_and_missing_model(self):
        """P-13: one line Not ready: ..."""
        win = self.window()
        self.provider(win, "claude")
        self.assertEqual(self.ready(win), "Not ready: the login is missing")
        self.provider(win, "codex")
        self.assertIn("Not ready: ", self.ready(win))
        self.assertIn("lms", self.ready(win))
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
        """P-13: with the owner's list a model outside it is missing; the shipped list only fills the choice (a working provider is never stopped by it)."""
        with mock.patch.object(profile, "has_login", return_value=True):
            win = self.window()
            self.provider(win, "claude")
            win.target()["classes"]["light"]["model"] = "not-in-the-docs"
            win.refresh_state()
            self.assertEqual(self.ready(win), "")
            (self.folder / "data").mkdir(parents=True, exist_ok=True)
            (self.folder / "data" / "cli-models.json").write_text(json.dumps({"claude": ["opus"]}), encoding="utf-8")
            win.refresh_state()
            self.assertIn("not-in-the-docs", self.ready(win))

    def test_benchmark_button_needs_saved_and_ready(self):
        """P-13."""
        win = self.window()
        self.keep_test()
        self.provider(win, "codex")
        self.assertEqual(str(win.benchmark_button.cget("state")), "normal")
        win.model_vars["light"].set("other")
        win.collect()
        win.refresh_state()
        self.assertEqual(str(win.benchmark_button.cget("state")), "disabled")   # changed, not saved
        win.save()
        self.assertEqual(str(win.benchmark_button.cget("state")), "normal")
        self.provider(win, "claude")
        self.assertEqual(str(win.benchmark_button.cget("state")), "disabled")   # no login
        win.add_provider()
        win.choose_source("Own server")
        self.assertEqual(str(win.benchmark_button.cget("state")), "disabled")   # not saved yet


class ServerPage(PageCase):
    def test_add_server_creates_a_page_with_fields_and_test_button(self):
        """P-2, P-11: Add server on the Servers tab."""
        win = self.window()
        win.pick_tab("servers")
        self.assertEqual(win.add_button.cget("text"), "Add server")
        win.add_entry()
        self.assertEqual(win.node[0], "servers")
        self.assertTrue({("name",), ("address",), ("apiKey",), ("contextWindow",)} <= self.paths(win))
        self.assertIsNotNone(win.test_button)
        self.assertEqual(win.test_button.cget("text"), "Test connection")
        self.assertTrue({"name", "address", "apiKey", "contextWindow"} <= set(win.notes))

    def test_key_is_masked_and_test_button_shows_the_result(self):
        win = self.window()
        win.select(win.entries.index(("servers", 0)))
        keys = [w for w in descendants(win.form) if w.winfo_class() == "Entry" and w.cget("show")]
        self.assertEqual(len(keys), 1)
        self.assertEqual(keys[0].get(), servers.MASK)
        self.assertNotIn("secret-key", [w.get() for w in descendants(win.form) if w.winfo_class() == "Entry"])
        self.assertEqual(win.result_label.cget("text"), "Not tested yet")
        result = {"time": "2026-01-01 10:00:00", "lines": ["Codex CLI: not usable, the server does not offer /v1/responses"]}

        def fake(server, **_k):
            self.assertEqual(server["apiKey"], "secret-key")   # the stored key, not the mask
            self.keep_test()
            return result
        with mock.patch.object(servers, "run_test", side_effect=fake):
            win.test_button.invoke()
            for _ in range(50):
                win.win.update()
                if "Tested at" in win.result_label.cget("text"):
                    break
                import time
                time.sleep(0.05)
        self.assertIn("Tested at 2026-01-01 10:00:00", win.result_label.cget("text"))
        win.save()
        self.assertEqual(gui_config.load_raw(self.folder / "config.toml")["servers"][0]["apiKey"], "secret-key")   # kept as it was

    def test_remove_used_server_is_refused_naming_the_providers(self):
        win = self.window()
        win.select(win.entries.index(("servers", 0)))
        with mock.patch("haivequeen.gui_dialog.ask", return_value=True), mock.patch("haivequeen.gui_dialog.info") as info:
            win.remove_node()
        self.assertEqual(len(win.data["servers"]), 1)
        self.assertIn("codex", str(info.call_args))


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

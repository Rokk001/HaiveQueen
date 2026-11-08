"""One provider form with the access type Subscription / API key / Local LLM."""
import os
import sys
import time
import unittest
from pathlib import Path
from unittest import mock

sys.path.insert(0, str(Path(__file__).resolve().parent))
import test_provider_page as base  # noqa: E402  (the window helpers of the provider page tests)
import test_servers as srv  # noqa: E402
from haivequeen import agent_runner, cli_presets, gui_config, gui_dialog, i18n, profile, servers  # noqa: E402
from haivequeen import platform as osplat  # noqa: E402
from haivequeen import scheduler as sch  # noqa: E402
from haivequeen.gui import COL  # noqa: E402

CONFIG = '''slots = 8

[[servers]]
name = "lms"
address = "http://192.168.1.20:1234"
apiKey = "secret-key"
contextWindow = 120000

[[servers]]
name = "spare"
address = "http://10.0.0.5:8080/v1"

[[providers]]
name = "claude"
protocol = "claude"
command = "claude"
args = ["--output-format", "stream-json", "--effort", "{effort}"]
slots = 5
use = "preferred"
limit = { "source" = "claude" }
[providers.classes]
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
name = "oc"
protocol = "opencode"
command = "opencode"
args = ["run", "--format", "json"]
slots = 2
server = "lms"

[[providers]]
name = "wrapped"
protocol = "claude"
command = "C:\\\\bin\\\\claude-local.cmd"
args = ["--x"]
slots = 1
'''
ONE = '''slots = 2

[[providers]]
name = "x"
protocol = "claude"
command = "claude"
slots = 1
'''


class FormCase(base.PageCase):
    def setUp(self):
        super().setUp()
        (self.folder / "config.toml").write_text(CONFIG, encoding="utf-8")
        gui_dialog.apply_style(self.root, "Consolas")

    def only(self):
        (self.folder / "config.toml").write_text(ONE, encoding="utf-8")

    def raw(self):
        return gui_config.load_raw(self.folder / "config.toml")

    def resolved(self, win, name):
        return {p["name"]: p for p in sch.get_providers(win.data)}[name]

    def keep_test(self, name, models=("m1", "m2"), iface=True):
        provider = next(p for p in gui_config.load_migrated(self.folder / "config.toml")["providers"] if p["name"] == name)
        data = servers._read_tests()
        data[name] = {"time": "2026-01-01 10:00:00", "fingerprint": servers.fingerprint(servers.server_of(provider)), "reachable": True, "status": 200,
                      "models": list(models), "interfaces": {c: {"ok": iface, "path": "/x"} for c in servers.CLI_KEYS}, "lines": ["Claude Code: usable"]}
        servers._write_tests(data)


class AccessType(FormCase):
    def test_f1_dropdown_under_the_name_with_three_values_and_no_steps(self):
        win = self.window()
        self.provider(win, "claude")
        self.assertEqual(win.access_values, ["Subscription", "API key", "Local LLM"])
        self.assertEqual(win.access_var.get(), "Subscription")
        self.assertEqual([t for t in base.label_texts(win.top) if t in ("Name", "Access type")], ["Name", "Access type"])
        self.assertTrue({("name",), ("slots",)} <= self.paths(win))
        self.assertFalse({("address",), ("apiKey",), ("contextWindow",)} & self.paths(win))
        win.choose_access("Local LLM")   # at once, no further step
        self.assertEqual(win.access_var.get(), "Local LLM")
        self.assertTrue({("address",), ("apiKey",), ("contextWindow",)} <= self.paths(win))
        self.assertIsNone(win.login_button)
        win.choose_access("API key")
        self.assertIn(("apiKey",), self.paths(win))
        self.assertFalse({("address",), ("contextWindow",)} & self.paths(win))
        self.assertIsNone(win.test_button)
        win.choose_access("Subscription")
        self.assertFalse({("address",), ("apiKey",), ("contextWindow",)} & self.paths(win))

    def test_f1_no_servers_tab_and_no_server_page(self):
        win = self.window()
        self.assertNotIn("servers", [k for k, _l in win.TABS])
        self.assertFalse([e for e in win.entries if e[0] == "servers"])
        self.assertFalse(hasattr(win, "add_server"))

    def test_f2_subscription_has_login_with_shared_sentence(self):
        win = self.window()
        self.provider(win, "claude")
        self.assertEqual(win.login_status.cget("text"), "Login missing")
        self.assertIsNotNone(win.login_button)
        self.assertIn("This login is shared by all providers with this CLI.", base.label_texts(win.form))
        self.assertEqual([k for k, _w in win.sections], ["cli", "source", "models", "use", "advanced"])

    def test_f3_api_key_is_masked_kept_in_config_and_handed_only_to_the_agent(self):
        self.only()
        win = self.window()
        self.provider(win, "x")
        win.choose_access("API key")
        keys = [w for w in base.descendants(win.form) if w.winfo_class() == "Entry" and w.cget("show")]
        self.assertEqual(len(keys), 1)
        keys[0].insert(0, "sk-one")
        win.save()
        self.assertEqual(self.raw()["providers"][0]["apiKey"], "sk-one")
        self.assertEqual(self.raw()["providers"][0]["access"], "api")
        win2 = self.window()
        self.provider(win2, "x")
        shown = [w.get() for w in base.descendants(win2.form) if w.winfo_class() == "Entry" and w.cget("show")]
        self.assertEqual(shown, [servers.MASK])
        p = {q["name"]: q for q in agent_runner.get_providers(self.raw())}["x"]
        home = Path(self.tmp.name) / "home"
        home.mkdir()
        with mock.patch.dict(os.environ, {"HOME": str(home), "USERPROFILE": str(home)}):
            os.environ.pop("ANTHROPIC_API_KEY", None)
            env = profile.environment(p)
            self.assertEqual(env["ANTHROPIC_API_KEY"], "sk-one")
            self.assertNotIn("ANTHROPIC_API_KEY", os.environ)   # never in the environment of HaiveQueen itself
        self.assertEqual(list(home.rglob("*")), [])   # no profile file is written

    def test_f3_codex_gets_its_key_variable_and_cli_without_key_support_is_not_offered(self):
        self.only()
        win = self.window()
        self.provider(win, "x")
        self.assertEqual(win.cli_values, ["Claude Code", "Codex CLI", "OpenCode", "Kilo Code", "Custom CLI"])
        win.choose_access("API key")
        self.assertEqual(win.cli_values, ["Claude Code", "Codex CLI"])
        self.assertEqual(servers.api_key_clis(), ["claude", "codex"])
        self.assertEqual(servers.environment({"access": "api", "protocol": "codex", "apiKey": "k"}), {"CODEX_API_KEY": "k"})
        self.assertEqual(servers.environment({"access": "api", "protocol": "opencode", "apiKey": "k"}), {})

    def test_f3_subscription_and_api_providers_of_one_cli_side_by_side(self):
        cfg = {"providers": [srv.Base.entry("sub", "claude"), srv.Base.entry("a1", "claude", access="api", apiKey="k1"),
                             srv.Base.entry("a2", "claude", access="api", apiKey="k2")]}
        ps = {p["name"]: p for p in sch.get_providers(cfg)}
        with mock.patch.dict(os.environ, {}):
            os.environ.pop("ANTHROPIC_API_KEY", None)
            self.assertNotIn("ANTHROPIC_API_KEY", profile.environment(ps["sub"]))
            self.assertEqual(profile.environment(ps["a1"])["ANTHROPIC_API_KEY"], "k1")
            self.assertEqual(profile.environment(ps["a2"])["ANTHROPIC_API_KEY"], "k2")
        self.assertEqual(profile.provider_home(ps["sub"]), profile.provider_home(ps["a1"]))   # one profile, the key only in the agent's environment
        self.assertFalse(ps["sub"]["login"])   # the login of the subscription is still needed there
        self.assertTrue(ps["a1"]["login"])   # an API key provider needs the key, not the login
        self.assertFalse(ps["a1"]["local"])

    def test_f3_api_models_are_the_cli_list_own_entry_counts_and_usage_check_none(self):
        self.only()
        win = self.window()
        win.add_provider()
        win.choose_access("API key")
        self.assertEqual(win.target()["limit"], {"source": "none"})
        models, _e, own_only = win.offered(win.target())
        self.assertEqual(models, cli_presets.model_list("claude"))
        self.assertFalse(own_only)
        entry = {"name": "a", "access": "api", "apiKey": "k", "protocol": "claude", "command": "claude",
                 "classes": {"medium": {"model": "my-own-model", "customModel": True}, "light": {"model": "nope"}}}
        with mock.patch("haivequeen.servers.known_models", return_value=["m1"]):
            ready = sch.get_providers({"providers": [entry]})[0]
            self.assertEqual(ready["notReady"], "model nope is not in the model list")
            del entry["classes"]["light"]
            self.assertEqual(sch.get_providers({"providers": [entry]})[0]["notReady"], "")

    def test_f4_local_llm_fields_test_connection_models_slots_and_one_cli(self):
        self.only()
        stub = srv.Stub(paths=srv.ALL)
        self.addCleanup(stub.close)
        win = self.window()
        self.provider(win, "x")
        win.choose_access("Local LLM")
        self.assertEqual(win.cli_values, ["Claude Code", "Codex CLI", "OpenCode", "Kilo Code"])
        self.assertTrue({("address",), ("apiKey",), ("contextWindow",), ("slots",)} <= self.paths(win))
        self.assertEqual(win.test_button.cget("text"), "Test connection")
        self.assertEqual(win.result_label.cget("text"), "Not tested yet")
        win.target()["address"] = stub.address
        win.reshow()
        win.test_button.invoke()
        for _ in range(200):
            win.win.update()
            if "Tested at" in win.result_label.cget("text"):
                break
            time.sleep(0.05)
        self.assertIn("Claude Code: usable", win.result_label.cget("text"))
        win.reshow()
        models, _e, _o = win.offered(win.target())
        self.assertEqual(models, ["qwen/qwen3-27b", "other-model"])   # the models of the last test
        self.assertEqual(list(win.model_boxes["medium"].cget("values"))[1:], ["qwen/qwen3-27b", "other-model"])

    def test_f4_cli_is_started_directly_with_the_address_without_script_on_all_platforms(self):
        seen = []
        for windows, linux in ((True, False), (False, True), (False, False)):
            with mock.patch.object(osplat, "is_windows", return_value=windows), mock.patch.object(osplat, "is_linux", return_value=linux):
                for cli in ("claude", "codex", "opencode", "kilo"):
                    cfg = {"providers": [srv.Base.entry("p", cli, access="llm", address="http://h:1/v1", apiKey="k", contextWindow=1000)]}
                    p = sch.get_providers(cfg)[0]
                    self.assertEqual(p["command"], cli)
                    self.assertFalse(p["command"].lower().endswith((".cmd", ".bat", ".sh", ".ps1")))
                    self.assertEqual(p["server"]["address"], "http://h:1")
                    seen.append((cli, servers.environment(p), servers.arguments(p)))
        self.assertTrue(any("ANTHROPIC_BASE_URL" in e for c, e, _a in seen if c == "claude"))
        self.assertTrue(any("model_providers.p.base_url=http://h:1/v1" in a for c, _e, a in seen if c == "codex"))
        by = {}
        for c, e, a in seen:
            by.setdefault(c, []).append((e, a))
        for c, runs in by.items():
            self.assertTrue(all(r == runs[0] for r in runs), c)

    def test_f4_ready_check_needs_a_test_that_shows_the_interface(self):
        self.only()
        win = self.window()
        self.provider(win, "x")
        win.choose_access("Local LLM")
        win.target()["address"] = "http://h:1"
        self.assertIn("has not been tested", self.resolved(win, "x")["notReady"])
        server = servers.server_of(win.target())
        data = servers._read_tests()
        data["x"] = {"time": "t", "fingerprint": servers.fingerprint(server), "reachable": True, "status": 200, "models": [],
                     "interfaces": {"claude": {"ok": False, "path": "/v1/messages"}}, "lines": []}
        servers._write_tests(data)
        self.assertIn("/v1/messages", self.resolved(win, "x")["notReady"])
        data["x"]["interfaces"]["claude"]["ok"] = True
        servers._write_tests(data)
        self.assertEqual(self.resolved(win, "x")["notReady"], "")

    def test_f5_servers_are_taken_over_into_each_provider_and_unused_one_becomes_a_provider(self):
        migrated = gui_config.load_migrated(self.folder / "config.toml")
        self.assertNotIn("servers", migrated)
        by = {p["name"]: p for p in migrated["providers"]}
        for name in ("codex", "oc"):
            self.assertEqual((by[name]["access"], by[name]["address"], by[name]["apiKey"], by[name]["contextWindow"]), ("llm", "http://192.168.1.20:1234", "secret-key", 120000))
            self.assertNotIn("server", by[name])
        spare = by["spare"]
        self.assertEqual((spare["access"], spare["slots"], spare["use"], spare["address"]), ("llm", 0, "fallback", "http://10.0.0.5:8080/v1"))
        self.assertEqual(sch.get_providers(migrated)[-1]["slots"], 0)
        self.assertEqual(servers.get_server(migrated, "spare")["address"], "http://10.0.0.5:8080")

    def test_f5_taken_name_gets_a_number_and_entries_are_removed_at_save(self):
        cfg = {"servers": [{"name": "claude", "address": "http://h:1"}], "providers": [srv.Base.entry("claude", "claude")]}
        self.assertEqual([p["name"] for p in servers.migrate(cfg)["providers"]], ["claude", "claude-2"])
        win = self.window()
        win.save()
        text = (self.folder / "config.toml").read_text(encoding="utf-8")
        self.assertNotIn("[[servers]]", text)
        self.assertNotIn('server = "lms"', text)
        raw = self.raw()
        self.assertNotIn("servers", raw)
        self.assertEqual({p["name"] for p in raw["providers"]}, {"claude", "codex", "oc", "wrapped", "spare"})
        self.assertEqual([p for p in raw["providers"] if p["name"] == "oc"][0]["apiKey"], "secret-key")
        self.assertIs(servers.migrate(raw), raw)   # nothing left to migrate

    def test_f6_switching_back_shows_the_earlier_values_and_save_writes_only_the_chosen_type(self):
        self.only()
        win = self.window()
        self.provider(win, "x")
        win.choose_access("Local LLM")
        win.target()["address"] = "http://h:1"
        win.target()["apiKey"] = "llm-key"
        win.choose_access("API key")
        self.assertNotIn("address", win.target())
        self.assertNotIn("apiKey", win.target())
        win.target()["apiKey"] = "api-key"
        win.choose_access("Local LLM")
        self.assertEqual((win.target()["address"], win.target()["apiKey"]), ("http://h:1", "llm-key"))
        win.choose_access("API key")
        self.assertEqual(win.target()["apiKey"], "api-key")
        win.choose_access("Subscription")
        win.save()
        entry = self.raw()["providers"][0]
        self.assertFalse({"address", "apiKey", "access", "contextWindow"} & set(entry))

    def test_f6_cli_use_and_advanced_are_shared_and_custom_only_for_subscription(self):
        win = self.window()
        self.provider(win, "oc")   # opencode, Local LLM
        win.choose_cli("Kilo Code")
        self.assertEqual(win.cli_values, ["Claude Code", "Codex CLI", "OpenCode", "Kilo Code"])
        self.assertEqual(win.target()["slots"], 2)
        win.choose_access("API key")   # Kilo Code cannot run with a key: the first offered CLI
        self.assertEqual((win.target()["protocol"], win.cli_var.get()), ("claude", "Claude Code"))
        self.assertEqual(win.target()["slots"], 2)
        win.choose_cli("Custom CLI")   # not offered for API key: nothing changes
        self.assertEqual(win.target()["protocol"], "claude")
        self.assertNotIn(("command",), self.paths(win))
        win.choose_access("Subscription")
        win.choose_cli("Custom CLI")
        self.assertIn(("command",), self.paths(win))

    def test_f8_add_provider_proposes_names_per_type(self):
        (self.folder / "config.toml").write_text(ONE.replace('name = "x"', 'name = "claude"'), encoding="utf-8")
        win = self.window()
        win.add_provider()
        e = win.target()
        self.assertEqual((e["protocol"], win.access_var.get(), e["name"]), ("claude", "Subscription", "claude-2"))
        self.assertFalse(e["nameEdited"])
        win.choose_cli("Codex CLI")
        self.assertEqual(win.target()["name"], "codex")
        win.choose_access("API key")
        self.assertEqual(win.target()["name"], "codex-api")
        win.choose_access("Local LLM")
        self.assertEqual(win.target()["name"], "codex-llm")
        win.choose_access("Subscription")
        win.choose_cli("Custom CLI")
        self.assertEqual(win.target()["name"], "custom")
        win.add_provider()
        win.choose_cli("Codex CLI")
        self.assertEqual(win.target()["name"], "codex")   # the first one is now "custom"
        win.add_provider()
        win.choose_cli("Codex CLI")
        self.assertEqual(win.target()["name"], "codex-2")   # number suffix for a taken name

    def test_f9_every_field_has_the_dark_style(self):
        from tkinter import ttk
        style = ttk.Style(self.root)
        self.assertEqual(style.lookup("TCombobox", "fieldbackground"), COL["track"])
        self.assertEqual(style.lookup("TCombobox", "foreground"), COL["text"])
        self.assertIn(COL["track"], [str(v) for state in style.map("TCombobox", "fieldbackground") for v in state])
        win = self.window()
        self.provider(win, "claude")
        self.assertTrue(win.model_boxes)
        for access in ("Subscription", "API key", "Local LLM"):
            win.choose_access(access)
            self.assertTrue(win.model_boxes)
            for widget in base.descendants(win.form):
                if widget.winfo_class() in ("Entry", "Text"):
                    self.assertEqual(str(widget.cget("background")), COL["track"], (access, widget))
                    self.assertEqual(str(widget.cget("foreground")), COL["text"])
                if widget.winfo_class() == "Menubutton":
                    self.assertEqual(str(widget.cget("background")), COL["track"])
            for box in win.model_boxes.values():
                self.assertEqual(box.winfo_class(), "TCombobox")

    def test_f10_explanations_in_both_languages_for_every_type(self):
        for language in ("English", "German"):
            with self.subTest(language=language), mock.patch("haivequeen.i18n.machine_language", return_value=language):
                win = self.window()
                self.provider(win, "claude")
                for access in ("Subscription", "API key", "Local LLM"):
                    win.choose_access(i18n.tr(access))
                    self.assertTrue({"name", "access", "cli", "light", "medium", "heavy", "slots", "use"} <= set(win.notes), access)
                    if access == "API key":
                        self.assertIn("apiKey", win.notes)
                    if access == "Local LLM":
                        self.assertTrue({"address", "apiKey", "contextWindow", "test"} <= set(win.notes))
                    for key, text in win.notes.items():
                        self.assertTrue(text and "\n" not in text, key)
                        source = win.note_sources[key]
                        self.assertEqual(text, i18n.GERMAN[source] if language == "German" else source, key)
                self.assertEqual(i18n.GERMAN["Access type"], "Zugangsart")
                win.win.destroy()

    def test_f10_ready_check_per_type(self):
        self.only()
        win = self.window()
        self.provider(win, "x")
        self.assertEqual(self.resolved(win, "x")["notReady"], "the login is missing")
        win.choose_access("API key")
        self.assertEqual(self.resolved(win, "x")["notReady"], "the API key is missing")
        win.target()["apiKey"] = "k"
        self.assertEqual(self.resolved(win, "x")["notReady"], "")
        win.choose_access("Local LLM")
        win.refresh_state()
        self.assertIn("Not ready:", win.ready_label.cget("text"))


if __name__ == "__main__":
    unittest.main()

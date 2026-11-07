"""Own LLM servers (HQ-PROVIDER-SETUP S1): server entries, handing a server to each built-in CLI, connection test, ready check, local rules and migration."""
import copy
import json
import os
import tempfile
import threading
import unittest
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from unittest import mock

import bzztest  # noqa: F401  (puts this checkout first on the import path, isolates the data folders)
from haivequeen import bees, config, gui_config, loop, profile, scheduler as sch, servers
from haivequeen import platform as osplat


class Stub:
    """A test server on a free local port; `paths` maps a request path to its status (a path that is not listed answers 404); /v1/models lists `models`."""

    def __init__(self, paths=None, models=("qwen/qwen3-27b", "other-model"), models_status=200):
        stub = self
        self.paths, self.models, self.models_status, self.hits = dict(paths or {}), list(models), models_status, []

        class Handler(BaseHTTPRequestHandler):
            def log_message(self, *a):
                pass

            def handle_any(self):
                length = int(self.headers.get("Content-Length") or 0)
                if length:
                    self.rfile.read(length)
                stub.hits.append((self.command, self.path, self.headers.get("Authorization")))
                if self.path == "/v1/models":
                    body, code = json.dumps({"data": [{"id": m} for m in stub.models]}).encode(), stub.models_status
                else:
                    code, body = stub.paths.get(self.path, 404), b"{}"
                self.send_response(code)
                self.send_header("Content-Length", str(len(body)))
                self.end_headers()
                self.wfile.write(body)
            do_GET = do_POST = handle_any

        self.httpd = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
        self.address = "http://127.0.0.1:%d" % self.httpd.server_address[1]
        threading.Thread(target=self.httpd.serve_forever, daemon=True).start()

    def close(self):
        self.httpd.shutdown()
        self.httpd.server_close()


ALL = {"/v1/messages": 400, "/v1/responses": 400, "/v1/chat/completions": 400}


class Base(unittest.TestCase):
    """A temporary data and settings folder per test; no login check unless a test wants one."""

    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.env = mock.patch.dict(os.environ, {"BZZ_DATA_DIR": str(Path(self.tmp.name) / "data"), "BZZ_CONFIG_DIR": str(Path(self.tmp.name) / "cfg")})
        self.env.start()
        self.addCleanup(self.env.stop)
        os.environ.pop(profile.NO_CHECK_ENV, None)   # the login check is real here (restored with the environment)
        self.stubs = []

    def stub(self, *a, **k):
        s = Stub(*a, **k)
        self.stubs.append(s)
        self.addCleanup(s.close)
        return s

    @staticmethod
    def server(name="lms", address="http://192.168.1.20:1234", key="", window=0):
        s = {"name": name, "address": address}
        if key:
            s["apiKey"] = key
        if window:
            s["contextWindow"] = window
        return s

    @staticmethod
    def entry(name, cli, server=None, **extra):
        from haivequeen import cli_presets
        e = {"name": name, "slots": 2, "classes": {"light": {"model": "m-light", "effort": "low"}, "medium": {"model": "m-med", "effort": "medium"}}}
        cli_presets.apply(e, cli)
        if server:
            e["server"] = server
        e.update(extra)
        return e

    def providers(self, cfg):
        return {p["name"]: p for p in sch.get_providers(cfg)}

    def passed(self, cfg):
        """The provider dicts of the agent runner."""
        from haivequeen import agent_runner
        return {p["name"]: p for p in agent_runner.get_providers(cfg)}


class ServerEntries(Base):
    def test_local_llm_is_read_and_written_in_the_provider_entry(self):
        """A Local LLM provider holds name, address, key and context window itself; an old [[servers]] entry is read into the providers that use it."""
        path = Path(config.config_dir()) / "config.toml"
        entry = self.entry("lms", "claude", access="llm", address="http://192.168.1.20:1234", apiKey="secret-key", contextWindow=120000)
        gui_config.save_file(path, {"slots": 2, "providers": [entry]})
        self.assertNotIn("[[servers]]", path.read_text(encoding="utf-8"))
        want = [{"name": "lms", "address": "http://192.168.1.20:1234", "apiKey": "secret-key", "contextWindow": 120000}]
        self.assertEqual(servers.get_servers(config.load_config()), want)
        old = {"servers": [self.server(key="secret-key", window=120000)], "providers": [self.entry("lms", "claude", "lms")]}
        self.assertEqual(servers.get_servers(old), want)

    def test_key_is_masked_and_stored_only_in_config(self):
        """The page shows a mask; the key is in config.toml and in no other file the server code writes."""
        self.assertEqual(servers.mask_key("secret-key"), servers.MASK)
        self.assertNotIn("secret", servers.mask_key("secret-key"))
        self.assertEqual(servers.mask_key(""), "")
        srv = self.stub(ALL)
        s = {"name": "lms", "address": srv.address, "apiKey": "secret-key", "contextWindow": 0}
        servers.run_test(s)
        for f in Path(self.tmp.name).rglob("*"):
            if f.is_file():
                self.assertNotIn("secret-key", f.read_text(encoding="utf-8", errors="replace"), str(f))

    def test_address_with_or_without_v1_gives_the_same_address(self):
        """P-19 (a): no /v1/v1, in the entry, the hand-over and the migration."""
        for text in ("http://h:1234", "http://h:1234/", "http://h:1234/v1", "http://h:1234/v1/", " HTTP://h:1234/V1 "):
            self.assertEqual(servers.clean_address(text).lower(), "http://h:1234")
        cfg = {"servers": [self.server(address="http://h:1234/v1")], "providers": [self.entry("c", "codex", "lms")]}
        args = self.providers(cfg)["c"]["args"] + sch.endpoint_args(self.providers(cfg)["c"])
        self.assertIn("model_providers.c.base_url=http://h:1234/v1", args)
        self.assertFalse([a for a in args if "/v1/v1" in a])

    def test_one_server_for_two_providers_with_different_clis(self):
        """A server is set up once and used by any number of providers, with any CLI."""
        cfg = {"servers": [self.server(key="k", window=100000)], "providers": [self.entry("a", "claude", "lms"), self.entry("b", "codex", "lms")]}
        ps = self.providers(cfg)
        self.assertEqual(ps["a"]["server"]["address"], ps["b"]["server"]["address"])
        self.assertIn("ANTHROPIC_BASE_URL", servers.environment(ps["a"]))
        self.assertIn("model_provider=b", sch.endpoint_args(ps["b"]))

    def test_unknown_server_is_refused(self):
        with self.assertRaises(ValueError):
            sch.get_providers({"providers": [self.entry("a", "claude", "nope")]})

class HandOver(Base):
    def cfg(self, cli, key="", window=0, models=None):
        e = self.entry("p", cli, "lms")
        if models:
            e["classes"] = models
        return {"servers": [self.server(key=key, window=window)], "providers": [e]}

    def test_claude_gets_base_url_token_attribution_off_and_compaction_window(self):
        env = servers.environment(self.providers(self.cfg("claude", key="tok", window=120000))["p"])
        self.assertEqual(env, {"ANTHROPIC_BASE_URL": "http://192.168.1.20:1234", "ANTHROPIC_AUTH_TOKEN": "tok",
                               "CLAUDE_CODE_ATTRIBUTION_HEADER": "0", "CLAUDE_CODE_AUTO_COMPACT_WINDOW": "120000"})

    def test_empty_key_gives_the_placeholder_and_empty_window_hands_nothing(self):
        env = servers.environment(self.providers(self.cfg("claude"))["p"])
        self.assertEqual(env["ANTHROPIC_AUTH_TOKEN"], servers.PLACEHOLDER_KEY)
        self.assertNotIn("CLAUDE_CODE_AUTO_COMPACT_WINDOW", env)
        p = self.providers(self.cfg("codex"))["p"]
        self.assertEqual(servers.environment(p)[servers.KEY_ENV], servers.PLACEHOLDER_KEY)
        self.assertFalse([a for a in servers.arguments(p) if a.startswith("model_context_window")])
        oc = json.loads(servers.environment(self.providers(self.cfg("opencode"))["p"])["OPENCODE_CONFIG_CONTENT"])
        self.assertNotIn("limit", oc["provider"]["p"]["models"]["m-med"])

    def test_claude_environment_in_the_agent_environment_only_and_user_key_removed(self):
        p = self.providers(self.cfg("claude", key="tok"))["p"]
        env = profile.environment(p, {"ANTHROPIC_API_KEY": "real-key", "PATH": "x"})
        self.assertEqual(env["ANTHROPIC_BASE_URL"], "http://192.168.1.20:1234")
        self.assertNotIn("ANTHROPIC_API_KEY", env)
        self.assertIn("CLAUDE_CONFIG_DIR", env)
        self.assertEqual(env["CLAUDE_CONFIG_DIR"], str(profile.provider_home(p)))

    def test_codex_gets_a_model_provider_with_v1_and_the_window(self):
        p = self.providers(self.cfg("codex", key="tok", window=64000))["p"]
        args = sch.endpoint_args(p)
        self.assertIn("model_provider=p", args)
        self.assertIn("model_providers.p.base_url=http://192.168.1.20:1234/v1", args)
        self.assertIn("model_providers.p.wire_api=responses", args)
        self.assertIn("model_context_window=64000", args)
        self.assertNotIn("tok", " ".join(args))   # the key goes through the environment, never into a command line
        self.assertEqual(servers.environment(p)[servers.KEY_ENV], "tok")
        from haivequeen import agent_runner
        self.assertEqual(sch.endpoint_args(p), agent_runner.endpoint_args(self.passed(self.cfg("codex", key="tok", window=64000))["p"]))

    def test_opencode_and_kilo_get_an_openai_compatible_provider(self):
        models = {"light": {"model": "small", "effort": "low"}, "heavy": {"model": "big", "effort": "high"}}
        for cli, var in (("opencode", "OPENCODE_CONFIG_CONTENT"), ("kilo", "KILO_CONFIG_CONTENT")):
            p = self.providers(self.cfg(cli, key="tok", window=50000, models=models))["p"]
            conf = json.loads(servers.environment(p)[var])
            block = conf["provider"]["p"]
            self.assertEqual(block["npm"], "@ai-sdk/openai-compatible")
            self.assertEqual(block["options"], {"baseURL": "http://192.168.1.20:1234/v1", "apiKey": "tok"})
            self.assertEqual(sorted(block["models"]), ["big", "small"])
            self.assertEqual(block["models"]["small"]["limit"]["context"], 50000)
            self.assertEqual("providers" in conf, cli == "opencode")
            from haivequeen import agent_runner
            args = agent_runner.build_args(self.passed(self.cfg(cli, models=models))["p"], {"model": "small", "cwd": "/w"})
            self.assertEqual(args[args.index("--model") + 1], "p/small")

    def test_other_clis_take_the_model_unchanged(self):
        from haivequeen import agent_runner
        for cli in ("claude", "codex"):
            args = agent_runner.build_args(self.passed(self.cfg(cli))["p"], {"model": "m", "effort": "low", "cwd": "/w", "repo": "/r", "tools": "/t", "claudeTools": "Read", "rules": "/r.md"})
            self.assertEqual(args[args.index("-m" if cli == "codex" else "--model") + 1], "m")

    def test_nothing_is_written_to_the_users_own_profile(self):
        """Handing a server writes no file at all: neither the user's home nor the HaiveQueen profile (only environment and arguments)."""
        home = Path(self.tmp.name) / "home"
        home.mkdir()
        with mock.patch.dict(os.environ, {"HOME": str(home), "USERPROFILE": str(home), "XDG_CONFIG_HOME": str(home / ".config")}):
            for cli in ("claude", "codex", "opencode", "kilo"):
                p = self.providers(self.cfg(cli, key="k", window=1000))["p"]
                profile.environment(p)
                servers.arguments(p)
        self.assertEqual(list(home.rglob("*")), [])

    def test_no_wrapper_on_any_platform(self):
        """The command is the CLI itself and the hand-over is the same list of arguments and variables on Windows, Linux and macOS."""
        results = []
        for win, linux in ((True, False), (False, True), (False, False)):
            with mock.patch.object(osplat, "is_windows", return_value=win), mock.patch.object(osplat, "is_linux", return_value=linux):
                for cli in ("claude", "codex", "opencode", "kilo"):
                    p = self.providers(self.cfg(cli, key="k", window=1000))["p"]
                    self.assertEqual(p["command"], cli)
                    self.assertFalse(str(p["command"]).lower().endswith((".cmd", ".bat", ".sh", ".ps1")))
                    results.append((cli, servers.environment(p), servers.arguments(p)))
        by_cli = {}
        for cli, env, args in results:
            by_cli.setdefault(cli, []).append((env, args))
        for cli, runs in by_cli.items():
            self.assertTrue(all(r == runs[0] for r in runs), cli)

    def test_account_login_provider_gets_nothing(self):
        p = self.providers({"providers": [self.entry("p", "claude")]})["p"]
        self.assertEqual(servers.environment(p), {})
        self.assertEqual(servers.arguments(p), [])


class ConnectionTest(Base):
    def test_reports_reachability_models_and_one_line_per_cli(self):
        srv = self.stub({"/v1/responses": 400})   # only Responses
        result = servers.run_test({"name": "lms", "address": srv.address, "apiKey": "k"})
        self.assertTrue(result["reachable"])
        self.assertEqual(result["models"], ["qwen/qwen3-27b", "other-model"])
        self.assertTrue(result["interfaces"]["codex"]["ok"])
        self.assertFalse(result["interfaces"]["claude"]["ok"])
        self.assertIn("Codex CLI: usable (/v1/responses)", result["lines"])
        self.assertIn("Claude Code: not usable, the server does not offer /v1/messages", result["lines"])
        self.assertIn("OpenCode: not usable, the server does not offer /v1/chat/completions", result["lines"])
        self.assertTrue(result["time"])
        self.assertTrue(all(h[2] == "Bearer k" for h in srv.hits))
        self.assertIn("/v1/messages", servers.summary({"name": "lms", "address": srv.address, "apiKey": "k"}))

    def test_unreachable_server(self):
        srv = self.stub()
        address = srv.address
        srv.close()
        result = servers.run_test({"name": "lms", "address": address}, timeout=1)
        self.assertFalse(result["reachable"])
        self.assertIn("does not answer", result["lines"][0])

    def test_result_is_kept_until_the_next_test_and_cleared_by_address_or_key_change(self):
        srv = self.stub(ALL)
        s = {"name": "lms", "address": srv.address, "apiKey": "k"}
        self.assertIsNone(servers.kept(s))
        servers.run_test(s)
        self.assertEqual(servers.kept(s)["models"][0], "qwen/qwen3-27b")
        self.assertTrue(servers.kept(dict(s)))   # also read again from the file (restart)
        self.assertIsNone(servers.kept({**s, "apiKey": "other"}))
        self.assertIsNone(servers.kept({**s, "address": srv.address + "/x"}))
        self.assertIsNotNone(servers.kept({**s, "address": srv.address + "/v1"}))   # the same address

    def test_rename_moves_the_result(self):
        srv = self.stub(ALL)
        entry = {"name": "lms", "address": srv.address}
        servers.run_test(entry)
        servers.rename_result("lms", "box")
        self.assertIsNotNone(servers.kept({**entry, "name": "box"}))
        self.assertIsNone(servers.kept(entry))


class ReadyCheck(Base):
    def cfg(self, srv, cli="codex", models=None):
        e = self.entry("p", cli, "lms")
        if models:
            e["classes"] = models
        return {"servers": [{"name": "lms", "address": srv.address}], "providers": [e]}

    @staticmethod
    def run_for(cfg, name):
        return servers.run_test(servers.get_server(cfg, name))

    def test_not_ready_without_a_test_then_ready_after_it(self):
        srv = self.stub(ALL)
        cfg = self.cfg(srv)
        p = self.providers(cfg)["p"]
        self.assertFalse(p["login"])
        self.assertIn("not been tested", p["notReady"])
        self.run_for(cfg, "p")
        p = self.providers(cfg)["p"]
        self.assertFalse(p["login"])
        self.assertIn("m-light", p["notReady"])   # the server does not list the model of the class
        cfg["providers"][0]["classes"] = {"medium": {"model": "other-model"}}
        p = self.providers(cfg)["p"]
        self.assertTrue(p["login"], p["notReady"])

    def test_missing_interface_names_it(self):
        srv = self.stub({"/v1/chat/completions": 400})
        cfg = self.cfg(srv, "codex", {"medium": {"model": "other-model"}})
        self.run_for(cfg, "p")
        p = self.providers(cfg)["p"]
        self.assertFalse(p["login"])
        self.assertIn("/v1/responses", p["notReady"])
        cfg["providers"].append(self.entry("q", "opencode", "lms", classes={"medium": {"model": "other-model"}}))
        self.run_for(cfg, "q")
        self.assertTrue(self.providers(cfg)["q"]["login"])

    def test_model_must_be_in_the_list_but_an_own_entry_counts(self):
        """P-13 and P-19 (d): a model that the server does not list is missing, unless the class entry is marked as an own entry."""
        srv = self.stub(ALL)
        cfg = self.cfg(srv, "claude", {"medium": {"model": "not-listed"}})
        self.run_for(cfg, "p")
        p = self.providers(cfg)["p"]
        self.assertFalse(p["login"])
        self.assertIn("not-listed", p["notReady"])
        cfg["providers"][0]["classes"]["medium"]["customModel"] = True
        self.assertTrue(self.providers(cfg)["p"]["login"])

    def test_own_entry_counts_with_account_login(self):
        data = Path(config.data_dir())
        data.mkdir(parents=True, exist_ok=True)
        (data / "cli-models.json").write_text(json.dumps({"claude": ["opus"]}), encoding="utf-8")
        with mock.patch.dict(os.environ, {profile.NO_CHECK_ENV: "1"}):
            e = self.entry("p", "claude", classes={"medium": {"model": "mine"}})
            self.assertFalse(self.providers({"providers": [e]})["p"]["login"])
            e["classes"]["medium"]["customModel"] = True
            self.assertTrue(self.providers({"providers": [e]})["p"]["login"])
            e["classes"]["medium"] = {"model": "opus"}
            self.assertTrue(self.providers({"providers": [e]})["p"]["login"])

    def test_account_login_needs_the_login(self):
        with mock.patch.object(profile, "has_login", return_value=False):
            p = self.providers({"providers": [self.entry("p", "claude")]})["p"]
        self.assertFalse(p["login"])
        self.assertIn("login", p["notReady"])

    def test_not_ready_provider_gets_no_agents(self):
        cfg = {"servers": [self.server()], "providers": [self.entry("p", "claude", "lms"), self.entry("q", "claude")]}
        ps = sch.get_providers(cfg)
        free = sch.provider_free(ps, [])
        self.assertEqual(free["p"], 0)
        with mock.patch.dict(os.environ, {profile.NO_CHECK_ENV: "1"}):
            self.assertEqual(sch.provider_free(sch.get_providers(cfg), [])["q"], 2)

    def test_server_provider_needs_no_login(self):
        with mock.patch.object(profile, "has_login", wraps=profile.has_login):
            cfg = {"servers": [self.server()], "providers": [self.entry("p", "claude", "lms")]}
            self.assertTrue(profile.has_login(sch.get_providers(cfg)[0]))


class Rounds(Base):
    def test_test_runs_on_save_for_new_and_changed_local_llm_providers(self):
        a, b = self.stub(ALL), self.stub(ALL)
        old = {"providers": [self.entry("p", "claude", access="llm", address=a.address), self.entry("q", "claude", access="llm", address=b.address)]}
        new = copy.deepcopy(old)
        self.assertEqual(servers.test_after_save(old, new), [])
        new["providers"][1]["address"] = b.address + "/v1x"   # changed address
        new["providers"].append(self.entry("r", "claude", access="llm", address=a.address))   # new
        self.assertEqual(sorted(servers.test_after_save(old, new)), ["q", "r"])
        new["providers"][0]["slots"] = 3   # the provider changed
        self.assertIn("p", servers.test_after_save(old, new))

    def test_retest_once_per_round_until_the_server_answers(self):
        srv = self.stub({})   # up, but no interface yet
        cfg = {"servers": [{"name": "lms", "address": srv.address}], "providers": [self.entry("p", "claude", "lms", classes={"medium": {"model": "other-model"}})]}
        ps = sch.get_providers(cfg)
        self.assertFalse(ps[0]["login"])
        self.assertEqual(servers.retest_not_ready(ps), ["p"])
        servers.refresh_readiness(ps)
        self.assertFalse(ps[0]["login"])
        srv.paths.update(ALL)   # the server machine woke up / the interface is on
        self.assertEqual(servers.retest_not_ready(ps), ["p"])
        servers.refresh_readiness(ps)
        self.assertTrue(ps[0]["login"], ps[0]["notReady"])
        hits = len(srv.hits)
        self.assertEqual(servers.retest_not_ready(ps), [])   # ready: no more tests
        self.assertEqual(len(srv.hits), hits)

    def test_start_tests_every_local_llm_provider(self):
        a = self.stub(ALL)
        cfg = {"servers": [{"name": "lms", "address": a.address}, {"name": "unused", "address": a.address}], "providers": [self.entry("p", "codex", "lms")]}
        self.assertEqual(servers.test_used(sch.get_providers(cfg)), ["p", "unused"])   # the unused entry is a Local LLM provider now

    def test_the_round_retests_not_ready_servers(self):
        """run_round repeats the test for a provider that is not ready because of it and updates the readiness of the running list."""
        srv = self.stub({})
        cfg = {"servers": [{"name": "lms", "address": srv.address}], "providers": [self.entry("p", "claude", "lms", classes={"medium": {"model": "other-model"}})]}
        env = mock.Mock(providers=sch.get_providers(cfg))
        srv.paths.update(ALL)
        conn = mock.Mock()
        with mock.patch.object(loop.usage, "refresh_claude_usage"), mock.patch.object(loop.profile, "notify_missing"), \
                mock.patch("haivequeen.worktrees.cleanup"), mock.patch.object(loop, "hive_schedule_lock", side_effect=RuntimeError("stop")):
            with self.assertRaises(RuntimeError):
                loop.run_round(conn, cfg, [env])
        self.assertTrue(env.providers[0]["login"])


class LocalRules(Base):
    def test_own_server_provider_is_local_first_for_bees_and_has_no_queen(self):
        cfg = {"servers": [self.server()], "providers": [self.entry("remote", "claude"), self.entry("loc", "claude", "lms")]}
        ps = sch.get_providers(cfg)
        loc = [p for p in ps if p["name"] == "loc"][0]
        self.assertTrue(loc["local"])
        self.assertNotIn("Queen", loc["capabilities"])
        order = [p["name"] for p in bees.route_candidates(ps, "remote", "", False)]
        self.assertEqual(order[0], "loc")

    def test_usage_check_off_for_a_new_server_provider_and_switching_keeps_ticks_and_check(self):
        e = self.entry("n", "claude", capabilities=["Coding", "Queen"], capabilitiesVersion=1)
        servers.set_access(e, "llm", new=True)
        self.assertEqual(e["limit"], {"source": "none"})
        self.assertEqual(e["capabilities"], ["Coding", "Queen"])
        # P-19 (g): an existing provider keeps its usage check when the source changes
        old = self.entry("o", "claude", limit={"source": "claude"})
        servers.set_access(old, "llm")
        self.assertEqual(old["limit"], {"source": "claude"})
        servers.set_access(old, "subscription")
        self.assertNotIn("access", old)
        self.assertEqual(old["limit"], {"source": "claude"})

    def test_switching_the_source_keeps_the_ticks_of_an_entry_from_before_the_ticks(self):
        e = self.entry("o", "claude")   # no capabilitiesVersion: ticks derived (Coding, Queen)
        before = sch.provider_capabilities(e)
        servers.set_access(e, "llm")
        self.assertEqual(sch.provider_capabilities(e), before)
        self.assertEqual(sch.get_providers({"providers": [e]})[0]["capabilities"], before)

    def test_hand_off_limit_is_the_smaller_of_window_and_todays_limit(self):
        cfg = {"servers": [self.server(window=100000)], "providers": [self.entry("a", "claude", "lms"), self.entry("b", "claude", "lms", classes={"medium": {"model": "m", "contextLimitTokens": 50000}}),
                                                                      self.entry("c", "claude")]}
        ps = sch.get_providers(cfg)
        settings = {"contextLimitTokens": 300000}
        self.assertEqual(sch.context_limit(settings, ps, "a", "medium"), 100000)
        self.assertEqual(sch.context_limit(settings, ps, "b", "medium"), 50000)
        self.assertEqual(sch.context_limit(settings, ps, "c", "medium"), 300000)
        self.assertEqual(sch.context_limit({"contextLimitTokens": 80000}, ps, "a", "medium"), 80000)

    def test_empty_window_leaves_todays_limit(self):
        cfg = {"servers": [self.server()], "providers": [self.entry("a", "claude", "lms")]}
        self.assertEqual(sch.context_limit({}, sch.get_providers(cfg), "a", "medium"), 300000)


class Migration(Base):
    def old(self, name="codex", base="http://192.168.1.20:1234/v1", proto="codex", ep_name="LM Studio", **extra):
        e = self.entry(name, proto)
        e["endpoint"] = {"id": "lms", "name": ep_name, "baseUrl": base}
        e.update(extra)
        return e

    def test_codex_endpoint_becomes_an_own_server_provider(self):
        cfg = {"providers": [self.old()]}
        new = servers.migrate(cfg)
        self.assertNotIn("servers", new)
        self.assertEqual((new["providers"][0]["access"], new["providers"][0]["address"]), ("llm", "http://192.168.1.20:1234"))
        self.assertNotIn("server", new["providers"][0])
        self.assertNotIn("endpoint", new["providers"][0])
        self.assertIn("endpoint", cfg["providers"][0])   # the input is not changed
        p = sch.get_providers(cfg)[0]
        self.assertTrue(p["local"])
        self.assertIsNone(p["endpoint"])
        self.assertEqual(servers.window_of(p["server"]), 0)   # window empty: nothing handed over
        self.assertFalse([a for a in sch.endpoint_args(p) if "context" in a])

    def test_same_base_url_shares_one_server_and_the_first_name_wins(self):
        cfg = {"providers": [self.old("a", ep_name="First"), self.old("b", base="http://192.168.1.20:1234", ep_name="Second"), self.old("c", base="http://10.0.0.5:1")]}
        new = servers.migrate(cfg)
        self.assertEqual([p["address"] for p in new["providers"]], ["http://192.168.1.20:1234", "http://192.168.1.20:1234", "http://10.0.0.5:1"])
        self.assertEqual([p["name"] for p in new["providers"]], ["a", "b", "c"])

    def test_only_codex_is_migrated_others_keep_account_login_and_endpoint(self):
        """P-19 (b)."""
        cfg = {"providers": [self.old("cl", proto="claude"), self.old("oc", proto="opencode"), self.old("cx")]}
        new = servers.migrate(cfg)
        self.assertEqual(new["providers"][0], cfg["providers"][0])
        self.assertEqual(new["providers"][1], cfg["providers"][1])
        self.assertEqual(new["providers"][2]["access"], "llm")
        ps = {p["name"]: p for p in sch.get_providers(cfg)}
        self.assertIsNone(ps["cl"]["server"])
        self.assertTrue(ps["cl"]["endpoint"])
        self.assertTrue(ps["cl"]["local"])   # counts as local as today
        self.assertEqual(sch.endpoint_args(ps["cl"])[:2], ["-c", "model_provider=lms"])   # works as today

    def test_custom_cli_stays_custom_with_all_values_and_works_as_today(self):
        """A custom command stays a Custom CLI; with an endpoint it keeps it, needs its login and is local (P-16, P-19 (i))."""
        custom = {"name": "lmstudio", "protocol": "claude", "command": "C:\\bin\\claude-local.cmd", "args": ["--x"], "slots": 1, "outputFormat": "claude",
                  "profileEnv": {"A": "{home}"}, "loginFiles": ["x.json"], "loginCommand": "foo login", "endpoint": {"id": "e", "baseUrl": "http://h:1/v1"}}
        cfg = {"providers": [copy.deepcopy(custom)]}
        self.assertIs(servers.migrate(cfg), cfg)
        p = sch.get_providers(cfg)[0]
        self.assertEqual((p["command"], p["args"], p["endpoint"], p["server"]), ("C:\\bin\\claude-local.cmd", ["--x"], custom["endpoint"], None))
        self.assertTrue(p["local"])
        self.assertFalse(p["login"])   # needs its login as today
        plain = {k: v for k, v in custom.items() if k != "endpoint"}
        self.assertTrue(sch.get_providers({"providers": [plain]})[0]["command"].endswith("claude-local.cmd"))

    def test_existing_and_migrated_providers_keep_their_name(self):
        """P-19 (h): the edited-name mark is true for existing and migrated providers."""
        self.assertTrue(servers.migrate({"providers": [self.old()]})["providers"][0]["nameEdited"])
        self.assertTrue(sch.get_providers({"providers": [self.entry("a", "claude")]})[0]["nameEdited"])
        self.assertFalse(sch.get_providers({"providers": [self.entry("a", "claude", nameEdited=False)]})[0]["nameEdited"])

    def test_server_entry_values_win_over_conflicting_provider_fields(self):
        """F-5: address, key and context window of the server entry are not lost to same-named fields of the provider."""
        cfg = {"servers": [{"name": "s", "address": "http://a:1", "apiKey": "k", "contextWindow": 9}],
               "providers": [{"name": "p", "protocol": "claude", "command": "claude", "server": "s", "address": "http://old:2", "apiKey": "x", "contextWindow": 1}]}
        p = servers.migrate(cfg)["providers"][0]
        self.assertEqual((p["address"], p["apiKey"], p["contextWindow"]), ("http://a:1", "k", 9))

    def test_migrated_provider_has_the_usage_check_it_had(self):
        e = self.old()
        self.assertNotIn("limit", servers.migrate({"providers": [e]})["providers"][0])
        e2 = self.old(limit={"source": "codex"})
        self.assertEqual(servers.migrate({"providers": [e2]})["providers"][0]["limit"], {"source": "codex"})

    def test_config_toml_stays_unchanged_until_the_owner_saves(self):
        path = Path(config.config_dir()) / "config.toml"
        path.parent.mkdir(parents=True, exist_ok=True)
        gui_config.save_file(path, {"providers": [self.old()]})
        before = path.read_bytes()
        loaded = config.load_config()
        self.assertEqual(loaded["providers"][0]["address"], "http://192.168.1.20:1234")
        self.assertNotIn("endpoint", loaded["providers"][0])
        self.assertEqual(gui_config.load_migrated(path)["providers"], loaded["providers"])
        sch.get_providers(loaded)
        self.assertEqual(path.read_bytes(), before)
        gui_config.save_file(path, gui_config.load_migrated(path))   # the owner saves: the new form is written
        text = path.read_text(encoding="utf-8")
        self.assertIn('access = "llm"', text)
        self.assertNotIn("[[servers]]", text)
        self.assertNotIn("endpoint", text)

    def test_agent_runner_reads_the_old_form_too(self):
        p = self.passed({"providers": [self.old()]})["codex"]
        self.assertEqual(p["server"]["address"], "http://192.168.1.20:1234")
        from haivequeen import agent_runner
        self.assertIn("model_providers.codex.base_url=http://192.168.1.20:1234/v1", agent_runner.endpoint_args(p))


if __name__ == "__main__":
    unittest.main()

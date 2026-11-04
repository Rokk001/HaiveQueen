"""Tests of U-6 to U-8: the token overlays of the five tiles, the Start Context tile and its overlay, the capture of the start context in the runner
(Queen, process Bee, subagent Bee) and the benchmark of the start context per provider and Apiary."""
import os
import sys
import tempfile
import unittest
from datetime import datetime, timedelta
from pathlib import Path
from unittest import mock

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
import isolation  # noqa: E402,F401
from haivequeen import agent_runner as ar  # noqa: E402
from haivequeen import bees, db, gui_data, hives, runtime, startctx  # noqa: E402

NOW = datetime(2026, 11, 1, 12, 0, 0)
SYS = {"cores": 8, "cpu": 38, "ram_used_gb": 25.2, "ram_total_gb": 32.0, "ram_pct": 80, "disk_used_tb": 1.2, "disk_total_tb": 2.7, "disk_pct": 45}
CFG = {"slots": 2, "providers": [{"name": "claude"}, {"name": "codex"}, {"name": "opencode"}]}
PLAN = "docs/plans/AI-LEVELS-PLAN.md"
PLAN2 = "docs/plans/OTHER-PLAN.md"


def iso(minutes_ago):
    """Time string a number of minutes before NOW."""
    return (NOW - timedelta(minutes=minutes_ago)).isoformat(timespec="seconds")


def claude_msg(mid, ctx_parts, out=5, sub=None):
    """A Claude assistant event; ctx_parts = (input, cache write, cache read); `sub` = tool use id of the Task call when a subagent writes it."""
    e = {"type": "assistant", "message": {"id": mid, "model": "claude-sonnet-5-5", "usage": {"input_tokens": ctx_parts[0], "cache_creation_input_tokens": ctx_parts[1],
                                                                                         "cache_read_input_tokens": ctx_parts[2], "output_tokens": out}, "content": [{"type": "text", "text": "x"}]}}
    if sub:
        e["parent_tool_use_id"] = sub
    return e


class Base(unittest.TestCase):
    """A temp database with two Apiaries, two Hives, a running Queen with a process Bee and an embedded Bee."""

    def setUp(self):
        """Create the invented data."""
        self.tmp = tempfile.TemporaryDirectory()
        self.conn = db.connect(Path(self.tmp.name) / "o.db")
        for name in ("ODP", "ZED"):
            db.upsert_project(self.conn, name, None, "cyan", 1)
        for proj, plan in (("ODP", PLAN), ("ZED", PLAN2)):
            hives.create(self.conn, proj, plan, "claude", "cyan")
            self.conn.execute("UPDATE hives SET state='running' WHERE project=? AND plan_path=?", (proj, plan))
            for pid in ("P1", "P2", "P3"):
                self.conn.execute("INSERT INTO packages(project,plan_path,id,title) VALUES (?,?,?,?)", (proj, plan, pid, pid))
        self.conn.commit()
        self.add("ODP", "ODP__queen", PLAN, "queen", "Ava", "running", 30, 1000, 100, provider="claude")
        self.add("ODP", "ODP__sophia", PLAN, "bee", "Sophia", "running", 20, 600, 60, package="P1", provider="claude", cache=(400, 100))
        self.add("ODP", "ODP__mia", PLAN, "bee", "Mia", "done", 25, 300, 30, package="P2", provider="claude", cache=(200, 50))
        self.add("ZED", "ZED__queen", PLAN2, "queen", "Eva", "running", 40, 2000, 200, provider="codex", model="gpt-6.1-sol")
        self.add("ZED", "ZED__old", PLAN2, "bee", "Old", "done", 60 * 30, 800, 80, package="P1", provider="codex", model="gpt-6.1-sol")   # a run of an earlier day
        no_loop = mock.patch("haivequeen.loop.loop_running", return_value=0)
        no_loop.start()
        self.addCleanup(no_loop.stop)

    def add(self, project, name, plan, phase, person, status, ago, tin, tout, package=None, provider="claude", model="claude-sonnet-5-5", cache=None):
        """Insert an agent row started `ago` minutes before NOW."""
        f = {"plan": plan, "phase": phase, "provider": provider, "model": model, "effort": "medium", "status": status, "startedAt": iso(ago), "person": person,
             "tokensIn": tin, "tokensOut": tout}
        if package:
            f["package"] = package
        if cache:
            f["cacheRead"], f["cacheWrite"] = cache
        runtime.upsert_agent(self.conn, project, name, f)

    def snap(self):
        """A fresh snapshot."""
        return gui_data.snapshot(self.conn, now=NOW, cfg=CFG, sysinfo=SYS)

    def tearDown(self):
        """Release the temp database."""
        self.conn.close()
        self.tmp.cleanup()


class OverlayTest(Base):
    """U-6: each overlay lists exactly the units its tile counts."""

    def test_total_io_per_model_sums_to_the_tile_and_splits_the_input(self):
        """Total I/O lists today's tokens per model; the sum is the tile; fresh = input minus cache reads, cached = cache reads; a provider without cache figures shows None."""
        s = self.snap()
        rows = s["overlays"]["io"]["rows"]
        self.assertEqual(sum(r["tin"] for r in rows), s["tiles"]["tokens_in"])
        self.assertEqual(sum(r["tout"] for r in rows), s["tiles"]["tokens_out"])
        sonnet = next(r for r in rows if "Sonnet" in r["model"])
        self.assertEqual((sonnet["tin"], sonnet["cached"], sonnet["fresh"]), (1900, 600, 1300))
        gpt = next(r for r in rows if "gpt" in r["model"].lower())
        self.assertEqual((gpt["tin"], gpt["cached"], gpt["fresh"]), (2000, None, None))   # the earlier day's run is not in today's use

    def test_apiaries_and_hives(self):
        """Apiaries: the Apiaries of the tile with today's tokens; Hives: the active Hives with today's tokens and the tokens per finished package (whole use / done packages)."""
        s = self.snap()
        self.assertEqual({r["name"]: (r["tin"], r["tout"]) for r in s["overlays"]["apiaries"]["rows"]}, {"ODP": (1900, 190), "ZED": (2000, 200)})
        self.assertEqual(len(s["overlays"]["apiaries"]["rows"]), s["tiles"]["apiaries"])
        hv = {r["apiary"]: r for r in s["overlays"]["hives"]["rows"]}
        self.assertEqual(len(hv), s["tiles"]["hives"])
        self.assertEqual((hv["ODP"]["tin"], hv["ODP"]["tout"]), (1900, 190))
        self.assertEqual(hv["ODP"]["per_package"], (950, 95))   # 2 runs of the Hive... all runs / done packages
        self.assertEqual(hv["ZED"]["per_package"], (None, None) if hv["ZED"]["done"] == 0 else hv["ZED"]["per_package"])
        self.assertEqual((hv["ZED"]["tin"], hv["ZED"]["tout"]), (2000, 200))

    def test_hive_without_a_done_package_shows_a_dash(self):
        """No done package: no tokens per package."""
        s = self.snap()
        zed = next(r for r in s["overlays"]["hives"]["rows"] if r["apiary"] == "ZED")
        self.assertEqual(zed["per_package"], None)

    def test_queens_and_busy_bees(self):
        """Queens: the online Queens; Busy Bees: the running Bees; each with the tokens of its run as in the table."""
        s = self.snap()
        self.assertEqual([(r["name"], r["tin"], r["tout"]) for r in s["overlays"]["queens"]["rows"]], [("Ava", 1000, 100), ("Eva", 2000, 200)])
        self.assertEqual([(r["name"], r["tin"], r["tout"]) for r in s["overlays"]["bees"]["rows"]], [("Sophia", 600, 60)])
        self.assertEqual(len(s["overlays"]["queens"]["rows"]), s["tiles"]["queens"])
        self.assertEqual(len(s["overlays"]["bees"]["rows"]), s["tiles"]["bees"])

    def test_tile_without_units_shows_none(self):
        """A tile without units has no rows (the window draws "none")."""
        self.conn.execute("DELETE FROM agents")
        self.conn.execute("DELETE FROM hives")
        self.conn.commit()
        s = self.snap()
        for key in ("io", "apiaries", "hives", "queens", "bees"):
            self.assertEqual(s["overlays"][key]["rows"], [], key)

    def test_subagent_bee_tokens_are_counted_once(self):
        """The tokens of a subagent Bee are shown at the Bee and not again at the Queen; the Hive total and the Total I/O count them once."""
        bees.record_embedded_start(self.conn, "ODP", PLAN, "P3", "Mona", "heavy", "claude", "claude-sonnet-5-5", "medium")
        queen = next(a for a in runtime.list_agents(self.conn, "ODP", "active") if a["phase"] == "queen")
        runtime.upsert_agent(self.conn, "ODP", queen["name"], {"subCtx": {"toolu_1": {"desc": "P3 Mona: work", "ctx": 90000, "tin": 400, "tout": 40}}})
        s = self.snap()
        q = next(r for r in s["overlays"]["queens"]["rows"] if r["name"] == "Ava")
        self.assertEqual((q["tin"], q["tout"]), (600, 60))   # her 1000/100 contain the subagent's 400/40
        b = next(r for r in s["overlays"]["bees"]["rows"] if r["name"] == "Mona")
        self.assertEqual((b["tin"], b["tout"]), (400, 40))
        self.assertEqual(sum(r["tin"] for r in s["overlays"]["queens"]["rows"]) + sum(r["tin"] for r in s["overlays"]["bees"]["rows"]), 600 + 2000 + 600 + 400)
        hive = next(r for r in s["overlays"]["hives"]["rows"] if r["apiary"] == "ODP")
        self.assertEqual(hive["tin"], 1900)   # unchanged: counted once
        row = next(r for r in s["bees"] if r["name"] == "Ava")
        self.assertEqual(row["tin"], 600)   # the table shows the same as the overlay


class CaptureTest(unittest.TestCase):
    """U-7: the runner stores context, tokens and the start context from the first turn, also of a subagent Bee."""

    def setUp(self):
        """A temp database and run folder."""
        self.tmp = tempfile.TemporaryDirectory()
        self.conn = db.connect(Path(self.tmp.name) / "o.db")
        db.upsert_project(self.conn, "ODP", None, "cyan", 1)

    def tearDown(self):
        """Release the temp folder."""
        self.conn.close()
        self.tmp.cleanup()

    def run_of(self, name, phase, events, protocol="claude", fmt="claude"):
        """Feed events to an AgentRun and return it."""
        runtime.upsert_agent(self.conn, "ODP", name, {"plan": PLAN, "phase": phase, "provider": "claude", "status": "running", "startedAt": db.now(), "package": "P1"})
        folder = Path(self.tmp.name) / name
        folder.mkdir()
        run = ar.AgentRun(self.conn, "ODP", name, "claude", protocol, self.tmp.name, folder, PLAN, "P1", phase, mock.Mock())
        run.format = fmt
        run.start_parts = {"role": 300, "rules": {"CLAUDE.md": 500}, "task": 100}
        for e in events:
            run.handle_event(e)
        run.push_state(True)
        return run

    def starts(self):
        """The stored starts as dicts."""
        return [dict(r) for r in self.conn.execute("SELECT * FROM start_contexts ORDER BY id")]

    def test_queen_and_process_bee_store_their_first_turn(self):
        """The start context is the context of the first model turn; a later bigger turn does not change it."""
        events = [claude_msg("m1", (10, 90, 900)), claude_msg("m2", (10, 0, 5000))]
        self.run_of("ODP__queen", "queen", events)
        self.run_of("ODP__bee", "bee", events)
        rows = self.starts()
        self.assertEqual([(r["agent"], r["kind"], r["tokens"], r["provider"]) for r in rows], [("ODP__queen", "queen", 1000, "claude"), ("ODP__bee", "bee", 1000, "claude")])
        self.assertIn("CLAUDE.md", rows[0]["parts_json"])
        row = runtime.get_agent(self.conn, "ODP", "ODP__bee")
        self.assertEqual((row["ctxTokens"], row["startCtx"]), (5010, 1000))

    def test_cache_figures_are_stored(self):
        """Cache reads and cache writes are stored with the agent."""
        self.run_of("ODP__bee", "bee", [claude_msg("m1", (10, 90, 900)), claude_msg("m2", (10, 0, 5000))])
        row = runtime.get_agent(self.conn, "ODP", "ODP__bee")
        self.assertEqual((row["cacheRead"], row["cacheWrite"]), (5900, 90))

    def test_other_cli_formats(self):
        """OpenCode stores its first step; Codex without cache figures stores none."""
        self.run_of("ODP__oc", "bee", [{"type": "step_finish", "part": {"tokens": {"input": 50, "output": 5, "cache": {"read": 400, "write": 50}}}},
                                       {"type": "step_finish", "part": {"tokens": {"input": 60, "output": 5, "cache": {"read": 900, "write": 0}}}}], "opencode", "opencode")
        self.assertEqual(self.starts()[0]["tokens"], 500)
        row = runtime.get_agent(self.conn, "ODP", "ODP__oc")
        self.assertEqual(row["cacheRead"], 1300)
        self.run_of("ODP__cx", "bee", [{"type": "turn.completed", "usage": {"input_tokens": 700, "output_tokens": 10}}], "codex", "codex")
        self.assertEqual(self.starts()[1]["tokens"], 700)
        self.assertIsNone(runtime.get_agent(self.conn, "ODP", "ODP__cx").get("cacheRead"))

    def test_subagent_bee_is_captured_without_agent_id(self):
        """A subagent message is told by its parent tool use id alone; the Task call names the Bee; context, tokens and the start of the subagent are stored and the Queen's context stays hers."""
        events = [
            claude_msg("q1", (10, 0, 990)),
            {"type": "assistant", "message": {"id": "q2", "usage": {"input_tokens": 5, "cache_creation_input_tokens": 0, "cache_read_input_tokens": 995, "output_tokens": 5},
                                              "content": [{"type": "tool_use", "id": "toolu_9", "name": "Task", "input": {"description": "P2 Mira: tavern work", "prompt": "do it"}}]}},
            claude_msg("s1", (7, 0, 20000), out=11, sub="toolu_9"),
            claude_msg("s2", (7, 0, 30000), out=13, sub="toolu_9"),
        ]
        run = self.run_of("ODP__queen", "queen", events)
        self.assertEqual(run.ctx, 1000)
        entry = run.sub_ctx["toolu_9"]
        self.assertEqual((entry["desc"], entry["ctx"], entry["tin"], entry["tout"]), ("P2 Mira: tavern work", 30007, 50014, 24))
        kinds = [(r["kind"], r["tokens"]) for r in self.starts()]
        self.assertEqual(kinds, [("queen", 1000), ("subbee", 20007)])
        self.assertEqual(runtime.get_agent(self.conn, "ODP", "ODP__queen")["subCtx"]["toolu_9"]["ctx"], 30007)


class StartTileTest(Base):
    """U-7: the Start Context tile and its overlay."""

    def start(self, project, plan, agent, kind, provider, tokens, parts=None, ago=10):
        """Insert one start row."""
        startctx.record(self.conn, project, plan, agent, kind, provider, tokens, parts or {}, started_at=iso(ago))

    def test_average_per_cli_and_overlay(self):
        """The tile shows the average per provider of today's starts ('–' for none); the overlay has averages per Apiary and Hive and the split per file for Queens and Bees."""
        self.start("ODP", PLAN, "ODP__queen", "queen", "claude", 20000, {"role": 3000, "rules": {"CLAUDE.md": 5000}, "task": 1000})
        self.start("ODP", PLAN, "ODP__sophia", "bee", "claude", 10000, {"role": 2000, "rules": {"CLAUDE.md": 5000}, "task": 500})
        self.start("ODP", PLAN, "Mira", "subbee", "claude", 12000)
        self.start("ZED", PLAN2, "ZED__old", "bee", "claude", 99000, ago=60 * 30)   # an earlier day does not count
        s = self.snap()
        tile = {r["provider"]: r["avg"] for r in s["tiles"]["start_ctx"]}
        self.assertEqual(tile, {"claude": 14000, "codex": None, "opencode": None})
        row = next(r for r in s["overlays"]["start"]["rows"] if r["provider"] == "claude")
        self.assertEqual(row["apiaries"][0]["name"], "ODP")
        self.assertEqual(row["apiaries"][0]["avg"], 14000)
        self.assertEqual(row["hives"][0]["avg"], 14000)
        self.assertEqual(row["split"]["queen"]["parts"], {"role": 3000, "rules CLAUDE.md": 5000, "task": 1000})
        self.assertEqual(row["split"]["queen"]["rest"], 11000)   # 20000 minus the three parts: the CLI's own part
        self.assertEqual(row["split"]["bee"]["avg"], 11000)   # the process Bee and the subagent Bee
        self.assertTrue(row["split"]["queen"]["approximate"])

    def test_benchmark_comparison_or_no_benchmark_yet(self):
        """Per CLI and Apiary the benchmark shows 'HaiveQueen 18k, own profile 61k, -70 %' or 'no benchmark yet'."""
        self.start("ODP", PLAN, "ODP__queen", "queen", "claude", 20000)
        startctx.store_benchmark(self.conn, "claude", "ODP", "hq", 18000, when=iso(5))
        startctx.store_benchmark(self.conn, "claude", "ODP", "own", 61000, when=iso(5))
        s = self.snap()
        row = next(r for r in s["overlays"]["start"]["rows"] if r["provider"] == "claude")
        self.assertEqual(row["apiaries"][0]["benchmark"], "HaiveQueen 18k, own profile 61k, -70 %")
        startctx.store_benchmark(self.conn, "claude", "ZED", "hq", 5000, when=iso(5))
        self.assertEqual(startctx.benchmark_text(self.conn, "claude", "ZED"), "no benchmark yet")   # one value of the pair is no comparison
        self.assertEqual(startctx.benchmark_text(self.conn, "codex", "ODP"), "no benchmark yet")


class BenchmarkTest(unittest.TestCase):
    """U-8: the benchmark measures every registered Apiary of a provider with and without the profile."""

    def setUp(self):
        """A temp database with two Apiaries that have a code folder."""
        self.tmp = tempfile.TemporaryDirectory()
        self.conn = db.connect(Path(self.tmp.name) / "o.db")
        for name in ("ODP", "ZED"):
            ws = Path(self.tmp.name) / name
            ws.mkdir()
            db.upsert_project(self.conn, name, str(ws), "cyan", 1)
        self.cfg = {"providers": [{"name": "claude", "protocol": "claude", "command": "claude", "args": []}, {"name": "codex", "protocol": "codex", "command": "codex", "args": []}]}

    def tearDown(self):
        """Release the temp folder."""
        self.conn.close()
        self.tmp.cleanup()

    def test_measures_each_apiary_twice_with_the_same_task_and_replaces_old_values(self):
        """Two runs per Apiary (with and without profile), the same minimal task, both stored with the date; a new measurement replaces the old one."""
        calls = []

        def measure(prov, cwd, hq, task):
            calls.append((Path(cwd).name, hq, task))
            return 18000 if hq else 61000
        out = startctx.benchmark(self.conn, self.cfg, "claude", measure=measure)
        self.assertEqual(sorted((c[0], c[1]) for c in calls), [("ODP", False), ("ODP", True), ("ZED", False), ("ZED", True)])
        self.assertEqual(len({c[2] for c in calls}), 1)
        self.assertEqual(out["errors"], [])
        rows = startctx.benchmarks(self.conn, "claude")
        self.assertEqual({(r["project"], r["mode"]): r["tokens"] for r in rows}, {("ODP", "hq"): 18000, ("ODP", "own"): 61000, ("ZED", "hq"): 18000, ("ZED", "own"): 61000})
        self.assertTrue(all(r["measured_at"] for r in rows))
        startctx.benchmark(self.conn, self.cfg, "claude", measure=lambda prov, cwd, hq, task: 1000 if hq else 2000)
        self.assertEqual(len(startctx.benchmarks(self.conn, "claude")), 4)
        self.assertEqual({r["tokens"] for r in startctx.benchmarks(self.conn, "claude")}, {1000, 2000})

    def test_failed_run_shows_its_error_stores_nothing_and_keeps_the_old_value(self):
        """A failed run is reported with its error; the other run of the pair is stored; an older value of the failed run stays."""
        startctx.store_benchmark(self.conn, "claude", "ODP", "own", 55000, when="2026-10-01T10:00:00")

        def measure(prov, cwd, hq, task):
            if not hq and Path(cwd).name == "ODP":
                raise RuntimeError("not logged in")
            return 18000 if hq else 61000
        out = startctx.benchmark(self.conn, self.cfg, "claude", measure=measure)
        self.assertEqual(len(out["errors"]), 1)
        self.assertIn("not logged in", out["errors"][0])
        self.assertIn("ODP", out["errors"][0])
        got = {(r["project"], r["mode"]): r["tokens"] for r in startctx.benchmarks(self.conn, "claude")}
        self.assertEqual(got[("ODP", "own")], 55000)   # the older value stays
        self.assertEqual(got[("ODP", "hq")], 18000)   # the successful run of the pair is stored

    def test_excluded_provider_is_not_measured(self):
        """A provider in excludedProviders measures nothing and says so."""
        called = []
        out = startctx.benchmark(self.conn, {**self.cfg, "excludedProviders": ["claude"]}, "claude", measure=lambda *a: called.append(a) or 1)
        self.assertEqual(called, [])
        self.assertTrue(out["excluded"])
        self.assertEqual(startctx.benchmarks(self.conn, "claude"), [])

    def test_benchmark_runs_are_no_agents_or_usage(self):
        """A benchmark creates no agent row and no usage row, so no tile, table or overlay shows it."""
        startctx.benchmark(self.conn, self.cfg, "claude", measure=lambda prov, cwd, hq, task: 10)
        self.assertEqual(self.conn.execute("SELECT COUNT(*) FROM agents").fetchone()[0], 0)
        self.assertEqual(self.conn.execute("SELECT COUNT(*) FROM providers_usage").fetchone()[0], 0)
        self.assertEqual(self.conn.execute("SELECT COUNT(*) FROM start_contexts").fetchone()[0], 0)

    def test_measure_reads_the_first_turn_of_the_event_stream(self):
        """The default measurement runs the CLI and takes the context of the first model turn."""
        stream = "\n".join(__import__("json").dumps(e) for e in [claude_msg("m1", (10, 90, 900)), claude_msg("m2", (1, 1, 9000)),
                                                                {"type": "result", "is_error": False, "result": "OK", "usage": {"input_tokens": 1, "output_tokens": 1}}]) + "\n"
        proc = mock.Mock()
        proc.stdout = [line.encode() for line in stream.splitlines(True)]
        proc.wait.return_value = 0
        proc.stderr = None
        prov = {"name": "claude", "protocol": "claude", "format": "claude", "command": "claude", "args": ["--output-format", "stream-json"]}
        with mock.patch("subprocess.Popen", return_value=proc) as popen:
            self.assertEqual(startctx.measure_cli(prov, self.tmp.name, False, startctx.TASK), 1000)
        self.assertIn(startctx.TASK, popen.call_args[0][0])


class ButtonTest(unittest.TestCase):
    """U-8: the button on the provider page."""

    def setUp(self):
        """A settings window on a temp config."""
        import tkinter as tk
        try:
            self.root = tk.Tk()
            self.root.attributes("-alpha", 0.0)
        except tk.TclError:
            self.skipTest("no display")

        class Quiet(tk.Toplevel):
            """A Toplevel that is transparent from the start."""

            def __init__(self, *args, **kw):
                super().__init__(*args, **kw)
                self.attributes("-alpha", 0.0)

        quiet = mock.patch("tkinter.Toplevel", Quiet)
        quiet.start()
        self.addCleanup(quiet.stop)
        self.tmp = tempfile.TemporaryDirectory()
        self.folder = Path(self.tmp.name)
        (self.folder / "config.toml").write_text('[[providers]]\nname = "claude"\nprotocol = "claude"\ncommand = "claude"\nargs = ["--output-format", "stream-json"]\nslots = 1\n', encoding="utf-8")
        ws = self.folder / "ws"
        ws.mkdir()
        self.conn = db.connect(self.folder / "o.db")
        db.upsert_project(self.conn, "ODP", str(ws), "cyan", 1)

    def tearDown(self):
        """Close the window."""
        self.root.destroy()
        self.conn.close()
        self.tmp.cleanup()

    def test_button_asks_first_and_shows_results_and_errors(self):
        """No confirmation, no measurement; after it the results (and a failed run's error) are shown on the page."""
        from haivequeen import gui_config
        with mock.patch.dict(os.environ, {"BZZ_DATA_DIR": str(self.folder / "data")}):
            win = gui_config.ConfigWindow(self.root, self.conn, self.folder)
            win.select(1)
            self.assertIsNotNone(win.benchmark_button)
            with mock.patch("haivequeen.gui_dialog.ask", return_value=False) as ask, mock.patch("haivequeen.startctx.benchmark") as bench:
                win.benchmark_button.invoke()
                self.assertTrue(ask.called)
                bench.assert_not_called()
            result = {"excluded": False, "errors": ["ODP without profile: not logged in"], "results": []}
            with mock.patch("haivequeen.gui_dialog.ask", return_value=True), mock.patch("haivequeen.startctx.benchmark", return_value=result) as bench:
                win.benchmark_click(wait=True)
                self.assertEqual(bench.call_args[0][2], "claude")
            self.assertIn("not logged in", win.benchmark_status.cget("text"))


class HoverTest(unittest.TestCase):
    """U-6/U-7: the overlay opens over a tile."""

    def test_overlay_over_each_of_the_six_tiles(self):
        """Moving the mouse over a tile draws its overlay; leaving the tiles removes it."""
        import tkinter as tk
        try:
            root = tk.Tk()
            root.attributes("-alpha", 0.0)
        except tk.TclError:
            self.skipTest("no display")
        self.addCleanup(root.destroy)
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        conn = db.connect(Path(tmp.name) / "o.db")
        self.addCleanup(conn.close)
        db.upsert_project(conn, "ODP", None, "cyan", 1)
        from haivequeen import gui
        gui.REFRESH_MS = 10 ** 9
        root.geometry("1672x901")
        win = gui.Dashboard(root, conn)
        root.update()
        self.assertEqual(len(win.snap["tiles"]["start_ctx"]), len(win.snap["tiles"]["start_ctx"]))
        self.assertEqual([k for k, *_ in win.tile_boxes], ["apiaries", "hives", "queens", "bees", "io", "start"])
        for key, x0, y0, x1, y1 in win.tile_boxes:
            win.hover(type("E", (), {"x": int((x0 + x1) / 2 * win.scale), "y": int((y0 + y1) / 2 * win.scale)})())
            self.assertEqual(win.overlay_key, key)
            self.assertTrue(win.canvas.find_withtag("overlay"), key)
        win.hover(type("E", (), {"x": 5, "y": 700})())
        self.assertIsNone(win.overlay_key)
        self.assertFalse(win.canvas.find_withtag("overlay"))
        # the overlay survives a redraw while the mouse stays on the tile
        key, x0, y0, x1, y1 = win.tile_boxes[0]
        win.hover(type("E", (), {"x": int((x0 + x1) / 2 * win.scale), "y": int((y0 + y1) / 2 * win.scale)})())
        win.draw()
        self.assertTrue(win.canvas.find_withtag("overlay"))


if __name__ == "__main__":
    unittest.main()

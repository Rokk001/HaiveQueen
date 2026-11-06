"""Tests of the graphical dashboard: the snapshot data with invented rows (temp database) and a drawing smoke test of the window."""
import re
import sys
import tempfile
import types
import unittest
from datetime import datetime, timedelta
from pathlib import Path
from unittest import mock

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from haivequeen import db, gui_data, hives, runstate, runtime  # noqa: E402

NOW = datetime(2026, 11, 1, 12, 0, 0)
SYS = {"cores": 8, "cpu": 38, "ram_used_gb": 25.2, "ram_total_gb": 32.0, "ram_pct": 80, "disk_used_tb": 1.2, "disk_total_tb": 2.7, "disk_pct": 45}
CFG = {"slots": 2, "providers": [{"name": "claude"}, {"name": "codex"}]}


# Build the iso time a number of minutes before NOW.
def iso(minutes_ago):
    """Time string a number of minutes before NOW."""
    return (NOW - timedelta(minutes=minutes_ago)).isoformat(timespec="seconds")


# Test the snapshot of invented data.
class SnapshotTest(unittest.TestCase):
    """One Apiary with one Hive, a running Queen and two running Bees."""

    # Create a temp database with a project, a Hive, a Queen, Bees, packages, an event and a queue row.
    def setUp(self):
        """Create the invented data."""
        self.tmp = tempfile.TemporaryDirectory()
        self.conn = db.connect(Path(self.tmp.name) / "o.db")
        db.upsert_project(self.conn, "ODP", None, "cyan", 1)
        plan = "docs/plans/AI-LEVELS-PLAN.md"
        hives.create(self.conn, "ODP", plan, "claude", "cyan")
        self.conn.execute("UPDATE hives SET state='running' WHERE project='ODP'")
        for pid in ("P1", "P2", "P3", "P4"):
            self.conn.execute("INSERT INTO packages(project,plan_path,id,title) VALUES ('ODP',?,?,?)", (plan, pid, pid))
        self.conn.commit()
        runtime.upsert_agent(self.conn, "ODP", "ODP__queen", {"plan": plan, "phase": "queen", "provider": "claude", "model": "claude-sonnet-5-5",
                                                              "effort": "medium", "status": "running", "startedAt": iso(65), "person": "Ava"})
        for name, status, pkg, tin in (("Sophia", "running", "P1", 120000), ("Emma", "running", "P2", 90000), ("Mia", "done", "P3", 50000)):
            runtime.upsert_agent(self.conn, "ODP", "ODP__" + name, {"plan": plan, "phase": "bee", "provider": "claude", "model": "claude-sonnet-5-5",
                                                                    "effort": "medium", "status": status, "startedAt": iso(12), "person": name,
                                                                    "package": pkg, "tokensIn": tin, "tokensOut": 3000})
        runtime.append_event(self.conn, "ODP", "accepted hive AI-LEVELS", kind="note", agent="Ava__queen", mirror="none")   # no log file: tests must not write outside their temp folders
        self.conn.execute("INSERT INTO queue(project,branch,tip,status,updated_at) VALUES ('ODP','b1','abc','ready',?)", (iso(1),))
        self.conn.commit()
        no_loop = mock.patch("haivequeen.loop.loop_running", return_value=0)   # the state must not depend on a real orchestrator on this machine
        no_loop.start()
        self.addCleanup(no_loop.stop)
        self.snap = gui_data.snapshot(self.conn, now=NOW, cfg=CFG, sysinfo=SYS)

    # Close the database.
    def tearDown(self):
        """Release the temp database."""
        self.conn.close()
        self.tmp.cleanup()

    # The tiles count what runs.
    def test_tiles(self):
        """One Apiary, one Hive, one Queen, two busy Bees, capacity = the slots of all providers ."""
        t = self.snap["tiles"]
        self.assertEqual((t["apiaries"], t["hives"], t["queens"], t["bees"]), (1, 1, 1, 2))
        self.assertEqual(t["bee_capacity"], 4)

    # The Hive row shows queen, bees, progress and runtime.
    def test_hive_row(self):
        """The Hive has queen Ava, 2 Bees running of 2 open, 1 of 4 packages done (25%) and runs for 1:05:00."""
        h = self.snap["hives"][0]
        self.assertEqual((h["name"], h["apiary"], h["queen"]), ("AI-LEVELS", "ODP", "Ava"))
        self.assertEqual((h["bees_running"], h["bees_total"], h["progress"]), (2, 2, 25))
        self.assertEqual((h["done"], h["total"]), (1, 4))
        self.assertEqual((h["status"], h["kind"], h["runtime"]), ("Running", "ok", "1:05:00"))

    # Bees of a Queen live in their own tables; progress and busy Bees come from there.
    def test_bee_tables_drive_progress_and_waiting_bees(self):
        """Packages finished by a Bee count as progress; a package with a Bee assignment but no process shows as a waiting Bee."""
        from haivequeen import bees
        bees.ensure_attempts(self.conn)
        plan = "docs/plans/AI-LEVELS-PLAN.md"
        self.conn.execute("INSERT INTO bee_completed VALUES ('ODP',?,'P1')", (plan,))
        self.conn.execute("INSERT INTO bee_completed VALUES ('ODP',?,'P2')", (plan,))
        self.conn.execute("INSERT INTO bee_assignments VALUES ('ODP',?,'P4','heavy','codex')", (plan,))
        self.conn.commit()
        snap = gui_data.snapshot(self.conn, now=NOW, cfg=CFG, sysinfo=SYS)
        h = snap["hives"][0]
        self.assertEqual((h["done"], h["total"], h["progress"]), (3, 4, 75))   # P1, P2 by Bees and P3 by the finished Bee agent row
        self.assertEqual(h["bees_total"], 3)   # two running Bees and the waiting assignment of P4
        waiting = [b for b in snap["bees"] if b["status"] == "waiting"]
        self.assertEqual([(b["name"], b["hive"], b["model"], b["task"]) for b in waiting], [("–", "AI-LEVELS", "–", "P4")])   # no name and no stored provider before the Bee starts

    # A plan whose status is PAUSED shows as paused in its Hive row, without a Queen and with one.
    def test_paused_plan_shows_in_the_hive_row(self):
        """The Hive row of a PAUSED plan is flagged and says so; a plan that is not paused keeps its normal status."""
        self.conn.execute("INSERT OR REPLACE INTO plans (project, path, title, status, updated_at) VALUES ('ODP','docs/plans/AI-LEVELS-PLAN.md','AI','PAUSED','2026-11-01T12:00:00')")
        self.conn.commit()
        snap = gui_data.snapshot(self.conn, now=NOW, cfg=CFG, sysinfo=SYS)
        row = snap["hives"][0]
        self.assertTrue(row["paused"])
        self.assertIn("paused", row["status"])
        self.conn.execute("INSERT OR REPLACE INTO plans (project, path, title, status, updated_at) VALUES ('ODP','docs/plans/AI-LEVELS-PLAN.md','AI','READY','2026-11-01T12:00:00')")
        self.conn.commit()
        row = gui_data.snapshot(self.conn, now=NOW, cfg=CFG, sysinfo=SYS)["hives"][0]
        self.assertFalse(row["paused"])
        self.assertNotIn("paused", row["status"])

    # Active Hives show running and waiting Hives only.
    def test_rejected_and_done_hives_are_not_active(self):
        """A Hive of a REJECTED or DONE plan, also one set in the plan file and synced, is absent from the active list; a done Hive state too."""
        plan = "docs/plans/AI-LEVELS-PLAN.md"
        def active():
            snap = gui_data.snapshot(self.conn, now=NOW, cfg=CFG, sysinfo=SYS)
            return snap["hives"], snap["tiles"]["hives"]
        self.assertEqual(len(active()[0]), 1)
        for status in ("REJECTED", "DONE"):
            self.conn.execute("INSERT OR REPLACE INTO plans (project, path, title, status, updated_at) VALUES ('ODP',?,'AI',?,'2026-11-01T12:00:00')", (plan, status))
            self.conn.commit()
            self.assertEqual(active(), ([], 0), status)
        self.conn.execute("UPDATE plans SET status='READY'")
        self.conn.execute("UPDATE hives SET state='done'")
        self.conn.commit()
        self.assertEqual(active()[0], [])

    # Only a running Queen starts Bees, so the assignments of a Hive without one are not waiting Bees.
    def test_assignments_of_a_hive_without_a_running_queen_are_not_waiting_bees(self):
        """With the Queen ended, the stored Bee assignment of the Hive is not shown as a waiting Bee and does not count as a Bee of the Hive."""
        from haivequeen import bees
        bees.ensure_attempts(self.conn)
        self.conn.execute("INSERT INTO bee_assignments VALUES ('ODP','docs/plans/AI-LEVELS-PLAN.md','P4','heavy','codex')")
        self.conn.execute("UPDATE agents SET status='done' WHERE phase='queen'")
        self.conn.commit()
        snap = gui_data.snapshot(self.conn, now=NOW, cfg=CFG, sysinfo=SYS)
        self.assertEqual([b for b in snap["bees"] if b["status"] == "waiting"], [])
        self.assertEqual(snap["hives"][0]["bees_total"], snap["hives"][0]["bees_running"])

    # Context and tokens per Queen, Bee and Hive.
    def test_context_and_tokens(self):
        """The Queen and the Bees carry their context, its limit and their tokens; the Hive carries the total of all its agents."""
        h = self.snap["hives"][0]
        self.assertEqual(h["queen_ctx_limit"], gui_data.CONTEXT_FALLBACK)
        self.assertEqual((h["hive_tin"], h["hive_tout"]), (260000, 9000))
        bee = next(b for b in self.snap["bees"] if b["name"] == "Sophia")
        self.assertEqual((bee["tin"], bee["tout"]), (120000, 3000))
        self.assertEqual(bee["ctx_limit"], gui_data.CONTEXT_FALLBACK)

    # The model limits are bars.
    def test_limits_are_bars(self):
        """Every limit entry has a label, a used share (or None), a level and a note; each provider has a slots bar."""
        self.assertTrue(self.snap["limits"])
        for item in self.snap["limits"]:
            self.assertEqual(set(item), {"label", "pct", "note", "level"})
        self.assertTrue(any(item["label"].endswith("slots") for item in self.snap["limits"]))

    # Everything the window prints is English.
    def test_no_german_words_in_the_window(self):
        """The window code and the status words contain no German text."""
        german = re.compile(r"(?i)\b(wartet|nächster|naechster|unbekannt|kein limit|antwort|abgeschlossen|keine aktive|übergabe|weisellos|ruht)\b")
        for name in ("gui.py", "gui_data.py"):
            source = (Path(__file__).resolve().parent.parent / "haivequeen" / name).read_text(encoding="utf-8")
            self.assertIsNone(german.search(source), name)
        for state in ("answer", "owner_check", "waiting", "queenless", "integration_wait", "provider_wait", "handoff", "resting", "running", "done"):
            self.assertIsNone(german.search(gui_data.status_text({"state": state, "pending_questions": [1], "reset_at": "2026-11-02T10:00:00"})), state)
        for reason in ("STOP", "PAUSED", "waiting for the integration place", "Hive limit 3 reached", "no provider with a free slot", "the Hive is already claimed"):
            self.assertIsNone(german.search(gui_data.blocker_text(reason)), reason)

    # The Queen heads the list and her Bees follow directly below her.
    def test_bees(self):
        """The running Queen Ava comes first with her model and effort, then her two running Bees; the finished Bee is not listed."""
        rows = self.snap["bees"]
        self.assertEqual(rows[0]["kind"], "queen")
        self.assertEqual(rows[0]["name"], "Ava")
        self.assertEqual(sorted(b["name"] for b in rows[1:]), ["Emma", "Sophia"])
        self.assertEqual({b["kind"] for b in rows[1:]}, {"bee"})
        self.assertEqual({b["hive"] for b in rows}, {"AI-LEVELS"})
        self.assertEqual((rows[0]["model"], rows[0]["task"], rows[0]["status"]), ("Son 5.5 med.", "Queen", "running"))
        self.assertEqual(rows[0]["ctx_limit"], gui_data.CONTEXT_FALLBACK)

    # Without a running Queen the Bees stay in the list, without a Queen row.
    def test_bees_without_a_running_queen_have_no_queen_row(self):
        """With the Queen ended the two running Bees are still listed and no row is a Queen row."""
        self.conn.execute("UPDATE agents SET status='done' WHERE phase='queen'")
        self.conn.commit()
        snap = gui_data.snapshot(self.conn, now=NOW, cfg=CFG, sysinfo=SYS)
        self.assertEqual([b["kind"] for b in snap["bees"]], ["bee", "bee"])
        self.assertEqual(snap["tiles"]["queens"], 0)

    # Every log line has the id of its event.
    def test_log_ids_follow_the_log(self):
        """The ids run parallel to the lines, oldest first."""
        self.assertEqual(len(self.snap["log_ids"]), len(self.snap["log"]))
        self.assertEqual(self.snap["log_ids"], sorted(self.snap["log_ids"]))

    # Tokens of the Hive over seven days.
    def test_activity(self):
        """The Apiary row comes first, then the Hive row; with one Apiary and one Hive each holds 100% and the tokens of the agents."""
        apiary, hive = self.snap["activity"][0], self.snap["activity"][1]
        self.assertEqual((apiary["kind"], apiary["name"], apiary["pct"]), ("apiary", "ODP", 100))
        self.assertEqual((hive["kind"], hive["name"], hive["pct"]), ("hive", "AI-LEVELS", 100))
        self.assertEqual((hive["tin"], hive["tout"]), (260000, 9000))

    # Log lines carry their source tag.
    def test_log(self):
        """The Queen's note is tagged as a QUEEN line with its time."""
        stamp, tag, text = self.snap["log"][-1]
        self.assertTrue(tag.startswith("QUEEN"))
        self.assertIn("accepted hive", text)

    # The queue panel counts questions and queued rows.
    def test_queue(self):
        """One queued row labelled ODP#1 and no open question."""
        self.assertEqual((self.snap["queue"]["open"], self.snap["queue"]["queued"]), (0, 1))
        self.assertEqual(self.snap["queue"]["labels"], ["ODP#1"])

    # The window shows what the overview data holds.
    def test_matches_the_overview_data(self):
        """Hive and Queen counts, queue and the status sections match overview.collect."""
        from haivequeen import overview
        d = overview.collect(self.conn, None, NOW, CFG, {"cores": 8, "free_mb": 7000, "total_mb": 32768, "host": "gui", "compiler_count": None})
        self.assertEqual(self.snap["tiles"]["hives"], sum(n for _, n in d["hives"]))
        self.assertEqual(self.snap["tiles"]["queens"], 1)
        self.assertEqual(self.snap["run_state"], d["run"]["state"])
        titles = [t for t, _ in self.snap["info"]]
        self.assertEqual(titles[:5], ["STATE", "USAGE", "SYSTEM", "PLANS", "QUESTIONS"])

    # The build line is part of the dashboard data and of the SYSTEM panel.
    def test_build_line_in_snapshot_without_build_table(self):
        """CFG has no buildJobs table: the STATUS panel SYSTEM section still carries the build line; the SYSTEM meter panel does not."""
        self.assertNotIn("buildJobs", CFG)
        self.assertNotIn("build_line", self.snap)
        lines = [t for title, ls in self.snap["info"] if title == "SYSTEM" for t, _c in ls]
        self.assertTrue(any(re.match(r"^Build jobs \d+ of \d+ cores \((running|next build)\) \| per job not measured yet", t) for t in lines), lines)

    # One state tells the user whether HaiveQueen runs, waits or stands still.
    def test_orchestrator_state(self):
        """No loop process is STOPPED, a pending stop is STOPPING, otherwise the run state decides the label and its colour level."""
        state = gui_data.orchestrator_state
        self.assertEqual(state("RUNNING", 0, False), {"label": "STOPPED", "level": "bad"})
        self.assertEqual(state("RUNNING", 5, True), {"label": "STOPPING", "level": "warn"})
        self.assertEqual(state("RUNNING", 5, False), {"label": "RUNNING", "level": "ok"})
        self.assertEqual(state("PAUSED", 5, False), {"label": "PAUSED", "level": "warn"})
        self.assertEqual(self.snap["orchestrator"]["label"], "STOPPED")   # the test runtime has no loop process

    # The run state is shown.
    def test_run_state(self):
        """A paused run shows PAUSED."""
        runstate.pause(self.conn, "test")
        self.assertEqual(gui_data.snapshot(self.conn, now=NOW, cfg=CFG, sysinfo=SYS)["run_state"], "PAUSED")

    # The Apiary filter narrows the Hives.
    def test_project_filter(self):
        """An unknown Apiary shows no Hives."""
        self.assertEqual(gui_data.snapshot(self.conn, "none", now=NOW, cfg=CFG, sysinfo=SYS)["hives"], [])

    # Formatting helpers.
    def test_formats(self):
        """Token counts and elapsed times use the compact forms of the mockup."""
        self.assertEqual((gui_data.fmt_tokens(964000), gui_data.fmt_tokens(1_100_000), gui_data.fmt_tokens(36000)), ("964k", "1.1M", "36k"))
        self.assertEqual(gui_data.fmt_elapsed(iso(1), NOW), "01:00")
        self.assertEqual(gui_data.hive_name("docs/plans/HQ-ROLES-PLAN.md"), "HQ-ROLES")


# Test the pure parts of the state button, the app icon and the repository link.
class StateButtonTest(unittest.TestCase):
    """The actions a state offers, the icon files and the address the header link opens."""

    # The state button offers the actions that fit its state.
    def test_state_actions(self):
        """STOPPED offers Start; RUNNING and PAUSED offer pause or resume and stop; STOPPING only waits. Every action is a method of the window."""
        from haivequeen import gui
        names = lambda label: [item[1] for item in gui.state_actions(label) if item]
        self.assertEqual(names("STOPPED"), ["start"])
        self.assertEqual(names("RUNNING"), ["toggle_pause", "stop"])
        self.assertEqual(names("PAUSED"), ["toggle_pause", "stop"])
        self.assertEqual(names("STOPPING"), [None])
        for label in ("STOPPED", "STOPPING", "RUNNING", "PAUSED"):
            for item in gui.state_actions(label):
                self.assertTrue(item is None or item[1] is None or callable(getattr(gui.Dashboard, item[1])), (label, item))

    # The window shows the HaiveQueen icon and the repository link points to GitHub.
    def test_app_icon_and_repository_link(self):
        """The icon files exist and the header link opens the project's GitHub page."""
        from haivequeen import gui
        for name in ("app.ico", "app_icon.png"):
            self.assertTrue((gui.ASSETS / name).is_file(), name)
        self.assertEqual(gui.REPO_URL, "https://github.com/Rokk001/HaiveQueen")


# Draw the window once when a display exists.
class WindowTest(unittest.TestCase):
    """Smoke test: the window draws from the invented snapshot without an exception."""

    # Draw the window, or skip without a display.
    def test_draw(self):
        """Create the window, draw a snapshot and check that items exist on the canvas."""
        import tkinter as tk
        from haivequeen import gui
        try:
            root = tk.Tk()
            root.attributes("-alpha", 0.0)   # fully transparent: the test window must not flash on the user's screen
        except tk.TclError:
            self.skipTest("no display")
        tmp = tempfile.TemporaryDirectory()
        conn = db.connect(Path(tmp.name) / "o.db")
        try:
            root.geometry("1672x901")
            gui.REFRESH_MS = 10 ** 9
            win = gui.Dashboard(root, conn)
            root.update()
            self.assertGreater(len(win.canvas.find_all()), 50)
            self.assertTrue(win.hits)
        finally:
            root.destroy()
            conn.close()
            tmp.cleanup()

    # A click on the repository name in the header opens GitHub.
    def test_header_link_opens_the_repository(self):
        """The click area under the version line calls the browser with the repository address."""
        import tkinter as tk
        from haivequeen import gui
        try:
            root = tk.Tk()
            root.attributes("-alpha", 0.0)   # fully transparent: the test window must not flash on the user's screen
        except tk.TclError:
            self.skipTest("no display")
        tmp = tempfile.TemporaryDirectory()
        conn = db.connect(Path(tmp.name) / "o.db")
        try:
            root.geometry("1672x912")
            gui.REFRESH_MS = 10 ** 9
            win = gui.Dashboard(root, conn)
            root.update()
            link = [h for h in win.hits if h[1] < 150 * win.scale < h[3] and h[0] > 450 * win.scale]
            self.assertEqual(len(link), 1)
            with mock.patch("webbrowser.open") as opened:
                link[0][4](None)
            opened.assert_called_once_with(gui.REPO_URL)
        finally:
            root.destroy()
            conn.close()
            tmp.cleanup()

    # The Queen menu of a Hive runs bzz requeen or bzz dequeen for exactly that Hive, and only after a confirmation.
    def test_queen_action_runs_the_bzz_command_after_confirmation(self):
        """Declining starts nothing; confirming starts `requeen` or `dequeen` with `<apiary>:<plan path>` and a hidden console."""
        import tkinter as tk
        from haivequeen import gui
        try:
            root = tk.Tk()
            root.attributes("-alpha", 0.0)   # fully transparent: the test window must not flash on the user's screen
        except tk.TclError:
            self.skipTest("no display")
        tmp = tempfile.TemporaryDirectory()
        conn = db.connect(Path(tmp.name) / "o.db")
        try:
            gui.REFRESH_MS = 10 ** 9
            win = gui.Dashboard(root, conn)
            hive = {"name": "AI-LEVELS", "apiary": "ODP", "plan_path": "docs/plans/AI-LEVELS-PLAN.md"}
            with mock.patch.object(gui.subprocess, "Popen") as popen, mock.patch.object(win, "confirm", return_value=False), mock.patch.object(win, "watch_command"):
                win.queen_action(hive, "requeen")
            popen.assert_not_called()
            for command in ("requeen", "dequeen"):
                with mock.patch.object(gui.subprocess, "Popen") as popen, mock.patch.object(win, "confirm", return_value=True), mock.patch.object(win, "watch_command") as watch:
                    win.queen_action(hive, command)
                argv = popen.call_args[0][0]
                self.assertEqual(argv[1:], ["-m", "haivequeen", command, "ODP:docs/plans/AI-LEVELS-PLAN.md"])
                self.assertEqual(popen.call_args[1].get("creationflags"), gui.NO_WINDOW.get("creationflags"))
                watch.assert_called_once()
        finally:
            root.destroy()
            conn.close()
            tmp.cleanup()

    # Draw at many window sizes and check that nothing is drawn outside the canvas.
    def test_sizes(self):
        """At six window sizes the picture scale never exceeds its layout slot and every canvas item stays inside the canvas."""
        import tkinter as tk
        from haivequeen import gui
        gui.REFRESH_MS = 10 ** 9
        checked = 0
        for width, height in ((800, 500), (1000, 600), (1366, 768), (1600, 900), (1920, 1050), (2400, 1300)):
            try:
                root = tk.Tk()
                root.attributes("-alpha", 0.0)   # fully transparent: the test window must not flash on the user's screen
            except tk.TclError:
                self.skipTest("no display")
            tmp = tempfile.TemporaryDirectory()
            conn = db.connect(Path(tmp.name) / "o.db")
            try:
                root.geometry("%dx%d+0+0" % (width, height))
                win = gui.Dashboard(root, conn)
                root.update()
                canvas_w, canvas_h = win.canvas.winfo_width(), win.canvas.winfo_height()
                if abs(canvas_w - width) > 0.05 * width:
                    continue   # the window manager clamped this size (small screen): nothing to check
                zoom, sub = win.fraction
                self.assertLessEqual(zoom / sub, win.scale * 1.005)
                left, top, right, bottom = win.canvas.bbox("all")
                self.assertGreaterEqual((left, top), (-1, -1), (width, height))
                self.assertLessEqual(right, canvas_w + 2, (width, height))
                self.assertLessEqual(bottom, canvas_h + 2, (width, height))
                checked += 1
            finally:
                root.destroy()
                conn.close()
                tmp.cleanup()
        self.assertGreaterEqual(checked, 3)


if __name__ == "__main__":
    unittest.main()

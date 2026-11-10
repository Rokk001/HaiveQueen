"""Tests of the follow-up fixes F-1 to F-6 of the dashboard and the windows: the busy tile counts every running agent against Agent slots, windows react within
0.1 s while data loads, buttons show hover and pressed, footer links look like links, the Advanced texts of General, and a not-ready provider in Model Limits."""
import os
import sys
import tempfile
import gc
import threading
import time
import unittest
from datetime import datetime, timedelta
from pathlib import Path
from unittest import mock

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
import isolation  # noqa: E402,F401  (redirects the default data and settings folders to a temp folder before anything is imported)
from haivequeen import bees, db, gui, gui_data, gui_dialog, hives, overview, runtime  # noqa: E402

PLAN = "docs/plans/AI-LEVELS-PLAN.md"
NOW = datetime(2026, 11, 1, 12, 0, 0)
SYS = {"cores": 8, "cpu": 38, "ram_used_gb": 25.2, "ram_total_gb": 32.0, "ram_pct": 80, "disk_used_tb": 1.2, "disk_total_tb": 2.7, "disk_pct": 45}
REACT = 0.1   # seconds: the visible effect of a click or key press


def quiet_toplevel():
    """Patch tkinter.Toplevel so test windows are fully transparent from the start."""
    import tkinter as tk

    class Quiet(tk.Toplevel):
        """A Toplevel that is transparent from the start."""

        def __init__(self, *args, **kw):
            super().__init__(*args, **kw)
            self.attributes("-alpha", 0.0)
    return mock.patch("tkinter.Toplevel", Quiet)


class Base(unittest.TestCase):
    """A temp database with one Plan, its running Queen Ava and a running process Agent Sophia."""

    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        patch = mock.patch.dict(os.environ, {"BZZ_DATA_DIR": self.tmp.name, "BZZ_LOG_DIR": str(Path(self.tmp.name) / "logs")})
        patch.start()
        self.addCleanup(patch.stop)
        self.conn = db.connect(Path(self.tmp.name) / "o.db")
        self.addCleanup(self.conn.close)
        db.upsert_project(self.conn, "ODP", None, "cyan", 1)
        hives.create(self.conn, "ODP", PLAN, "claude", "cyan")
        self.conn.execute("UPDATE hives SET state='running' WHERE project='ODP'")
        for pid in ("P1", "P2", "P3"):
            self.conn.execute("INSERT INTO packages(project,plan_path,id,title) VALUES ('ODP',?,?,?)", (PLAN, pid, pid))
        self.conn.commit()
        runtime.upsert_agent(self.conn, "ODP", "ODP__queen", {"plan": PLAN, "phase": "queen", "provider": "claude", "status": "running", "startedAt": db.now(), "person": "Ava"})
        runtime.upsert_agent(self.conn, "ODP", "ODP__bee", {"plan": PLAN, "phase": "bee", "provider": "claude", "status": "running", "startedAt": db.now(),
                                                            "person": "Sophia", "package": "P1"})
        no_loop = mock.patch("haivequeen.loop.loop_running", return_value=0)
        no_loop.start()
        self.addCleanup(no_loop.stop)

    def window(self):
        """A Dashboard on a transparent root (skipped without a display)."""
        import tkinter as tk
        try:
            root = tk.Tk()
            root.attributes("-alpha", 0.0)
        except tk.TclError:
            self.skipTest("no display")
        self.addCleanup(gc.collect)   # runs after the root is destroyed: images of earlier windows are freed here, never by the garbage collector in a reading thread
        self.addCleanup(root.destroy)
        root.geometry("1672x901+0+0")
        gui.REFRESH_MS = 10 ** 9
        win = gui.Dashboard(root, self.conn)
        root.update()
        for _ in range(100):   # the first draw at the real size (it scales the pictures) is part of opening the window
            if win.resize_job is None:
                break
            root.update()
            time.sleep(0.01)
        return root, win


class BusyTileTest(Base):
    """F-1: the busy agents tile counts Queens, process Agents and subagent Agents against the General setting Agent slots."""

    def test_counts_a_queen_a_process_agent_and_a_subagent_against_agent_slots(self):
        bees.record_embedded_start(self.conn, "ODP", PLAN, "P2", "Mona", "heavy", "claude", "claude-opus-5-5", "medium")
        snap = gui_data.snapshot(self.conn, now=NOW, cfg={"slots": 8, "providers": [{"name": "claude", "slots": 3}]}, sysinfo=SYS)
        self.assertEqual((snap["tiles"]["bees"], snap["tiles"]["bee_capacity"]), (3, 8))

    def test_only_a_queen_counts_too(self):
        self.conn.execute("DELETE FROM agents WHERE name='ODP__bee'")
        self.conn.commit()
        snap = gui_data.snapshot(self.conn, now=NOW, cfg={"slots": 5, "providers": []}, sysinfo=SYS)
        self.assertEqual((snap["tiles"]["bees"], snap["tiles"]["bee_capacity"]), (1, 5))

    def test_the_tile_text(self):
        """The window draws the two numbers as 'running / slots'."""
        root, win = self.window()
        t = win.snap["tiles"]
        texts = [win.canvas.itemcget(i, "text") for i in win.canvas.find_all() if win.canvas.type(i) == "text"]
        self.assertIn("%d / %d" % (t["bees"], t["bee_capacity"]), texts)


class ReactionTest(Base):
    """F-2: a slow data refresh never delays a click, a key press, a move or a resize beyond 0.1 s."""

    def slow(self):
        """A snapshot function that waits for a gate (stands for a read of 2 s); returns (function, gate)."""
        gate = threading.Event()
        real = gui_data.snapshot

        def snapshot(*args, **kwargs):
            gate.wait(5)
            return real(*args, **kwargs)
        return snapshot, gate

    def timed(self, call):
        start = time.monotonic()
        call()
        first = time.monotonic() - start
        if first < REACT:
            return first
        start = time.monotonic()   # a loaded machine: judge the second, repeatable call
        call()
        return min(first, time.monotonic() - start)

    def test_dashboard_click_and_key_during_a_slow_refresh(self):
        root, win = self.window()
        snapshot, gate = self.slow()
        with mock.patch.object(gui_data, "snapshot", snapshot):
            self.assertLess(self.timed(win.tick), REACT)   # the refresh starts in the background
            self.assertTrue(win.loading)
            root.update()
            win.draw()   # the picture cache of the real size is warm, as in a window that has been open for a moment
            rows = [h for h in win.hits if h[4] == win.select]
            events = [mock.Mock(x=(row[0] + row[2]) / 2, y=(row[1] + row[3]) / 2) for row in rows[:2]] or [mock.Mock(x=5, y=5)]
            # the best of a few clicks: a machine that is busy with other work may delay a single one
            clicks = [self.timed(lambda e=event: win.on_click(e)) for event in events * 3]
            self.assertLess(min(clicks), REACT)
            self.assertLess(min(self.timed(lambda: win.on_key(mock.Mock(keysym="Tab"))) for _ in range(3)), REACT)
            self.assertLess(min(self.timed(lambda: win.hover(mock.Mock(x=5, y=5))) for _ in range(3)), REACT)
            gate.set()
            for _ in range(200):
                root.update()
                if not win.loading:
                    break
                time.sleep(0.02)
            self.assertFalse(win.loading)

    def test_a_second_refresh_is_not_started_while_one_runs(self):
        root, win = self.window()
        snapshot, gate = self.slow()
        calls = []
        with mock.patch.object(gui_data, "snapshot", lambda *a, **k: (calls.append(1), snapshot(*a, **k))[1]):
            win.tick()
            win.tick()
            gate.set()
            for _ in range(100):
                root.update()
                time.sleep(0.01)
        self.assertEqual(len(calls), 1)

    def test_resize_and_move_events_do_not_wait_for_a_redraw(self):
        root, win = self.window()
        draws = []
        with mock.patch.object(win, "draw", lambda: (draws.append(1), time.sleep(2))):
            elapsed = self.timed(lambda: [win.on_resize(None) for _ in range(30)])
            self.assertLess(elapsed / 30, REACT)
            self.assertEqual(draws, [])   # nothing is drawn while the size events come; one redraw follows when they stop
        self.assertIsNotNone(win.resize_job)

    def test_questions_hives_and_live_windows_during_a_slow_read(self):
        from haivequeen import gui_hives, gui_questions
        root, win = self.window()
        with quiet_toplevel():
            questions = gui_questions.QuestionsWindow(root, self.conn)
            hives_win = gui_hives.HivesWindow(root, self.conn, "Consolas")
            live_file = Path(self.tmp.name) / "live.txt"
            live_file.write_text("one\n", encoding="utf-8")
            live = gui.LiveView(root, "live", live_file, "Consolas")
        gate = threading.Event()

        def slow(*args, **kwargs):
            gate.wait(5)
            return []
        with mock.patch.object(gui_questions, "open_questions", slow), mock.patch.object(gui_hives, "plan_rows", slow), mock.patch.object(gui_data, "live_lines", slow):
            for window in (questions, hives_win, live):
                self.assertLess(self.timed(window.tick if window is live else window.tick), REACT, window)
            root.update()
            # keys and clicks in these windows are handled at once
            self.assertLess(self.timed(lambda: questions.use_recommendation()), REACT)
            self.assertLess(self.timed(lambda: root.update()), REACT)
            gate.set()
            for _ in range(100):
                root.update()
                time.sleep(0.01)
        for window in (questions, hives_win, live):
            window_top = window.win
            window_top.destroy()

    def test_settings_window_handles_a_click_during_a_slow_dashboard_refresh(self):
        from haivequeen import gui_config
        root, win = self.window()
        snapshot, gate = self.slow()
        base = Path(self.tmp.name)
        (base / "config.toml").write_text("slots = 2\n", encoding="utf-8")
        with quiet_toplevel():
            settings = gui_config.ConfigWindow(root, self.conn, base, wait=True)
        with mock.patch.object(gui_data, "snapshot", snapshot):
            win.tick()
            self.assertLess(self.timed(lambda: settings.pick_tab("providers")), REACT)
            gate.set()
            for _ in range(100):
                root.update()
                if not win.loading:
                    break
                time.sleep(0.02)
        settings.win.destroy()

    def pump(self, root, condition, rounds=300):
        """Run the event loop until `condition()` holds."""
        for _ in range(rounds):
            root.update()
            if condition():
                return True
            time.sleep(0.02)
        return condition()

    def test_dashboard_actions_do_not_read_on_the_ui_thread(self):
        """Pause, filter, plan control, stop and the Queen menu return within REACT while every read takes a long time (they refresh and look up in the background)."""
        root, win = self.window()
        gate = threading.Event()
        real_snapshot = gui_data.snapshot
        real_rows = db.rows
        ui = threading.get_ident()
        on_ui = []

        def slow_snapshot(*args, **kwargs):
            on_ui.append(threading.get_ident() == ui)
            gate.wait(5)
            return real_snapshot(*args, **kwargs)

        def slow_rows(*args, **kwargs):
            on_ui.append(threading.get_ident() == ui)
            gate.wait(5)
            return real_rows(*args, **kwargs)
        win.confirm = lambda text: True
        win.complain = win.inform = lambda *a, **k: None
        bee = next(b for b in win.snap["bees"] if b["kind"] != "queen" and b.get("agent"))
        win.table = "bees"
        win.sel["bees"] = win.snap["bees"].index(bee)
        with mock.patch.object(gui_data, "snapshot", slow_snapshot), mock.patch.object(db, "rows", slow_rows),                 mock.patch("haivequeen.runstate.get_state", side_effect=AssertionError("read on the UI thread")),                 mock.patch("haivequeen.runstate.pause"), mock.patch("haivequeen.runstate.resume"), mock.patch("haivequeen.gui.osplat.kill_process_tree"),                 mock.patch("haivequeen.bees.stop_package"), mock.patch("haivequeen.gui_dialog.popup_menu"):
            for call in (win.toggle_pause, win.next_project, win.stop_bee, lambda: win.plan_control(win.snap["hives"][0], "pause"), lambda: win.on_key(mock.Mock(keysym="m"))):
                self.assertLess(self.timed(call), REACT, call)
            self.assertTrue(all(not flag for flag in on_ui), "a data read ran on the UI thread")
            gate.set()
            self.assertTrue(self.pump(root, lambda: not win.loading))

    def test_an_action_during_a_running_refresh_is_read_after_it(self):
        root, win = self.window()
        gate = threading.Event()
        calls = []
        real = gui_data.snapshot

        def snapshot(*args, **kwargs):
            calls.append(1)
            gate.wait(5)
            return real(*args, **kwargs)
        with mock.patch.object(gui_data, "snapshot", snapshot):
            win.tick()
            win.refresh_soon()
            gate.set()
            self.assertTrue(self.pump(root, lambda: len(calls) >= 2 and not win.loading))

    def test_questions_answer_and_selection_do_not_read_on_the_ui_thread(self):
        from haivequeen import gui_questions
        db.add_question(self.conn, "ODP", PLAN, "owner-check", "ok?", "yes")
        root, win = self.window()
        answered = []
        with quiet_toplevel():
            questions = gui_questions.QuestionsWindow(root, self.conn, on_answered=lambda: answered.append(1))
        self.assertTrue(self.pump(root, lambda: bool(questions.questions)))
        gate = threading.Event()

        def slow(*args, **kwargs):
            gate.wait(5)
            return []
        questions.reply.insert("1.0", "fine")
        with mock.patch.object(gui_questions, "open_questions", slow), mock.patch.object(gui_questions, "plan_packages", slow):
            self.assertLess(self.timed(questions.show), REACT)   # the steps of the plan are read in the background
            self.assertLess(self.timed(questions.send), REACT)   # the list is reloaded in the background
            self.assertEqual(answered, [1])
            gate.set()
            self.assertTrue(self.pump(root, lambda: not getattr(questions, "loading", False)))
        questions.win.destroy()

    def test_hives_window_action_and_live_first_load_do_not_wait_for_a_read(self):
        from haivequeen import gui_hives
        root, win = self.window()
        gate = threading.Event()

        def slow(*args, **kwargs):
            gate.wait(5)
            return []
        live_file = Path(self.tmp.name) / "live2.txt"
        live_file.write_text("one\n", encoding="utf-8")
        with quiet_toplevel(), mock.patch.object(gui_hives, "plan_rows", slow), mock.patch.object(gui_data, "live_lines", slow):
            self.assertLess(self.timed(lambda: gui_hives.HivesWindow(root, self.conn, "Consolas").win.destroy()), REACT)
            live = gui.LiveView(root, "live", live_file, "Consolas")
            self.assertLess(self.timed(live.tick), REACT)
            gate.set()
            self.pump(root, lambda: not getattr(live, "loading", False))
        live.win.destroy()

    def test_unchanged_snapshot_is_not_drawn_again(self):
        root, win = self.window()
        fixed = dict(win.snap)

        def same(*args, **kwargs):
            """The same data every time, only the time stamp differs."""
            return {**fixed, "now": datetime.now()}
        with mock.patch.object(win, "draw") as draw, mock.patch.object(gui_data, "snapshot", same):
            win.refresh_async()
            for _ in range(200):
                root.update()
                if not win.loading:
                    break
                time.sleep(0.02)
            win.refresh_async()
            for _ in range(200):
                root.update()
                if not win.loading:
                    break
                time.sleep(0.02)
        self.assertEqual(draw.call_count, 0)


class ButtonTest(unittest.TestCase):
    """F-3: every button shows a hover and a pressed look."""

    def setUp(self):
        import tkinter as tk
        try:
            self.root = tk.Tk()
            self.root.attributes("-alpha", 0.0)
        except tk.TclError:
            self.skipTest("no display")
        self.addCleanup(self.root.destroy)
        gui_dialog.apply_style(self.root, "Consolas")
        self.tk = tk

    def look(self, button):
        return button.cget("background"), button.cget("relief")

    def test_hover_and_pressed_differ_from_normal_and_return(self):
        button = self.tk.Button(self.root, text="OK")
        button.pack()
        self.root.update()
        normal = self.look(button)
        event = mock.Mock(widget=button)
        gui_dialog.button_look(button, "hover")
        hover = self.look(button)
        gui_dialog.button_look(button, "down")
        down = self.look(button)
        self.assertEqual(hover[0], gui.COL["button_hover"])
        self.assertEqual(down, (gui.COL["button_down"], "sunken"))
        self.assertNotEqual(normal[0], hover[0])
        self.assertNotEqual(hover[0], down[0])
        gui_dialog.button_look(button, "normal")
        self.assertEqual(self.look(button), normal)
        self.assertIsNotNone(event)

    def test_real_events_reach_the_buttons(self):
        """The class binding reacts to the pointer and the mouse button of every tk.Button."""
        button = self.tk.Button(self.root, text="OK")
        button.pack()
        self.root.update()
        normal = self.look(button)
        button.event_generate("<Enter>")
        self.root.update()
        self.assertEqual(button.cget("background"), gui.COL["button_hover"])
        button.event_generate("<ButtonPress-1>", x=2, y=2)
        self.root.update()
        self.assertEqual(self.look(button), (gui.COL["button_down"], "sunken"))
        button.event_generate("<Leave>")
        self.root.update()
        self.assertEqual(self.look(button)[0], normal[0])

    def test_a_disabled_button_stays_as_it_is_and_the_cursor_is_a_hand(self):
        button = self.tk.Button(self.root, text="OK", state="disabled")
        before = self.look(button)
        gui_dialog.button_look(button, "hover")
        self.assertEqual(self.look(button), before)
        self.assertEqual(str(self.tk.Button(self.root).cget("cursor")), "hand2")

    def test_bound_once_per_application(self):
        before = self.root.bind_class("Button", "<Enter>")
        gui_dialog.apply_style(self.root, "Consolas")
        self.assertEqual(self.root.bind_class("Button", "<Enter>"), before)


class FooterLinkTest(Base):
    """F-4: the footer links look like links."""

    def test_colour_underline_on_hover_and_hand_cursor(self):
        root, win = self.window()
        names = {entry[5] for entry in win.links}
        self.assertTrue({"plans", "questions", "config", "help"} <= names)
        link = next(entry for entry in win.links if entry[5] == "plans")
        self.assertEqual(win.canvas.itemcget(link[4], "fill"), gui.COL["cyan"])
        self.assertNotIn("underline", str(win.canvas.itemcget(link[4], "font")))
        x, y = (link[0] + link[2]) / 2, (link[1] + link[3]) / 2
        win.hover(mock.Mock(x=x, y=y))
        self.assertIn("underline", str(win.canvas.itemcget(link[4], "font")))
        self.assertEqual(str(win.canvas.cget("cursor")), "hand2")
        win.hover(mock.Mock(x=5, y=5))
        self.assertNotIn("underline", str(win.canvas.itemcget(link[4], "font")))
        self.assertEqual(str(win.canvas.cget("cursor")), "")

    def test_hover_underline_survives_a_redraw(self):
        root, win = self.window()
        link = next(entry for entry in win.links if entry[5] == "help")
        win.hover(mock.Mock(x=(link[0] + link[2]) / 2, y=(link[1] + link[3]) / 2))
        win.draw()
        again = next(entry for entry in win.links if entry[5] == "help")
        self.assertIn("underline", str(win.canvas.itemcget(again[4], "font")))
        win.hover_leave(None)
        self.assertEqual(str(win.canvas.cget("cursor")), "")


class AdvancedTextTest(unittest.TestCase):
    """F-5: the Advanced keys of General are general HaiveQueen settings; hives is labelled Plans, the key stays hives."""

    def test_texts(self):
        from haivequeen import gui_config, i18n
        self.assertEqual(gui_config.GENERAL_OTHER_HELP, "A further HaiveQueen setting, as written in config.toml.")
        self.assertEqual(gui_config.ADVANCED_LABELS["hives"], "Plans")
        self.assertEqual(gui_config.OTHER_HELP, "A further setting of this Project, as written in its settings file.")   # a Project page keeps its own wording
        self.assertEqual(i18n.tr("Plans", language="German"), "Pläne")
        self.assertNotEqual(i18n.tr(gui_config.GENERAL_OTHER_HELP, language="German"), gui_config.GENERAL_OTHER_HELP)

    def test_general_page_shows_them_and_config_keeps_hives(self):
        from test_k5_apiary_page import ApiaryPageCase
        from haivequeen import gui_config

        class Case(ApiaryPageCase):
            def runTest(self):
                config = self.base / "config.toml"
                config.write_text(config.read_text(encoding="utf-8").replace("slots = 2", "slots = 2\nhives = 3\nagentStallSeconds = 60\nexportDir = \"x\""), encoding="utf-8")
                win = self.window()
                win.win.update()
                from test_k5_apiary_page import descendants
                texts = [w.cget("text") for w in descendants(win.form) if w.winfo_class() == "Label"]
                self.assertIn("Plans", texts)
                self.assertNotIn("Hives", texts)
                self.assertEqual(texts.count(gui_config.GENERAL_OTHER_HELP), 3)
                self.assertNotIn(gui_config.OTHER_HELP, texts)
                self.assertIn(("hives",), [path for path, _g, _l in win.getters])
                before = gui_config.load_raw(config)
                self.assertTrue(win.collect())
                win.save()
                self.assertEqual(gui_config.load_raw(config)["hives"], before["hives"])
                win.win.destroy()
        result = unittest.TestResult()
        Case().run(result)
        self.assertEqual(result.failures + result.errors, [], result.failures + result.errors)


class NotReadyProviderTest(Base):
    """F-6: a provider that is switched on but not ready shows offline with its reason and without free slot cells."""

    def test_limit_row_data(self):
        from test_limits_panel import prow
        row = gui_data.limit_rows({"provider_limits": [prow(not_ready="the login is missing")]})[0]["slots"]
        self.assertEqual(row["offline"], "offline")
        self.assertEqual(row["reason"], "not ready: the login is missing")
        self.assertIn("the login is missing", row["hover"])
        ready = gui_data.limit_rows({"provider_limits": [prow(not_ready="")]})[0]["slots"]
        self.assertNotIn("offline", ready)
        self.assertNotIn("reason", ready)

    def test_overview_marks_the_provider_and_the_reason(self):
        cfg = {"providers": [{"name": "alpha", "protocol": "claude", "slots": 2, "use": "preferred", "classes": {"medium": {"model": "m"}}},
                             {"name": "beta", "protocol": "claude", "slots": 2, "use": "fallback", "classes": {"medium": {"model": "m"}}}]}
        with mock.patch("haivequeen.servers.readiness", side_effect=lambda prov, has: (True, "") if prov["name"] == "alpha" else (False, "model m is not in the model list")):
            rows = overview.provider_lines(self.conn, cfg, [])
        self.assertEqual([r["not_ready"] for r in rows], ["", "model m is not in the model list"])
        shown = gui_data.limit_rows({"provider_limits": rows})
        self.assertNotIn("offline", shown[0]["slots"])
        self.assertEqual(shown[1]["slots"]["offline"], "offline")
        self.assertEqual(shown[1]["slots"]["reason"], "not ready: model m is not in the model list")

    def test_german_texts(self):
        from test_limits_panel import prow
        with mock.patch("haivequeen.i18n.machine_language", return_value="German"):
            row = gui_data.limit_rows({"provider_limits": [prow(not_ready="Login fehlt")]})[0]["slots"]
        self.assertEqual(row["reason"], "nicht bereit: Login fehlt")

    def test_the_panel_draws_no_cells_but_the_reason(self):
        root, win = self.window()
        rows = gui_data.limit_rows({"provider_limits": [dict(
            stale=False, stale_hours=None, five_hour=None, error="", age_minutes=None, name="alpha", used_pct=40.0, hours=100.0, reset_at=time.time() + 360000,
            ratio=1.2, has_limit=True, used_slots=0, slots=2, total_slots=3, model="m", next=False, not_ready="the login is missing")]})
        win.snap["limits"] = rows
        win.draw()
        canvas = win.canvas
        self.assertEqual(len(canvas.find_withtag("slot_empty")) + len(canvas.find_withtag("slot_full")), 0)
        texts = [canvas.itemcget(i, "text") for i in canvas.find_all() if canvas.type(i) == "text"]
        self.assertIn("offline", texts)
        self.assertIn("not ready: the login is missing", texts)


class SettingsLoadTest(Base):
    """F-2: the settings window reads its files and the database in a thread; a gated read of 2 s never holds a click, and the pages appear when the read ends."""

    def open(self, root):
        from haivequeen import gui_config
        base = Path(self.tmp.name)
        (base / "config.toml").write_text('slots = 2\n[[providers]]\nname = "claude"\n', encoding="utf-8")
        with quiet_toplevel():
            win = gui_config.ConfigWindow(root, self.conn, base, wait=True)
        self.addCleanup(win.win.destroy)
        return win

    def pump(self, root, condition, rounds=300):
        for _ in range(rounds):
            root.update()
            if condition():
                return True
            time.sleep(0.02)
        return condition()

    def check_gated(self, patched):
        root, _dash = self.window()
        win = self.open(root)
        gate = threading.Event()
        self.addCleanup(gate.set)
        with patched(gate):
            start = time.monotonic()
            win.reload()
            self.assertLess(time.monotonic() - start, REACT)
            self.assertTrue(win.loading)
            start = time.monotonic()
            win.registered()
            root.update()
            self.assertLess(time.monotonic() - start, REACT)
            gate.set()
            self.assertTrue(self.pump(root, lambda: not win.loading))
        self.assertEqual(win.data.get("slots"), 2)
        self.assertIn("ODP", win.registered())
        self.assertEqual(win.status.cget("text"), "")

    def test_reload_with_a_slow_file_read_returns_at_once_and_shows_the_data_after(self):
        from haivequeen import gui_config
        real = gui_config.load_migrated

        def patched(gate):
            def slow(path):
                gate.wait(5)
                return real(path)
            return mock.patch.object(gui_config, "load_migrated", slow)
        self.check_gated(patched)

    def test_reload_with_a_slow_database_read_returns_at_once_and_shows_the_data_after(self):
        from haivequeen import gui_config
        real = gui_config.ConfigWindow.read_registered

        def patched(gate):
            def slow(conn):
                gate.wait(5)
                return real(conn)
            return mock.patch.object(gui_config.ConfigWindow, "read_registered", staticmethod(slow))
        self.check_gated(patched)

    def test_registered_does_not_touch_the_database(self):
        root, _dash = self.window()
        win = self.open(root)
        win.conn = mock.Mock(side_effect=AssertionError("database read on the UI thread"))
        self.assertIn("ODP", win.registered())


class MenubuttonTest(unittest.TestCase):
    """F-3: the buttons of drop-down menus (tk.OptionMenu, option_menu) show hover and pressed like every other button."""

    def setUp(self):
        import tkinter as tk
        try:
            self.root = tk.Tk()
            self.root.attributes("-alpha", 0.0)
        except tk.TclError:
            self.skipTest("no display")
        self.addCleanup(self.root.destroy)
        gui_dialog.apply_style(self.root, "Consolas")
        self.tk = tk

    def check(self, button):
        button.pack()
        self.root.update()
        normal = (button.cget("background"), button.cget("relief"))
        button.event_generate("<Enter>")
        self.root.update()
        hover = (button.cget("background"), button.cget("relief"))
        self.assertEqual(hover[0], gui.COL["button_hover"])
        # a real press would open the menu (a modal grab): the binding of the class is checked, the look is applied by its function
        self.assertIn("<lambda>", self.root.bind_class("Menubutton", "<ButtonPress-1>"))
        gui_dialog.button_look(button, "down")
        down = (button.cget("background"), button.cget("relief"))
        self.assertEqual(down, (gui.COL["button_down"], "sunken"))
        self.assertNotEqual(normal[0], hover[0])
        button.event_generate("<Leave>")
        self.root.update()
        self.assertEqual((button.cget("background"), button.cget("relief")), normal)

    def test_tk_option_menu(self):
        var = self.tk.StringVar(value="a")
        self.check(self.tk.OptionMenu(self.root, var, "a", "b"))

    def test_dashboard_option_menu(self):
        var = self.tk.StringVar(value="a")
        self.check(gui_dialog.option_menu(self.root, var, ["a", "b"], "Consolas"))

    def test_pressed_look_ends_when_the_list_closes(self):
        var = self.tk.StringVar(value="a")
        button = gui_dialog.option_menu(self.root, var, ["a", "b"], "Consolas")
        button.pack()
        self.root.update()
        self.assertIn("<lambda>", button.nametowidget(str(button["menu"])).bind("<Unmap>"))


if __name__ == "__main__":
    unittest.main()

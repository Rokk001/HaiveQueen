"""Tests of the Model Limits panel: one row per provider with name, model, NEXT badge, quota reserve, slot cells, weekly line and 5-hour line."""
import json
import os
import sys
import tempfile
import time
import unittest
from pathlib import Path
from unittest import mock

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
import isolation  # noqa: E402,F401  (redirects the default data and settings folders to a temp folder before anything is imported)
from haivequeen import db, gui_data, overview, scheduler as sch  # noqa: E402

MINUS = "−"


# A provider row as overview.provider_lines builds it.
def prow(**kw):
    """A provider row with a reading, a reset in 100 hours and 1 of 3 slots used; `kw` overrides single fields."""
    row = {"stale": False, "stale_hours": None, "five_hour": None, "error": "", "age_minutes": None, "name": "claude", "used_pct": 40.0,
           "hours": 100.0, "reset_at": time.time() + 360000, "ratio": 1.27, "has_limit": True, "used_slots": 1, "slots": 2, "total_slots": 3,
           "model": "claude-sonnet", "next": False}
    row.update(kw)
    return row


# The limit rows of provider rows.
def rows_of(*provider_rows, german=False):
    """gui_data.limit_rows of the rows, in English or German."""
    with mock.patch("haivequeen.i18n.machine_language", return_value="German" if german else "English"):
        return gui_data.limit_rows({"provider_limits": list(provider_rows)})


class DataTest(unittest.TestCase):
    """The data of the rows (texts, levels, order)."""

    def test_one_row_per_provider_in_order_with_name_and_model(self):
        """L-1/L-2: the rows follow the order of the providers; each has its name, the medium model or 'no medium model'."""
        rows = rows_of(prow(name="codex", model="gpt"), prow(name="claude"), prow(name="local", model=None))
        self.assertEqual([r["name"] for r in rows], ["Codex", "Claude", "Local"])
        self.assertEqual([r["model"] for r in rows], ["gpt", "claude-sonnet", "no medium model"])
        self.assertEqual(rows_of(prow(model=None), german=True)[0]["model"], "kein Medium-Modell")

    def test_reserve_is_whole_percent_with_colour_and_hover(self):
        """L-3: factor 1.27 gives +27 % (green), 0.85 gives minus 15 % (red); the hover sentence names the date; no bare factor."""
        up = rows_of(prow(ratio=1.27))[0]["reserve"]
        down = rows_of(prow(ratio=0.85))[0]["reserve"]
        self.assertEqual((up["text"], up["level"]), ("+27 %", "ok"))
        self.assertEqual((down["text"], down["level"]), (MINUS + "15 %", "bad"))
        date = sch.format_reset_time(prow()["reset_at"])
        self.assertEqual(up["hover"], "27 %% more quota left than time until the reset (%s)" % date)
        self.assertEqual(down["hover"], "15 %% less quota left than time until the reset (%s)" % date)
        self.assertNotIn("1.27", json.dumps(rows_of(prow(ratio=1.27))))
        self.assertNotIn("1,27", json.dumps(rows_of(prow(ratio=1.27), german=True)))

    def test_zero_after_rounding_is_green(self):
        """L-10 (c): a factor of 0.996 shows +0 % in green."""
        res = rows_of(prow(ratio=0.996))[0]["reserve"]
        self.assertEqual((res["text"], res["level"]), ("+0 %", "ok"))

    def test_no_limit_source_and_switched_off_check(self):
        """L-3/L-5/L-10 (f): a provider without limit source shows 'no limit' and 'no weekly limit' in muted colour."""
        row = rows_of(prow(has_limit=False, used_pct=None, ratio=None, hours=None, reset_at=None))[0]
        self.assertEqual((row["reserve"]["text"], row["reserve"]["level"]), ("no limit", "dim"))
        self.assertEqual((row["weekly"]["text"], row["weekly"]["pct"], row["weekly"]["level"]), ("no weekly limit", None, "dim"))

    def test_last_verified_reading_keeps_colour_and_shows_age(self):
        """L-3/L-10 (i): the reserve of a last verified reading keeps green/red and has its age; without reset time it is unknown with the age."""
        old = rows_of(prow(ratio=0.85, stale=True, stale_hours=2.0, error="x", age_minutes=120))[0]["reserve"]
        self.assertEqual((old["text"], old["level"], old["age"]), (MINUS + "15 %", "bad", "2h"))
        young = rows_of(prow(ratio=1.1, error="x", age_minutes=15))[0]["reserve"]
        self.assertEqual((young["text"], young["level"], young["age"]), ("+10 %", "ok", "15min"))
        none = rows_of(prow(ratio=None, hours=None, reset_at=None, stale=True, stale_hours=1.0, error="x", age_minutes=60))[0]["reserve"]
        self.assertEqual((none["text"], none["age"]), ("unknown", "1h"))

    def test_without_reading_shows_the_error_or_unknown(self):
        """L-3: no reading shows the stored error (the Claude text) in place of the reserve, else 'unknown'; the weekly line says '7d unknown'."""
        gone = dict(used_pct=None, ratio=None, hours=None, reset_at=None)
        row = rows_of(prow(error="Claude usage refresh failed: x", **gone))[0]
        self.assertEqual(row["reserve"]["text"], "Claude limit unknown: Claude usage refresh failed: x")
        self.assertEqual(row["weekly"]["text"], "7d unknown")
        self.assertEqual(rows_of(prow(**gone))[0]["reserve"]["text"], "unknown")

    def test_reset_due_now(self):
        """L-3/L-10 (j): with no time left the reserve and the weekly line say 'reset now'."""
        row = rows_of(prow(hours=0.0, ratio=float("inf")))[0]
        self.assertEqual(row["reserve"]["text"], "reset now")
        self.assertEqual(row["weekly"]["text"], "7d 40 % · reset now")

    def test_reading_without_reset_time(self):
        """L-10 (b): a reading without reset time gives the reserve 'unknown' and 'reset unknown' in the weekly line."""
        row = rows_of(prow(ratio=None, hours=None, reset_at=None))[0]
        self.assertEqual(row["reserve"]["text"], "unknown")
        self.assertEqual(row["weekly"]["text"], "7d 40 % · reset unknown")

    def test_weekly_line_and_colour_by_used_share(self):
        """L-5: the weekly line holds percent and reset; the colour follows the used share as before."""
        date = sch.format_reset_time(prow()["reset_at"])
        self.assertEqual(rows_of(prow(used_pct=60.0))[0]["weekly"], {"pct": 60, "level": "warn", "text": "7d 60 % · reset " + date})
        self.assertEqual([rows_of(prow(used_pct=u))[0]["weekly"]["level"] for u in (10.0, 70.0, 90.0)], ["ok", "warn", "bad"])

    def test_five_hour_line(self):
        """L-5/L-10 (d)(e): the 5h percent, 'new window' when expired, nothing without a window or with a last verified reading."""
        self.assertEqual(rows_of(prow(five_hour={"pct": 12, "reset": ""}))[0]["five"], "5h 12 %")
        self.assertEqual(rows_of(prow(five_hour={"pct": None, "reset": "new window"}))[0]["five"], "5h new window")
        self.assertEqual(rows_of(prow())[0]["five"], "")
        self.assertEqual(rows_of(prow(five_hour={"pct": 12, "reset": ""}, error="x", age_minutes=5))[0]["five"], "")
        self.assertEqual(rows_of(prow(five_hour={"pct": 12, "reset": ""}, stale=True, stale_hours=1.0))[0]["five"], "")

    def test_slots(self):
        """L-4/L-10 (a): totals, used and the extra agents above the slot count; 0 slots says 'no slots' and the running agents."""
        self.assertEqual(rows_of(prow(used_slots=4, total_slots=3))[0]["slots"], {"total": 3, "used": 4, "extra": 1, "none": "", "running": ""})
        zero = rows_of(prow(used_slots=2, total_slots=0))[0]["slots"]
        self.assertEqual((zero["none"], zero["running"]), ("no slots", "2 running"))
        self.assertEqual(rows_of(prow(used_slots=0, total_slots=0))[0]["slots"]["running"], "")

    def test_german_texts(self):
        """L-10 (k): every new text exists in German as listed."""
        de = rows_of(prow(ratio=1.27), prow(ratio=0.85), prow(has_limit=False, used_pct=None, ratio=None, hours=None, reset_at=None, total_slots=0, used_slots=2),
                     prow(used_pct=None, ratio=None, hours=None, reset_at=None), prow(hours=0.0, ratio=float("inf"), five_hour={"pct": None, "reset": ""}),
                     prow(ratio=None, hours=None, reset_at=None, five_hour={"pct": None, "reset": ""}), german=True)
        date = sch.format_reset_time(prow()["reset_at"])
        self.assertEqual(de[0]["reserve"]["hover"], "27 %% mehr Quota übrig als Zeit bis zum Reset (%s)" % date)
        self.assertEqual(de[1]["reserve"]["hover"], "15 %% weniger Quota übrig als Zeit bis zum Reset (%s)" % date)
        self.assertEqual((de[2]["reserve"]["text"], de[2]["weekly"]["text"], de[2]["slots"]["none"], de[2]["slots"]["running"]),
                         ("kein Limit", "kein Wochenlimit", "keine Slots", "2 laufen"))
        self.assertEqual((de[3]["reserve"]["text"], de[3]["weekly"]["text"]), ("unbekannt", "7d unbekannt"))
        self.assertEqual((de[4]["reserve"]["text"], de[4]["weekly"]["text"], de[4]["five"]), ("Reset jetzt", "7d 40 % · Reset jetzt", "5h neues Fenster"))
        self.assertEqual(de[5]["weekly"]["text"], "7d 40 % · Reset unbekannt")
        from haivequeen import i18n
        self.assertEqual([i18n.tr(t, language="German") for t in ("NEXT", "Quota reserve", "reserve, slots and weekly limit")],
                         ["NEXT", "Quota-Reserve", "Reserve, Slots und Wochenlimit"])


class ProviderLinesTest(unittest.TestCase):
    """The rows as overview.provider_lines reads them (readings from a limits file)."""

    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.conn = db.connect(Path(self.tmp.name) / "o.db")
        self.addCleanup(self.conn.close)
        self.file = Path(self.tmp.name) / "limits.json"

    def lines(self, readings, providers):
        """The limit rows for the providers with the readings of the limits file."""
        self.file.write_text(json.dumps(readings), encoding="utf-8")
        with mock.patch.dict(os.environ, {"BZZ_PROVIDER_LIMITS_FILE": str(self.file)}):
            rows = overview.provider_lines(self.conn, {"providers": providers}, [])
            return rows, gui_data.limit_rows({"provider_limits": rows})

    @staticmethod
    def provider(name, slots, limit, model="m"):
        """A provider setting."""
        return {"name": name, "protocol": name if name in ("claude", "codex") else "claude", "use": "preferred", "slots": slots, "limit": limit,
                "classes": {"medium": {"model": model}}}

    def test_next_badge_on_the_picked_provider_only(self):
        """L-2: exactly the provider select_provider picks is marked next; with no free slot no row is."""
        readings = {"claude": {"usedPercent": 60, "resetHours": 50}, "codex": {"usedPercent": 10, "resetHours": 100}}
        _raw, rows = self.lines(readings, [self.provider("claude", 2, "claude"), self.provider("codex", 2, "codex")])
        self.assertEqual(sum(r["next"] for r in rows), 1)
        _raw, rows = self.lines(readings, [self.provider("claude", 0, "claude"), self.provider("codex", 0, "codex")])
        self.assertEqual([r["next"] for r in rows], [False, False])

    def test_five_hour_percent_at_every_provider_with_that_source(self):
        """L-5: every provider with a limit source that reports a 5-hour window shows its percent; one without shows none."""
        readings = {"claude": {"usedPercent": 60, "resetHours": 50, "fiveHourPercent": 12}, "codex": {"usedPercent": 10, "resetHours": 100, "fiveHourPercent": 33},
                    "other": {"usedPercent": 10, "resetHours": 100}}
        providers = [self.provider("claude", 2, "claude"), self.provider("codex", 2, "codex"), self.provider("other", 2, "codex")]
        _raw, rows = self.lines(readings, providers)
        self.assertEqual([r["five"] for r in rows], ["5h 12 %", "5h 33 %", ""])
        self.assertEqual(len([r for r in rows]), 3)   # no separate 5h rows

    def test_codex_five_hour_from_the_stored_reading(self):
        """L-5: without a limits file the 5-hour window of a Codex provider comes from its stored reading."""
        from haivequeen import runtime
        reset = int(time.time()) + 3600
        runtime.kv_set(self.conn, "*", "usage-codex:codex", json.dumps({"fiveHour": {"utilization": 0.25, "resetsAt": reset}}))
        env = {k: v for k, v in os.environ.items() if k not in ("BZZ_RUNTIME_OVERRIDE", "BZZ_PROVIDER_LIMITS_FILE")}
        with mock.patch.dict(os.environ, env, clear=True):
            rows = overview.provider_lines(self.conn, {"providers": [self.provider("codex", 2, "codex")]}, [])
        self.assertEqual(gui_data.limit_rows({"provider_limits": rows})[0]["five"], "5h 25 %")

    def test_snapshot_limits_are_the_rows(self):
        """L-6: the snapshot holds the rows; a slot that becomes used shows in the next snapshot."""
        cfg = {"slots": 2, "providers": [self.provider("claude", 3, "none")]}
        before = gui_data.snapshot(self.conn, cfg=cfg, sysinfo={"cores": 8, "cpu": 1, "ram_used_gb": 1.0, "ram_total_gb": 8.0, "ram_pct": 10,
                                                                    "disk_used_tb": 1.0, "disk_total_tb": 2.0, "disk_pct": 10, "host": "h"})
        self.assertEqual([r["slots"]["used"] for r in before["limits"]], [0])
        from haivequeen import runtime
        runtime.upsert_agent(self.conn, "ODP", "ODP__bee", {"plan": "docs/plans/X-PLAN.md", "phase": "bee", "provider": "claude", "status": "running",
                                                            "startedAt": db.now(), "person": "Sophia"})
        after = gui_data.snapshot(self.conn, cfg=cfg, sysinfo={"cores": 8, "cpu": 1, "ram_used_gb": 1.0, "ram_total_gb": 8.0, "ram_pct": 10,
                                                                   "disk_used_tb": 1.0, "disk_total_tb": 2.0, "disk_pct": 10, "host": "h"})
        self.assertEqual([r["slots"]["used"] for r in after["limits"]], [1])


class CellSizeTest(unittest.TestCase):
    """The size of the slot cells."""

    def test_cells_shrink_to_half_then_become_a_number(self):
        """L-4: normal size when they fit, smaller down to half the normal size, else None."""
        from haivequeen import gui
        self.assertEqual(gui.slot_cell_size(3, 215), gui.CELL)
        mid = gui.slot_cell_size(14, 215)
        self.assertTrue(gui.CELL / 2 <= mid < gui.CELL)
        self.assertIsNone(gui.slot_cell_size(40, 215))
        self.assertAlmostEqual(gui.slot_cell_size(1, 12), 12)
        self.assertIsNone(gui.slot_cell_size(1, 8))


class PanelDrawTest(unittest.TestCase):
    """The panel drawn in a window on invented rows."""

    BOX = (1045, 395, 1662, 600)

    def setUp(self):
        import tkinter as tk
        try:
            self.root = tk.Tk()
            self.root.attributes("-alpha", 0.0)
        except tk.TclError:
            self.skipTest("no display")
        self.tmp = tempfile.TemporaryDirectory()
        self.patch = mock.patch.dict(os.environ, {"BZZ_DATA_DIR": self.tmp.name, "BZZ_LOG_DIR": str(Path(self.tmp.name) / "logs")})
        self.patch.start()
        no_loop = mock.patch("haivequeen.loop.loop_running", return_value=0)
        no_loop.start()
        self.addCleanup(no_loop.stop)
        self.conn = db.connect(Path(self.tmp.name) / "o.db")
        from haivequeen import gui
        gui.REFRESH_MS = 10 ** 9
        self.gui = gui

    def tearDown(self):
        self.root.destroy()
        self.conn.close()
        self.patch.stop()
        self.tmp.cleanup()

    def open(self, rows, width=1672, height=901):
        """Open the dashboard at a size (in a fresh root window after the first one) and draw the limit rows."""
        import tkinter as tk
        if getattr(self, "used", False):
            self.root.destroy()
            self.root = tk.Tk()
            self.root.attributes("-alpha", 0.0)
        self.used = True
        self.root.geometry("%dx%d+0+0" % (width, height))
        win = self.gui.Dashboard(self.root, self.conn)
        self.root.update()
        win.snap = {**win.snap, "limits": rows}
        win.draw()
        self.root.update()
        return win

    def texts(self, win):
        """[(left, top, right, bottom, text)] of the text items inside the panel (design coordinates)."""
        x0, y0, x1, y1 = self.BOX
        out = []
        for item in win.canvas.find_all():
            if win.canvas.type(item) == "text":
                l, t, r, b = (v / win.scale for v in win.canvas.bbox(item))
                if x0 <= l and r <= x1 + 1 and y0 + 40 <= t and b <= y1 + 1 and t >= y0 + 40:
                    out.append((l, t, r, b, win.canvas.itemcget(item, "text")))
        return out

    def test_row_content_cells_and_badge(self):
        """L-2/L-4: name, model, one NEXT badge, filled and empty cells, no percent of slots and no old next text."""
        rows = rows_of(prow(name="claude", next=True, used_slots=2, total_slots=5), prow(name="codex", model="gpt", used_slots=0, total_slots=0))
        win = self.open(rows)
        found = [t[4] for t in self.texts(win)]
        for want in ("Claude", "claude-sonnet", "Codex", "gpt", "NEXT", "Quota reserve", "+27 %", "no slots"):
            self.assertIn(want, found)
        self.assertEqual(found.count("NEXT"), 1)
        self.assertEqual(len(win.canvas.find_withtag("next_badge")), 1)
        self.assertEqual((len(win.canvas.find_withtag("slot_full")), len(win.canvas.find_withtag("slot_empty"))), (2, 3))
        self.assertFalse([t for t in found if "next" in t.lower() and t != "NEXT"] or [t for t in found if "◀" in t])

    def test_honeycomb_geometry_and_screenshot(self):
        """S-10: regular hexagons share edges for free, full and mixed rows."""
        import math
        win = self.open(rows_of(prow(used_slots=0, total_slots=5), prow(name="codex", used_slots=3, total_slots=3),
                                prow(name="local", used_slots=2, total_slots=5)))
        cells = [i for i in win.canvas.find_all() if "slot_full" in win.canvas.gettags(i) or "slot_empty" in win.canvas.gettags(i)]
        self.assertEqual(len(cells), 8)
        for start, count in ((0, 5), (5, 3)):
            polygons = [win.canvas.coords(i) for i in cells[start:start + count]]
            for pts in polygons:
                sides = [math.hypot(pts[2*k] - pts[2*((k+1)%6)], pts[2*k+1] - pts[2*((k+1)%6)+1]) for k in range(6)]
                self.assertLess(max(sides) - min(sides), 0.01)
            for a, b in zip(polygons, polygons[1:]):
                edges = lambda p: {frozenset(((round(p[2*k], 3), round(p[2*k+1], 3)), (round(p[2*((k+1)%6)], 3), round(p[2*((k+1)%6)+1], 3)))) for k in range(6)}
                self.assertTrue(edges(a) & edges(b), (a, b))
        # Capture the rendered panel, including all three rows, for visual inspection.
        try:
            from PIL import ImageGrab
        except ImportError:
            self.skipTest("Pillow is required for screen capture")
        self.root.attributes("-alpha", 1.0)
        self.root.update()
        x, y = win.canvas.winfo_rootx(), win.canvas.winfo_rooty()
        box = tuple(round(v * win.scale) for v in self.BOX)
        shot = ImageGrab.grab((x + box[0], y + box[1], x + box[2], y + box[3]))
        self.assertGreater(sum(1 for pixel in shot.convert("RGB").getdata() if pixel == (246, 184, 0)), 30)
        win.scroll["limits"] = 2
        win.draw()
        self.root.update()
        mixed = [i for i in win.canvas.find_all() if "slot_full" in win.canvas.gettags(i) or "slot_empty" in win.canvas.gettags(i)]
        mixed = mixed[-5:]
        self.assertEqual(["slot_full" in win.canvas.gettags(i) for i in mixed], [True, True, False, False, False])
        shot = ImageGrab.grab((x + box[0], y + box[1], x + box[2], y + box[3])).convert("RGB")
        self.assertGreater(sum(1 for pixel in shot.getdata() if pixel == (246, 184, 0)), 30)

    def test_slot_hover_localized_and_current(self):
        """S-10: hovering cells explains the current counts in both languages."""
        for german, expected in ((False, "Agent slots of Claude: 2 of 5 in use. A filled cell is a running agent, an empty cell a free slot."),
                                 (True, "Agent-Slots von Claude: 2 von 5 belegt. Eine volle Wabe ist ein laufender Agent, eine leere ein freier Slot.")):
            with self.subTest(german=german), mock.patch("haivequeen.i18n.machine_language", return_value="German" if german else "English"):
                win = self.open(rows_of(prow(used_slots=2, total_slots=5), german=german))
                cell = win.canvas.find_withtag("slot_full")[0]
                pts = win.canvas.coords(cell)
                win.hover(mock.Mock(x=sum(pts[::2])/6, y=sum(pts[1::2])/6))
                self.assertEqual(win.tip_text, expected)

    def test_no_badge_when_none_is_next(self):
        """L-2: no NEXT badge when no row is next."""
        win = self.open(rows_of(prow(), prow(name="codex")))
        self.assertEqual(len(win.canvas.find_withtag("next_badge")), 0)

    def test_extra_agents_and_running_without_slots(self):
        """L-10 (a): all cells filled plus +N; 0 slots with agents shows 'no slots' and 'N running'."""
        win = self.open(rows_of(prow(used_slots=5, total_slots=3), prow(name="codex", used_slots=2, total_slots=0)))
        found = [t[4] for t in self.texts(win)]
        self.assertEqual((len(win.canvas.find_withtag("slot_full")), len(win.canvas.find_withtag("slot_empty"))), (3, 0))
        self.assertIn("+2", found)
        self.assertIn("no slots", found)
        self.assertIn("2 running", found)

    def test_many_slots_become_a_number(self):
        """L-4: cells that do not fit even at half size are replaced by 'used / total'."""
        win = self.open(rows_of(prow(used_slots=7, total_slots=60)))
        self.assertEqual(len(win.canvas.find_withtag("slot_full")) + len(win.canvas.find_withtag("slot_empty")), 0)
        self.assertIn("7 / 60", [t[4] for t in self.texts(win)])

    def test_weekly_and_five_hour_lines_with_equal_row_height(self):
        """L-5: the weekly line and the 5h line below it; rows with and without the 5h line have the same height."""
        rows = rows_of(prow(used_pct=60.0, five_hour={"pct": 12, "reset": ""}), prow(name="codex", used_pct=60.0))
        win = self.open(rows)
        found = self.texts(win)
        weekly = sorted(t for t in found if t[4].startswith("7d 60 %"))
        five = [t for t in found if t[4] == "5h 12 %"]
        self.assertEqual((len(weekly), len(five)), (2, 1))
        self.assertGreater(five[0][1], weekly[0][1])
        names = sorted(t for t in found if t[4] in ("Claude", "Codex"))
        self.assertAlmostEqual(weekly[1][1] - weekly[0][1], names[1][1] - names[0][1], delta=2)

    def test_hover_shows_the_sentence(self):
        """L-3: hovering over the reserve shows the one-sentence explanation."""
        win = self.open(rows_of(prow(ratio=1.27)))
        x0, y0, x1, y1, _tip = win.tip_boxes[0]
        x, y = (x0 + x1) / 2, (y0 + y1) / 2
        win.hover(mock.Mock(x=x, y=y))
        shown = [win.canvas.itemcget(i, "text") for i in win.canvas.find_withtag("tip") if win.canvas.type(i) == "text"]
        self.assertEqual(len(shown), 1)
        self.assertTrue(shown[0].startswith("27 % more quota left than time until the reset ("))
        win.hover_leave(None)
        self.assertEqual(len(win.canvas.find_withtag("tip")), 0)

    def test_panel_keeps_its_box_and_subtitle(self):
        """L-7/L-8: the panel frame is where it was and the subtitle reads 'reserve, slots and weekly limit'."""
        win = self.open(rows_of(prow()))
        self.assertTrue(any(t[4].startswith("| reserve, slots and weekly limit") for t in
                            [(0, 0, 0, 0, win.canvas.itemcget(i, "text")) for i in win.canvas.find_all() if win.canvas.type(i) == "text"]))
        frames = [tuple(round(v / win.scale) for v in win.canvas.coords(i)) for i in win.canvas.find_all() if win.canvas.type(i) == "rectangle"]
        self.assertIn((1045, 395, 1662, 600), frames)

    def test_rows_inside_the_panel_without_overlap_at_every_size(self):
        """L-7: at the default size and at the sizes of the size test every shown row is inside the panel and no text overlaps another."""
        rows = rows_of(prow(next=True, five_hour={"pct": 12, "reset": ""}), prow(name="codex", model="gpt-5.5-codex", used_slots=2, total_slots=14),
                       prow(name="local", has_limit=False, used_pct=None, ratio=None, hours=None, reset_at=None, total_slots=0))
        for width, height in ((1672, 901), (800, 500), (1000, 600), (1200, 700), (1366, 768), (1600, 900), (1920, 1050), (2400, 1300)):
            if width > self.root.winfo_screenwidth() or height > self.root.winfo_screenheight():
                continue
            with self.subTest(size=(width, height)):
                win = self.open(rows, width, height)
                found = self.texts(win)
                self.assertTrue(any(t[4].startswith("Cl") for t in found))
                if win.scale >= 0.55:   # in tiny windows the fonts cannot get smaller than 8 pixels and long texts are shortened
                    self.assertFalse([t[4] for t in found if t[4].endswith("…")], (width, height))
                for i, a in enumerate(found):
                    for b in found[i + 1:]:
                        overlap = a[0] < b[2] - 0.5 and b[0] < a[2] - 0.5 and a[1] < b[3] - 0.5 and b[1] < a[3] - 0.5
                        self.assertFalse(overlap, "%r overlaps %r at %dx%d" % (a[4], b[4], width, height))
                for item in win.canvas.find_all():   # nothing of the panel text is cut at the panel border
                    if win.canvas.type(item) == "text":
                        l, t, r, b = (v / win.scale for v in win.canvas.bbox(item))
                        if 1045 <= l <= 1662 and 395 + 40 <= t <= 600 and t < 600 <= b:
                            self.fail("%r is cut at the panel border at %dx%d" % (win.canvas.itemcget(item, "text"), width, height))

    def test_scroll_bar_and_scrolling_with_many_providers(self):
        """L-7: with more providers than fit the panel scrolls; the first row differs after scrolling and rows keep their height."""
        rows = rows_of(*[prow(name="p%d" % n) for n in range(8)])
        win = self.open(rows)
        self.assertIn("limits", win.bars)
        first = sorted(t for t in self.texts(win) if t[4].startswith("P"))
        self.assertGreaterEqual(len(first), 1)
        win.scroll["limits"] = 3
        win.draw()
        after = sorted(t for t in self.texts(win) if t[4].startswith("P"))
        self.assertEqual(after[0][4], "P3")
        self.assertEqual(len(after), len(first))

    def test_weekly_bar_colours(self):
        """L-5: the weekly bar is green, yellow or red by used share."""
        fills = []
        for used in (10.0, 70.0, 95.0):
            win = self.open(rows_of(prow(used_pct=used)))
            bars = [win.canvas.itemcget(i, "fill") for i in win.canvas.find_all() if win.canvas.type(i) == "rectangle"
                    and abs((win.canvas.coords(i)[3] - win.canvas.coords(i)[1]) / win.scale - 5) < 0.2
                    and win.canvas.coords(i)[0] / win.scale > 1045 + 150 and 395 < win.canvas.coords(i)[1] / win.scale < 600]
            fills.append(bars[-1])
        self.assertEqual(fills, [self.gui.COL[self.gui.LEVEL_FILL[level]] for level in ("ok", "warn", "bad")])


if __name__ == "__main__":
    unittest.main()

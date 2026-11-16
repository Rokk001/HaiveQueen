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
from haivequeen import db, dashboard_data, overview, scheduler as sch  # noqa: E402

MINUS = "−"


# The pixels of a Tk widget as Windows draws its window, independent of the screen.
def capture(widget):
    """RGB image of `widget` drawn by Windows itself (PrintWindow): the test window stays invisible (alpha 0) and nothing on the screen,
    another window on top, a locked desktop or a remote session, changes the result. None where PrintWindow is not available."""
    if sys.platform != "win32":
        return None
    import ctypes
    from ctypes import wintypes
    from PIL import Image

    class Header(ctypes.Structure):
        _fields_ = [("biSize", wintypes.DWORD), ("biWidth", wintypes.LONG), ("biHeight", wintypes.LONG), ("biPlanes", wintypes.WORD),
                    ("biBitCount", wintypes.WORD), ("biCompression", wintypes.DWORD), ("biSizeImage", wintypes.DWORD), ("biXPelsPerMeter", wintypes.LONG),
                    ("biYPelsPerMeter", wintypes.LONG), ("biClrUsed", wintypes.DWORD), ("biClrImportant", wintypes.DWORD)]
    user32, gdi32 = ctypes.windll.user32, ctypes.windll.gdi32
    user32.GetDC.restype = gdi32.CreateCompatibleDC.restype = gdi32.CreateCompatibleBitmap.restype = ctypes.c_void_p
    user32.GetDC.argtypes = [ctypes.c_void_p]
    user32.ReleaseDC.argtypes = [ctypes.c_void_p, ctypes.c_void_p]
    user32.PrintWindow.argtypes = [ctypes.c_void_p, ctypes.c_void_p, ctypes.c_uint]
    gdi32.CreateCompatibleDC.argtypes = [ctypes.c_void_p]
    gdi32.CreateCompatibleBitmap.argtypes = [ctypes.c_void_p, ctypes.c_int, ctypes.c_int]
    gdi32.SelectObject.argtypes = [ctypes.c_void_p, ctypes.c_void_p]
    gdi32.DeleteObject.argtypes = gdi32.DeleteDC.argtypes = [ctypes.c_void_p]
    gdi32.GetDIBits.argtypes = [ctypes.c_void_p, ctypes.c_void_p, ctypes.c_uint, ctypes.c_uint, ctypes.c_void_p, ctypes.c_void_p, ctypes.c_uint]
    widget.update()
    hwnd = widget.winfo_id()
    rect = wintypes.RECT()
    user32.GetClientRect(ctypes.c_void_p(hwnd), ctypes.byref(rect))
    width, height = rect.right, rect.bottom
    screen = user32.GetDC(hwnd)
    memory = gdi32.CreateCompatibleDC(screen)
    bitmap = gdi32.CreateCompatibleBitmap(screen, width, height)
    try:
        gdi32.SelectObject(memory, bitmap)
        if not user32.PrintWindow(hwnd, memory, 3):   # PW_CLIENTONLY | PW_RENDERFULLCONTENT
            return None
        pixels = ctypes.create_string_buffer(width * height * 4)
        header = Header(ctypes.sizeof(Header), width, -height, 1, 32, 0, 0, 0, 0, 0, 0)
        gdi32.GetDIBits(memory, bitmap, 0, height, pixels, ctypes.byref(header), 0)
        return Image.frombuffer("RGB", (width, height), pixels, "raw", "BGRX", 0, 1)
    finally:
        gdi32.DeleteObject(bitmap)
        gdi32.DeleteDC(memory)
        user32.ReleaseDC(hwnd, screen)


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
    """dashboard_data.limit_rows of the rows, in English or German."""
    with mock.patch("haivequeen.i18n.machine_language", return_value="German" if german else "English"):
        return dashboard_data.limit_rows({"provider_limits": list(provider_rows)})


class DataTest(unittest.TestCase):
    """The data of the rows (texts, levels, order)."""

    def test_one_row_per_provider_in_order_with_name_and_model(self):
        """The rows follow the order of the providers; each has its name, the medium model or 'no medium model'."""
        rows = rows_of(prow(name="codex", model="gpt"), prow(name="claude"), prow(name="local", model=None))
        self.assertEqual([r["name"] for r in rows], ["Codex", "Claude", "Local"])
        self.assertEqual([r["model"] for r in rows], ["gpt", "claude-sonnet", "no medium model"])
        self.assertEqual(rows_of(prow(model=None), german=True)[0]["model"], "kein Medium-Modell")

    def test_reserve_is_whole_percent_with_colour_and_hover(self):
        """Factor 1.27 gives +27 % (green), 0.85 gives minus 15 % (red); the hover sentence names the date; no bare factor."""
        up = rows_of(prow(ratio=1.27))[0]["reserve"]
        down = rows_of(prow(ratio=0.85))[0]["reserve"]
        self.assertEqual((up["text"], up["level"]), ("+27 %", "ok"))
        self.assertEqual((down["text"], down["level"]), (MINUS + "15 %", "bad"))
        date = time.strftime("%d.%m. %H:%M", time.localtime(prow()["reset_at"]))
        self.assertEqual(up["hover"], "27 %% more quota left than time until the reset (%s)" % date)
        self.assertEqual(down["hover"], "15 %% less quota left than time until the reset (%s)" % date)
        self.assertNotIn("1.27", json.dumps(rows_of(prow(ratio=1.27))))
        self.assertNotIn("1,27", json.dumps(rows_of(prow(ratio=1.27), german=True)))

    def test_zero_after_rounding_is_green(self):
        """A factor of 0.996 shows +0 % in green."""
        res = rows_of(prow(ratio=0.996))[0]["reserve"]
        self.assertEqual((res["text"], res["level"]), ("+0 %", "ok"))

    def test_no_limit_source_and_switched_off_check(self):
        """A provider without limit source shows +99 % in green and 'no limit' in muted colour."""
        row = rows_of(prow(has_limit=False, used_pct=None, ratio=None, hours=None, reset_at=None))[0]
        self.assertEqual((row["reserve"]["text"], row["reserve"]["level"]), ("+99 %", "ok"))
        self.assertEqual((row["weekly"]["text"], row["weekly"]["pct"], row["weekly"]["level"]), ("no limit", None, "dim"))

    def test_last_verified_reading_keeps_colour_and_shows_age(self):
        """The reserve of a last verified reading keeps green/red and has its age; without reset time it is unknown with the age."""
        old = rows_of(prow(ratio=0.85, stale=True, stale_hours=2.0, error="x", age_minutes=120))[0]["reserve"]
        self.assertEqual((old["text"], old["level"], old["age"]), (MINUS + "15 %", "bad", "Last reading: 2h"))
        young = rows_of(prow(ratio=1.1, error="x", age_minutes=15))[0]["reserve"]
        self.assertEqual((young["text"], young["level"], young["age"]), ("+10 %", "ok", "Last reading: 15min"))
        none = rows_of(prow(ratio=None, hours=None, reset_at=None, stale=True, stale_hours=1.0, error="x", age_minutes=60))[0]["reserve"]
        self.assertEqual((none["text"], none["age"]), ("unknown", "Last reading: 1h"))

    def test_without_reading_shows_the_error_or_unknown(self):
        """No reading shows 'unknown' with the stored error in its hover; the weekly line says '7d unknown'."""
        gone = dict(used_pct=None, ratio=None, hours=None, reset_at=None)
        row = rows_of(prow(error="Claude usage refresh failed: x", **gone))[0]
        self.assertEqual(row["reserve"]["text"], "unknown")
        self.assertEqual(row["reserve"]["hover"], "Claude usage refresh failed: x")
        self.assertEqual(row["weekly"]["text"], "7d unknown")
        self.assertEqual(rows_of(prow(**gone))[0]["reserve"]["text"], "unknown")

    def test_reset_due_now(self):
        """With no time left the weekly line says 'reset now' and the reserve remains a percentage."""
        row = rows_of(prow(hours=0.0, ratio=float("inf")))[0]
        self.assertEqual(row["reserve"]["text"], "+99 %")
        self.assertEqual(row["weekly"]["text"], "7d 40 % used · 60 % left · reset now")

    def test_reading_without_reset_time(self):
        """A reading without reset time gives the reserve 'unknown' and 'reset unknown' in the weekly line."""
        row = rows_of(prow(ratio=None, hours=None, reset_at=None))[0]
        self.assertEqual(row["reserve"]["text"], "unknown")
        self.assertEqual(row["weekly"]["text"], "7d 40 % used · 60 % left · reset unknown")

    def test_weekly_line_and_colour_by_used_share(self):
        """The weekly line holds percent and reset; the colour follows the used share as before."""
        date = time.strftime("%d.%m. %H:%M", time.localtime(prow()["reset_at"]))
        self.assertEqual(rows_of(prow(used_pct=60.0))[0]["weekly"], {"pct": 60, "level": "warn", "text": "7d 60 % used · 40 % left · " + date})
        self.assertEqual([rows_of(prow(used_pct=u))[0]["weekly"]["level"] for u in (10.0, 70.0, 90.0)], ["ok", "warn", "bad"])

    def test_five_hour_line(self):
        """The 5h percent, reset status and age of a kept reading."""
        self.assertEqual(rows_of(prow(five_hour={"pct": 12, "reset": ""}))[0]["five"], "5h 12 % used")
        self.assertEqual(rows_of(prow(five_hour={"pct": None, "reset": "new window"}))[0]["five"], "5h reset now")
        self.assertEqual(rows_of(prow())[0]["five"], "")
        self.assertEqual(rows_of(prow(five_hour={"pct": 12, "reset": ""}, error="x", age_minutes=5))[0]["five"], "5h 12 % used · 5min")
        self.assertEqual(rows_of(prow(five_hour={"pct": 12, "reset": ""}, stale=True, stale_hours=1.0))[0]["five"], "5h 12 % used · 1h")

    def test_slots(self):
        """Totals, used and the extra agents above the slot count; 0 slots says 'no slots' and the running agents."""
        self.assertEqual(rows_of(prow(used_slots=4, total_slots=3))[0]["slots"], {"total": 3, "used": 4, "extra": 1, "none": "", "running": "", "blocked": False})
        zero = rows_of(prow(used_slots=2, total_slots=0))[0]["slots"]
        self.assertEqual((zero["none"], zero["running"]), ("no slots", "2 running"))
        self.assertEqual(rows_of(prow(used_slots=0, total_slots=0))[0]["slots"]["running"], "")

    def test_german_texts(self):
        """Every new text exists in German as listed."""
        de = rows_of(prow(ratio=1.27), prow(ratio=0.85), prow(has_limit=False, used_pct=None, ratio=None, hours=None, reset_at=None, total_slots=0, used_slots=2),
                     prow(used_pct=None, ratio=None, hours=None, reset_at=None), prow(hours=0.0, ratio=float("inf"), five_hour={"pct": None, "reset": ""}),
                     prow(ratio=None, hours=None, reset_at=None, five_hour={"pct": None, "reset": ""}), german=True)
        date = time.strftime("%d.%m. %H:%M", time.localtime(prow()["reset_at"]))
        self.assertEqual(de[0]["reserve"]["hover"], "27 %% mehr Quota übrig als Zeit bis zum Reset (%s)" % date)
        self.assertEqual(de[1]["reserve"]["hover"], "15 %% weniger Quota übrig als Zeit bis zum Reset (%s)" % date)
        self.assertEqual((de[2]["reserve"]["text"], de[2]["weekly"]["text"], de[2]["slots"]["none"], de[2]["slots"]["running"]),
                         ("+99 %", "kein Limit", "keine Slots", "2 laufen"))
        self.assertEqual((de[3]["reserve"]["text"], de[3]["weekly"]["text"]), ("unbekannt", "7d unbekannt"))
        self.assertEqual((de[4]["reserve"]["text"], de[4]["weekly"]["text"], de[4]["five"]), ("+99 %", "7T 40 % verbraucht · 60 % übrig · Reset jetzt", "5h Reset jetzt"))
        self.assertEqual(de[5]["weekly"]["text"], "7T 40 % verbraucht · 60 % übrig · Reset unbekannt")
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
            return rows, dashboard_data.limit_rows({"provider_limits": rows})

    @staticmethod
    def provider(name, slots, limit, model="m"):
        """A provider setting."""
        return {"name": name, "protocol": name if name in ("claude", "codex") else "claude", "use": "preferred", "slots": slots, "limit": limit,
                "classes": {"medium": {"model": model}}}

    def test_next_badge_on_the_picked_provider_only(self):
        """Exactly the provider select_provider picks is marked next; with no free slot no row is."""
        readings = {"claude": {"usedPercent": 60, "resetHours": 50}, "codex": {"usedPercent": 10, "resetHours": 100}}
        _raw, rows = self.lines(readings, [self.provider("claude", 2, "claude"), self.provider("codex", 2, "codex")])
        self.assertEqual(sum(r["next"] for r in rows), 1)
        _raw, rows = self.lines(readings, [self.provider("claude", 0, "claude"), self.provider("codex", 0, "codex")])
        self.assertEqual([r["next"] for r in rows], [False, False])

    def test_five_hour_percent_at_every_provider_with_that_source(self):
        """Every provider with a limit source that reports a 5-hour window shows its percent; one without shows none."""
        readings = {"claude": {"usedPercent": 60, "resetHours": 50, "fiveHourPercent": 12}, "codex": {"usedPercent": 10, "resetHours": 100, "fiveHourPercent": 33},
                    "other": {"usedPercent": 10, "resetHours": 100}}
        providers = [self.provider("claude", 2, "claude"), self.provider("codex", 2, "codex"), self.provider("other", 2, "codex")]
        _raw, rows = self.lines(readings, providers)
        self.assertEqual([r["five"] for r in rows], ["5h 12 % used", "5h 33 % used", ""])
        self.assertEqual(len([r for r in rows]), 3)   # no separate 5h rows

    def test_codex_five_hour_from_the_stored_reading(self):
        """Without a limits file the 5-hour window of a Codex provider comes from its stored reading."""
        from haivequeen import runtime
        reset = int(time.time()) + 3600
        runtime.kv_set(self.conn, "*", "usage-codex:codex", json.dumps({"fiveHour": {"utilization": 0.25, "resetsAt": reset}}))
        env = {k: v for k, v in os.environ.items() if k not in ("BZZ_RUNTIME_OVERRIDE", "BZZ_PROVIDER_LIMITS_FILE")}
        with mock.patch.dict(os.environ, env, clear=True):
            rows = overview.provider_lines(self.conn, {"providers": [self.provider("codex", 2, "codex")]}, [])
        self.assertTrue(dashboard_data.limit_rows({"provider_limits": rows})[0]["five"].startswith("5h 25 % used · "))

    def test_snapshot_limits_are_the_rows(self):
        """The snapshot holds the rows; a slot that becomes used shows in the next snapshot."""
        cfg = {"slots": 2, "providers": [self.provider("claude", 3, "none")]}
        before = dashboard_data.snapshot(self.conn, cfg=cfg, sysinfo={"cores": 8, "cpu": 1, "ram_used_gb": 1.0, "ram_total_gb": 8.0, "ram_pct": 10,
                                                                    "disk_used_tb": 1.0, "disk_total_tb": 2.0, "disk_pct": 10, "host": "h"})
        self.assertEqual([r["slots"]["used"] for r in before["limits"]], [0])
        from haivequeen import runtime
        runtime.upsert_agent(self.conn, "ODP", "ODP__bee", {"plan": "docs/plans/X-PLAN.md", "phase": "bee", "provider": "claude", "status": "running",
                                                            "startedAt": db.now(), "person": "Sophia"})
        after = dashboard_data.snapshot(self.conn, cfg=cfg, sysinfo={"cores": 8, "cpu": 1, "ram_used_gb": 1.0, "ram_total_gb": 8.0, "ram_pct": 10,
                                                                   "disk_used_tb": 1.0, "disk_total_tb": 2.0, "disk_pct": 10, "host": "h"})
        self.assertEqual([r["slots"]["used"] for r in after["limits"]], [1])


if __name__ == "__main__":
    unittest.main()

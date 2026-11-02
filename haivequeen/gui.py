"""Graphical dashboard of HaiveQueen: a tkinter window (standard library only) that draws the layout of docs/HaiveQueen_Dashboard.png.

It shows the state read by haivequeen/overview.py through haivequeen/gui_data.py.
The logo and icons are cut out of the mockup (assets/gui, made by scripts/make_gui_assets.py). The controls are pause/resume, stop/start, apiary filter and the live view of an agent (Enter or double click on a row).
`bzz gui` opens the window, `bzz start` starts the orchestrator without a terminal window and opens it.
"""
import os
import subprocess
import sys
import textwrap
from datetime import datetime
from pathlib import Path

from . import config, db, gui_data, runstate
from . import platform as osplat

DESIGN_W, DESIGN_H = 1672, 912   # layout coordinates (the mockup without its title bar, a little taller for the status panel); everything scales with the window
REFRESH_MS = 2000
LIVE_MS = 2000   # reload interval of a live view
ASSETS = Path(__file__).resolve().parent.parent / "assets" / "gui"
COL = {"bg": "#000608", "panel": "#010608", "gold": "#f6b800", "edge": "#d8a400", "text": "#e9ecee", "dim": "#98a2a8",
       "cyan": "#00e2ff", "green": "#24fb42", "blue": "#7aa7ff", "amber": "#f0a020", "red": "#ff5a5f", "yellow": "#f9ce3a",
       "select": "#4b3802", "selectedge": "#8a6a10", "track": "#15222c", "seg": "#f9ce3a"}
LEVEL_COL = {"ok": "green", "warn": "yellow", "bad": "red", "dim": "dim", "text": "text"}
STATUS_COL = {"ok": "green", "idle": "blue", "wait": "amber"}
LEVEL_FILL = {"ok": "green", "warn": "yellow", "bad": "red", "slots": "#2b8cff", "dim": "dim"}   # bar colour of a model limit by its level
REPO_URL = "https://github.com/Rokk001/HaiveQueen"   # opened by a click on the repository name in the header
TAG_COL = {"QUEEN": "gold", "BEE": "cyan", "APIARY": "green", "SYSTEM": "green"}
FONTS = ("Cascadia Mono", "Consolas", "Courier New")   # the mockup uses a Cascadia-like monospace font
NO_WINDOW = {"creationflags": 0x08000000} if osplat.is_windows() else {}   # CREATE_NO_WINDOW: helper commands must not flash a console


# Path of the file that holds the process id of the open graphical dashboard.
def lock_file() -> Path:
    """Path of the file that holds the process id of the running graphical dashboard."""
    return config.data_dir() / "gui.pid"


# Tell whether another graphical dashboard window is open.
def running_pid() -> int:
    """Process id of a running graphical dashboard, or 0 when none runs."""
    try:
        pid = int(lock_file().read_text(encoding="ascii").strip())
    except (OSError, ValueError):
        return 0
    return pid if pid != os.getpid() and osplat.process_alive(pid) else 0


# Start the graphical dashboard as a process of its own, once.
def launch_detached() -> dict:
    """Open the graphical dashboard in a detached process; returns {ok, gui, already} (already: a window was open before)."""
    pid = running_pid()
    if pid:
        return {"ok": True, "gui": pid, "already": True}
    flags = {"creationflags": 0x00000008 | 0x00000200} if osplat.is_windows() else {"start_new_session": True}   # DETACHED_PROCESS | CREATE_NEW_PROCESS_GROUP
    exe = osplat.python_executable()
    windowless = Path(exe).with_name("pythonw.exe")
    if osplat.is_windows() and windowless.is_file():
        exe = str(windowless)
    proc = subprocess.Popen([exe, "-m", "haivequeen", "gui"], stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, **flags)
    return {"ok": True, "gui": proc.pid, "already": False}


# Find the zoom/subsample pair (tk can only scale pictures by whole-number ratios) closest to a scale factor.
def best_fraction(factor) -> tuple:
    """(zoom, subsample) with both <= 8 and a ratio closest to `factor` but not above it (a picture must never be larger than its layout slot)."""
    fits = [(n, m) for n in range(1, 9) for m in range(1, 9) if n / m <= factor * 1.005]
    return max(fits, key=lambda nm: nm[0] / nm[1]) if fits else (1, 8)


# The read-only live text window of one agent.
class LiveView:
    """A tkinter Toplevel that shows the last gui_data.LIVE_LINES lines of an agent's live file and reloads it every LIVE_MS milliseconds."""

    # Build the window and start its reload timer.
    def __init__(self, root, title, path, mono):
        """Create the window on `root` for the live file `path`; `mono` is the monospace font family of the dashboard."""
        import tkinter as tk
        self.path, self.shown, self.job, self.follow = path, None, None, True
        self.win = tk.Toplevel(root)
        self.win.title(title)
        self.win.geometry("900x520")
        self.win.configure(bg=COL["bg"])
        bar = tk.Scrollbar(self.win)
        bar.pack(side="right", fill="y")
        self.box = tk.Text(self.win, bg=COL["panel"], fg=COL["text"], font=(mono, 11), wrap="word", state="disabled", relief="flat",
                           highlightthickness=1, highlightbackground=COL["edge"], yscrollcommand=bar.set, padx=10, pady=8)
        self.box.pack(side="left", fill="both", expand=True)
        bar.config(command=self.box.yview)
        for tag in set(gui_data.LIVE_COLOURS.values()):
            self.box.tag_configure(tag, foreground=COL[tag])
        self.win.bind("<Destroy>", self.on_destroy)
        self.box.bind("<Configure>", self.on_configure)
        self.tick()

    # Keep the end in view while the window is laid out or resized.
    def on_configure(self, _event):
        """Scroll to the end again after a resize when the view was following the end."""
        if self.follow:
            self.box.yview_moveto(1.0)

    # Tell whether the window still exists.
    def alive(self) -> bool:
        """True while the window is open."""
        try:
            return bool(self.win.winfo_exists())
        except Exception:   # the interpreter or the window may be gone
            return False

    # Bring the window to the front.
    def focus(self):
        """Raise the window and give it the focus."""
        self.win.deiconify()
        self.win.lift()
        self.win.focus_force()

    # Cancel the reload timer when the window closes.
    def on_destroy(self, event):
        """Stop reloading once the window is destroyed."""
        if event.widget is self.win and self.job:
            try:
                self.win.after_cancel(self.job)
            except Exception:   # the interpreter may already be gone
                pass
            self.job = None

    # Reload the file and show it.
    def refresh(self):
        """Show the current lines (or a hint when there are none) and follow the end when the view was at the end."""
        lines = gui_data.live_lines(self.path)
        if lines == self.shown:
            return
        self.follow = self.shown is None or self.box.yview()[1] >= 0.999   # the first load always shows the end
        self.shown = lines
        self.box.config(state="normal")
        self.box.delete("1.0", "end")
        if not lines:
            self.box.insert("end", "No live output yet (%s is missing or empty)." % self.path, "dim")
        for colour, text in lines:
            self.box.insert("end", text + chr(10), colour)
        self.box.config(state="disabled")
        if self.follow:
            self.box.update_idletasks()   # lay the new text out first, else the end is not known yet
            self.box.yview_moveto(1.0)

    # Reload now and again after LIVE_MS milliseconds.
    def tick(self):
        """Reload and schedule the next reload."""
        self.refresh()
        self.job = self.win.after(LIVE_MS, self.tick)


# The window itself.
class Dashboard:
    """The tkinter window: one canvas redrawn from a snapshot every REFRESH_MS milliseconds and after every resize."""

    # Build the window, remember the database and start the refresh timer.
    def __init__(self, root, conn, project=None):
        """Create the canvas on `root`, read from `conn` and show `project` (None = all Apiaries)."""
        import tkinter as tk
        from tkinter import font as tkfont
        self.root, self.conn, self.project = root, conn, project
        self.snap, self.scale, self.hits, self.images, self.resize_job = None, 1.0, [], {}, None
        self.mono = next((f for f in FONTS if f in tkfont.families(root)), "TkFixedFont")
        self.sel = {"hives": 0, "bees": 0}
        self.table, self.live = "hives", {}   # table that Enter acts on; open live windows by (project, agent)
        self.autoscroll, self.cleared_at, self.error = True, 0, ""
        self.scroll, self.rooms, self.bars, self.areas, self.drag = {}, {}, {}, [], None   # scroll offsets, visible rows, drawn scroll bars, wheel areas, dragged bar
        self.canvas = tk.Canvas(root, bg=COL["bg"], highlightthickness=0)
        self.canvas.pack(fill="both", expand=True)
        self.canvas.bind("<Configure>", self.on_resize)
        self.canvas.bind("<Button-1>", self.on_click)
        self.canvas.bind("<Double-Button-1>", self.on_double)
        self.canvas.bind("<Button-3>", self.on_context)
        self.canvas.bind("<B1-Motion>", self.on_motion)
        self.canvas.bind("<ButtonRelease-1>", self.on_release)
        self.canvas.bind("<MouseWheel>", self.on_wheel)
        root.bind("<Key>", self.on_key)
        self.canvas.bind("<Destroy>", self.on_destroy)
        self.tick_job = None
        self.tick()

    # Cancel the pending redraw and refresh when the window closes.
    def on_destroy(self, _event):
        """Cancel the delayed jobs so nothing fires on a closed window."""
        for job in (self.resize_job, self.tick_job):
            if job:
                try:
                    self.root.after_cancel(job)
                except Exception:   # the interpreter may already be gone
                    pass
        self.resize_job = self.tick_job = None

    # Redraw once the window size stopped changing.
    def on_resize(self, _event):
        """Wait until the size settles, then redraw (scaling the pictures is slow)."""
        if self.resize_job:
            self.root.after_cancel(self.resize_job)
        self.resize_job = self.root.after(80, self.draw)

    # Read a fresh snapshot and draw it.
    def refresh(self):
        """Read a new snapshot and redraw; a failed read shows its error in the footer instead of closing the window."""
        try:
            self.snap, self.error = gui_data.snapshot(self.conn, self.project), ""
        except Exception as exc:   # a failed read must not close the window
            self.error = "error: %s" % exc
        self.draw()

    # Refresh and schedule the next round.
    def tick(self):
        """Refresh now and again after REFRESH_MS milliseconds."""
        self.refresh()
        self.tick_job = self.root.after(REFRESH_MS, self.tick)

    # ---- drawing helpers ----

    # Scale a design length to pixels.
    def px(self, v):
        """Pixels of a design length."""
        return v * self.scale

    # Draw one piece of text at design coordinates.
    def text(self, x, y, s, size=15, col="text", anchor="nw", bold=False, underline=False):
        """Draw text at design position (x, y) and return the canvas item id."""
        style = " ".join(word for word, on in (("bold", bold), ("underline", underline)) if on) or "normal"
        font = (self.mono, -max(8, round(self.px(size))), style)
        return self.canvas.create_text(self.px(x), self.px(y), text=s, fill=COL.get(col, col), anchor=anchor, font=font)

    # Draw a rectangle at design coordinates.
    def rect(self, x0, y0, x1, y1, fill="", outline="", width=1):
        """Draw a rectangle; fill and outline are colour names of COL or hex colours."""
        return self.canvas.create_rectangle(self.px(x0), self.px(y0), self.px(x1), self.px(y1), fill=COL.get(fill, fill),
                                            outline=COL.get(outline, outline), width=width)

    # Draw a dot at design coordinates.
    def dot(self, x, y, colour, r=5):
        """Draw a filled circle of radius r around (x, y)."""
        self.canvas.create_oval(self.px(x - r), self.px(y - r), self.px(x + r), self.px(y + r), fill=COL[colour], outline="")

    # Load a picture of assets/gui scaled to the window.
    def picture(self, name):
        """The PhotoImage of assets/gui/<name>.png scaled by the current zoom/subsample pair, or None when the file is missing."""
        key = (name, self.fraction)
        if key not in self.images:
            try:
                img = self.tk.PhotoImage(file=str(ASSETS / (name + ".png")))
                zoom, sub = self.fraction
                self.images[key] = img.zoom(zoom).subsample(sub) if (zoom, sub) != (1, 1) else img
            except self.tk.TclError:
                self.images[key] = None
        return self.images[key]

    # Draw a picture at design coordinates.
    def pic(self, name, x, y, anchor="nw"):
        """Draw assets/gui/<name>.png with its top-left corner (or `anchor`) at the design position; nothing when the file is missing."""
        img = self.picture(name)
        if img:
            self.canvas.create_image(self.px(x), self.px(y), image=img, anchor=anchor)

    # Draw a bordered panel with its heading, subtitle and hotkey hints.
    def panel(self, x0, y0, x1, y1, title, sub="", keys=(), icon="panel_icon"):
        """Draw a panel frame with the mockup's heading icon `icon`, `title` (gold), `sub` (dim) and right-aligned hints [(key, label)]."""
        self.rect(x0, y0, x1, y1, fill="panel", outline="edge", width=2)
        self.pic(icon, x0 + 12, y0 + 10)
        size = max(13, min(20, (x1 - x0 - 70) / (0.62 * len(title))))   # long titles shrink to fit narrow panels
        self.text(x0 + 52, y0 + 26, title, size, "gold", "w")
        if sub:
            self.text(x0 + 52 + 0.62 * size * len(title) + 14, y0 + 26, "| " + sub, 15, "dim", "w")
        x = x1 - 16
        for key, label in reversed(keys):
            self.text(x, y0 + 26, label, 15, "dim", "e")
            x -= 9 * len(label) + 8
            self.text(x, y0 + 26, "[%s]" % key, 15, "cyan", "e")
            x -= 9 * (len(key) + 2) + 22

    # Draw a horizontal bar with a dark track and filled segments.
    def bar(self, x, y, w, h, frac, fill="seg", segments=0):
        """Draw a progress bar of design width w; `segments` > 0 cuts the filled part into blocks."""
        self.rect(x, y, x + w, y + h, fill="track")
        width = w * max(0.0, min(1.0, frac))
        if width <= 0:
            return
        if not segments:
            self.rect(x, y, x + width, y + h, fill=fill)
            return
        step = w / segments
        for i in range(max(1, round(width / step))):
            self.rect(x + i * step + 1, y, x + (i + 1) * step - 1, y + h, fill=fill)

    # Remember a clickable area.
    def hit(self, x0, y0, x1, y1, action, arg=None):
        """Register the design rectangle as clickable; a click calls `action(arg)`."""
        self.hits.append((self.px(x0), self.px(y0), self.px(x1), self.px(y1), action, arg))

    # Cut a text to the characters that fit a design width.
    def fit(self, s, width, size):
        """`s` cut with an ellipsis so that it fits `width` design pixels at font size `size`."""
        room = max(4, int(width / (0.6 * size)))
        return s if len(s) <= room else s[:room - 1] + "…"

    # ---- sections ----

    # Draw the whole window from the last snapshot.
    def draw(self):
        """Redraw every section at the current window size."""
        import tkinter as tk
        self.tk = tk
        c = self.canvas
        self.scale = max(0.3, min(c.winfo_width() / DESIGN_W, c.winfo_height() / DESIGN_H))
        self.fraction = best_fraction(self.scale)
        c.delete("all")
        self.hits, self.bars, self.areas = [], {}, []
        if self.snap is None:
            self.text(20, 20, self.error or "loading…", 18, "dim")
            return
        s = self.snap
        self.header(s)
        self.tiles(s)
        self.hive_table(s, 10, 267, 1035, 507)
        self.bee_table(s, 10, 515, 1035, 700)
        self.activity(s, 10, 710, 505, 850)
        self.system(s, 515, 710, 837, 850)
        self.queue(s, 846, 710, 1035, 850)
        self.log_panel(s, 1045, 165, 1662, 385)
        self.limits_panel(s, 1045, 395, 1662, 600)
        self.status_panel(s, 1045, 610, 1662, 850)
        self.footer()

    # Draw the title block: the picture of the mockup plus the version line.
    def header(self, s):
        """Draw the header picture (logo, wordmark, honeycomb, slogan) and the version line over it."""
        self.pic("header", 0, 0)
        first = self.text(342, 143, "v%s  |  bzz  |  " % s["version"], 17, "cyan", "w")
        left = self.canvas.bbox(first)[2] / self.scale   # the link starts where the first part ends (design units)
        link = self.text(left, 143, "Rokk001/HaiveQueen", 17, "cyan", "w", underline=True)
        self.hit(left - 4, 130, self.canvas.bbox(link)[2] / self.scale + 4, 156, lambda _a: self.open_repo())

    # Draw the five counter tiles.
    def tiles(self, s):
        """Draw the Apiaries, Hives, Queens, Busy Bees and Total I/O tiles."""
        t = s["tiles"]
        io = "%s / %s" % (gui_data.fmt_tokens(t["tokens_in"]), gui_data.fmt_tokens(t["tokens_out"]))
        items = (("APIARIES", str(t["apiaries"]), "active", 10, 207, "t_apiaries"), ("HIVES", str(t["hives"]), "active", 215, 407, "t_hives"),
                 ("QUEENS", str(t["queens"]), "online", 417, 602, "t_queens"), ("BUSY BEES", "%d / %d" % (t["bees"], t["bee_capacity"]), "active", 610, 812, "t_bees"),
                 ("TOTAL I/O", io, "in / out (today)", 821, 1035, "t_io"))
        for title, value, sub, x0, x1, icon in items:
            self.rect(x0, 165, x1, 255, fill="panel", outline="edge", width=2)
            self.pic(icon, x0 + 10, 172)
            self.text(x0 + 82, 183, title, 17, "text", "nw")
            self.text(x0 + 82, 208, value, min(28, (x1 - x0 - 90) / (0.62 * len(value))), "cyan", "nw")
            self.text(x0 + 82, 236, sub, min(15, (x1 - x0 - 90) / (0.6 * len(sub))), "dim", "nw")

    # ---- scrolling ----

    # Return a scrollable list's first visible row, limited to what exists.
    def offset(self, key, total, room):
        """Clamp and return the first visible row of the scrollable `key` when `total` rows exist and `room` fit; `room` is remembered for the keys."""
        self.rooms[key] = room
        self.scroll[key] = max(0, min(self.scroll.get(key, 0), max(0, total - room)))
        return self.scroll[key]

    # Make a panel react to the mouse wheel.
    def area(self, key, x0, y0, x1, y1):
        """Register the design rectangle: the mouse wheel over it scrolls the list `key`."""
        self.areas.append((self.px(x0), self.px(y0), self.px(x1), self.px(y1), key))

    # Draw a vertical scroll bar when not everything fits.
    def scrollbar(self, key, x, y0, y1, total, room):
        """Draw a scroll bar (design width 8) at x between y0 and y1 when `total` rows exceed `room`; its thumb can be dragged or clicked."""
        if total <= room:
            return
        self.rect(x, y0, x + 8, y1, fill="track")
        thumb = max(18.0, (y1 - y0) * room / total)
        top = y0 + (y1 - y0 - thumb) * self.scroll.get(key, 0) / (total - room)
        self.rect(x, top, x + 8, top + thumb, fill="edge")
        self.bars[key] = (self.px(x - 4), self.px(y0), self.px(x + 12), self.px(y1), total, room, self.px(thumb))

    # Draw a context fill bar with its percentage.
    def context_bar(self, x, y, ctx, limit, w=50):
        """Bar of the used share of a context window with the percentage beside it; a dash when the limit is unknown."""
        if not limit:
            self.text(x, y, "–", 13, "dim", "w")
            return
        pct = round(100 * ctx / limit)
        self.bar(x, y - 7, w, 14, ctx / limit, "green" if pct < 60 else "yellow" if pct <= 85 else "red")
        self.text(x + w + 6, y, "%d%%" % pct, 13, "cyan", "w")

    # ---- tables and panels ----

    # Draw the table of active Hives.
    def hive_table(self, s, x0, y0, x1, y1):
        """Draw the Hive table: Queen, Bees, progress (finished packages), the Queen's context and tokens, status and runtime; details of the selected row below."""
        rows = s["hives"]
        sub = "%d hives across %d apiaries" % (len(rows), s["tiles"]["apiaries"]) + ("  (%s)" % s["project"] if s["project"] else "")
        state = s["run_state"]
        self.panel(x0, y0, x1, y1, "ACTIVE HIVES", sub if state == "RUNNING" else state + "  " + sub, (("Enter", "live"), ("P", "pause/resume"), ("Space", "plan"), ("F", "apiary")))
        for name, x in (("#", 24), ("HIVE", 90), ("APIARY", 218), ("QUEEN", 312), ("BEES", 386), ("PROGRESS", 436), ("CONTEXT", 576), ("TOKENS", 670), ("STATUS", 772)):
            self.text(x0 + x, y0 + 56, name, 13, "dim", "w")
        self.pick_clamp("hives", len(rows))
        room = 6
        off = self.offset("hives", len(rows), room)
        self.area("hives", x0, y0, x1, y1)
        self.scrollbar("hives", x1 - 16, y0 + 70, y0 + 82 + room * 25 - 12, len(rows), room)
        for n, r in enumerate(rows[off:off + room]):
            i, y = off + n, y0 + 82 + n * 25
            chosen = i == self.sel["hives"]
            if chosen:
                self.rect(x0 + 8, y - 12, x1 - 22, y + 12, fill="select", outline="selectedge")
            self.hit(x0 + 8, y - 12, x1 - 22, y + 12, self.select, ("hives", i))
            colour = STATUS_COL[r["kind"]]
            self.text(x0 + 24, y, str(i + 1), 15, "dim", "w")
            self.pic("row_icon" if chosen else "row_icon_dim", x0 + 58, y, "w")
            self.text(x0 + 90, y, r["name"][:15], 14, "gold" if chosen else "text", "w")
            self.text(x0 + 218, y, r["apiary"][:10], 14, "cyan", "w")
            self.text(x0 + 312, y, r["queen"][:9], 14, "text", "w")
            self.text(x0 + 386, y, "%d/%d" % (r["bees_running"], r["bees_total"]), 14, "text", "w")
            self.bar(x0 + 436, y - 7, 70, 14, r["progress"] / 100, "seg", 7)
            self.text(x0 + 513, y, "%d/%d" % (r["done"], r["total"]), 13, "text", "w")
            self.context_bar(x0 + 576, y, r["queen_ctx"], r["queen_ctx_limit"])
            self.text(x0 + 670, y, "%s/%s" % (gui_data.fmt_tokens(r["queen_tin"]), gui_data.fmt_tokens(r["queen_tout"])), 13, "cyan", "w")
            self.dot(x0 + 778, y, colour)
            self.text(x0 + 790, y, self.fit(r["status"], 145 if r["runtime"] else 200, 12), 12, colour, "w")
            self.text(x1 - 26, y, r["runtime"], 14, "text", "e")
        if rows:
            r = rows[self.sel["hives"]]
            ctx = "ctx %s/%s" % (gui_data.fmt_tokens(r["queen_ctx"]), gui_data.fmt_tokens(r["queen_ctx_limit"])) if r["queen_ctx_limit"] else "ctx -"
            line = "%s | %s | packages %d done, %d implemented%s%s of %d | Queen %s%s | %s | queen in/out %s/%s | hive total in/out %s/%s | %s" % (
                r["name"], r["apiary"], r["done"], r["implemented"], ", %d rejected" % r["rejected"] if r["rejected"] else "",
                ", %d waiting (%s)" % (r["waiting"], r["waiting_reasons"]) if r["waiting"] else "", r["total"], r["queen"], " " + r["queen_model"] if r["queen_model"] else "", ctx,
                gui_data.fmt_tokens(r["queen_tin"]), gui_data.fmt_tokens(r["queen_tout"]), gui_data.fmt_tokens(r["hive_tin"]), gui_data.fmt_tokens(r["hive_tout"]), r["status"])
            self.text(x0 + 24, y1 - 14, self.fit(line, x1 - x0 - 48, 12), 12, "dim", "w")
        else:
            self.text(x0 + 24, y0 + 90, "no active hives", 15, "dim", "w")

    # Draw the table of busy Bees.
    def bee_table(self, s, x0, y0, x1, y1):
        """Draw the Bees (running processes and packages with a Bee assigned) with model, Hive, task, context, tokens and runtime; the selected running Bee can be stopped with X."""
        rows = s["bees"]
        running = sum(1 for b in rows if b["status"] == "running")
        self.panel(x0, y0, x1, y1, "BUSY BEES", "%d running, %d waiting across %d hive%s" % (running, len(rows) - running, len({b["hive"] for b in rows}), "" if len({b["hive"] for b in rows}) == 1 else "s"), (("Enter", "live"), ("X", "stop")))
        for name, x in (("#", 24), ("BEE", 80), ("MODEL", 170), ("HIVE", 300), ("TASK", 410), ("CONTEXT", 545), ("TOKENS", 655), ("STATUS", 765), ("RUNTIME", 900)):
            self.text(x0 + x, y0 + 56, name, 13, "dim", "w")
        self.pick_clamp("bees", len(rows))
        room = 5
        off = self.offset("bees", len(rows), room)
        self.area("bees", x0, y0, x1, y1)
        self.scrollbar("bees", x1 - 16, y0 + 66, y0 + 76 + room * 22 - 8, len(rows), room)
        for n, r in enumerate(rows[off:off + room]):
            i, y = off + n, y0 + 76 + n * 22
            chosen = i == self.sel["bees"]
            if chosen:
                self.rect(x0 + 8, y - 10, x1 - 22, y + 10, fill="select", outline="selectedge")
            self.hit(x0 + 8, y - 10, x1 - 22, y + 10, self.select, ("bees", i))
            colour = "green" if r["status"] == "running" else "amber"
            self.text(x0 + 24, y, str(i + 1), 14, "dim", "w")
            self.pic("row_icon" if chosen else "row_icon_dim", x0 + 50, y, "w")
            self.text(x0 + 80, y, r["name"][:11], 14, "gold" if chosen else "text", "w")
            self.text(x0 + 170, y, r["model"][:16], 14, "text", "w")
            self.text(x0 + 300, y, r["hive"][:14], 14, "text", "w")
            self.text(x0 + 410, y, r["task"][:15], 14, "text", "w")
            self.context_bar(x0 + 545, y, r["ctx"], r["ctx_limit"])
            self.text(x0 + 655, y, "%s/%s" % (gui_data.fmt_tokens(r["tin"]), gui_data.fmt_tokens(r["tout"])), 13, "cyan", "w")
            self.dot(x0 + 772, y, colour)
            self.text(x0 + 784, y, r["status"], 14, colour, "w")
            self.text(x0 + 900, y, r["runtime"], 14, "text", "w")
        if not rows:
            self.text(x0 + 24, y0 + 90, "no busy bees", 15, "dim", "w")

    # Draw the token use per Apiary and Hive.
    def activity(self, s, x0, y0, x1, y1):
        """Draw the token use (in/out) of the last seven days per Apiary (cyan names) and per Hive, with the share of each as a bar."""
        self.panel(x0, y0, x1, y1, "HIVE ACTIVITY", "tokens in/out, last 7 days")
        rows = s["activity"]
        room = 4
        off = self.offset("activity", len(rows), room)
        self.area("activity", x0, y0, x1, y1)
        self.scrollbar("activity", x1 - 16, y0 + 50, y1 - 8, len(rows), room)
        for n, r in enumerate(rows[off:off + room]):
            y = y0 + 62 + n * 21
            self.text(x0 + 14, y, r["name"][:15], 14, "cyan" if r["kind"] == "apiary" else "text", "w")
            self.bar(x0 + 150, y - 7, 90, 14, r["pct"] / 100, "seg" if r["pct"] >= 50 else "green", 9)
            self.text(x0 + 246, y, "%d%%" % r["pct"], 14, "cyan", "w")
            self.text(x1 - 30, y, "%s/%s" % (gui_data.fmt_tokens(r["tin"]), gui_data.fmt_tokens(r["tout"])), 14, "text", "e")
        if not rows:
            self.text(x0 + 14, y0 + 66, "no usage yet", 14, "dim", "w")

    # Draw CPU, RAM and disk.
    def system(self, s, x0, y0, x1, y1):
        """Draw the CPU, RAM and disk meters."""
        self.panel(x0, y0, x1, y1, "SYSTEM", icon="icon_system")
        m = s["system"]
        rows = (("CPU", m["cpu"], "(%d cores)" % m["cores"], "#2b8cff", "icon_cpu"), ("RAM", m["ram_pct"], "%.1f/%.0f GB" % (m["ram_used_gb"], m["ram_total_gb"]), "#f9ce3a", "icon_ram"),
                ("Disk", m["disk_pct"], "%.1f/%.1f TB" % (m["disk_used_tb"], m["disk_total_tb"]), "#24fb42", "icon_disk"))
        for i, (name, pct, note, colour, icon) in enumerate(rows):
            y = y0 + 66 + i * 27
            self.pic(icon, x0 + 10, y, "w")
            self.text(x0 + 46, y, name, 15, "text", "w")
            self.text(x0 + 92, y, "%d%%" % pct, 15, "cyan", "w")
            self.text(x0 + 134, y, note, 13, "text", "w")
            self.bar(x1 - 82, y - 8, 70, 16, pct / 100, colour)

    # Draw open questions and queued integrations.
    def queue(self, s, x0, y0, x1, y1):
        """Draw the open question count and the queued integration rows."""
        self.panel(x0, y0, x1, y1, "APIARY QUEUE", icon="icon_queue")
        q = s["queue"]
        self.text(x0 + 14, y0 + 64, "open:   %d" % q["open"], 15, "text", "w")
        self.text(x0 + 14, y0 + 88, "queued: %d" % q["queued"], 15, "text", "w")
        self.text(x0 + 14, y0 + 112, self.fit(", ".join(q["labels"]), x1 - x0 - 28, 15), 15, "text", "w")

    # Draw the live log.
    def log_panel(self, s, x0, y0, x1, y1):
        """Draw the newest events with coloured source tags, a scroll bar and the auto-scroll, clear and save controls."""
        self.rect(x0, y0, x1, y1, fill="panel", outline="edge", width=2)
        self.pic("icon_logs", x0 + 12, y0 + 10)
        self.text(x0 + 52, y0 + 26, "LOGS", 20, "gold", "w")
        self.text(x0 + 130, y0 + 26, "| live", 15, "dim", "w")
        x = x1 - 16
        for key, label, action in (("S", "save", self.save_log), ("C", "clear", self.clear_log)):
            self.text(x, y0 + 26, label, 15, "dim", "e")
            self.text(x - 9 * len(label) - 8, y0 + 26, "[%s]" % key, 15, "cyan", "e")
            self.hit(x - 9 * len(label) - 40, y0 + 12, x, y0 + 40, lambda _a, f=action: f())
            x -= 9 * len(label) + 62
        self.text(x, y0 + 26, "[%s]" % ("On" if self.autoscroll else "Off"), 15, "cyan", "e")
        self.hit(x - 40, y0 + 12, x, y0 + 40, lambda _a: self.toggle_autoscroll())
        self.text(x - 46, y0 + 26, "Auto-scroll", 15, "dim", "e")
        lines = s["log"][self.cleared_at:]
        room = int((y1 - y0 - 60) // 22)
        if self.autoscroll:
            self.scroll["log"] = max(0, len(lines) - room)
        off = self.offset("log", len(lines), room)
        self.area("log", x0, y0, x1, y1)
        self.scrollbar("log", x1 - 16, y0 + 50, y1 - 8, len(lines), room)
        for n, (stamp, tag, msg) in enumerate(lines[off:off + room]):
            y = y0 + 62 + n * 22
            self.text(x0 + 12, y, stamp, 14, "text", "w")
            self.text(x0 + 70, y, self.fit("[%s]" % tag, 165, 14), 14, TAG_COL.get(tag.split()[0], "text"), "w")   # the mockup pads tags to one column
            self.text(x0 + 240, y, self.fit(msg, x1 - x0 - 270, 14), 14, "text", "w")
        if not lines:
            self.text(x0 + 14, y0 + 62, "no log lines", 13, "dim", "w")

    # Draw the model limits as bars.
    def limits_panel(self, s, x0, y0, x1, y1):
        """Draw the Claude/Codex 5-hour windows and per provider the weekly quota and the used slots as bars with a note beside each; scrolls when there are more."""
        self.panel(x0, y0, x1, y1, "MODEL LIMITS", "used share, resets and slots", icon="icon_system")
        rows = s["limits"]
        room = int((y1 - y0 - 56) // 26)
        off = self.offset("limits", len(rows), room)
        self.area("limits", x0, y0, x1, y1)
        self.scrollbar("limits", x1 - 16, y0 + 50, y1 - 8, len(rows), room)
        for n, r in enumerate(rows[off:off + room]):
            y = y0 + 66 + n * 26
            self.text(x0 + 14, y, r["label"][:15], 14, "text", "w")
            fill = LEVEL_FILL.get(r["level"], "dim")
            self.bar(x0 + 150, y - 7, 140, 14, (r["pct"] or 0) / 100, fill, 18)
            self.text(x0 + 298, y, "%d%%" % r["pct"] if r["pct"] is not None else "n/a", 14, "cyan" if r["pct"] is not None else "dim", "w")
            self.text(x0 + 345, y, self.fit(r["note"], x1 - x0 - 345 - 30, 12), 12, "dim", "w")
        if not rows:
            self.text(x0 + 14, y0 + 66, "no limit data", 14, "dim", "w")

    # Draw the status sections.
    def status_panel(self, s, x0, y0, x1, y1):
        """Draw run state, usage, system, plans, questions, round errors and self-update as text lines, with a scroll bar."""
        chars = int((x1 - x0 - 50) / (0.6 * 12))
        flat = []   # (is heading, text, level) after wrapping, so the panel can scroll by line
        for title, lines in s["info"]:
            flat.append((True, title, "gold"))
            for text, level in lines:
                flat += [(False, part, level) for part in textwrap.wrap(text, chars, subsequent_indent="  ") or [""]]
        room = int((y1 - y0 - 62) // 15)
        off = self.offset("status", len(flat), room)
        self.panel(x0, y0, x1, y1, "STATUS", "state, usage, plans and questions")
        self.area("status", x0, y0, x1, y1)
        self.scrollbar("status", x1 - 16, y0 + 50, y1 - 8, len(flat), room)
        for n, (heading, text, level) in enumerate(flat[off:off + room]):
            y = y0 + 58 + n * 15
            self.text(x0 + (14 if heading else 24), y, text, 12, "gold" if heading else LEVEL_COL.get(level, "text"), "w", heading)

    # Draw the command bar at the bottom.
    def footer(self):
        """Draw the bzz command bar (clickable) and the slogan; a read error replaces the slogan."""
        self.rect(0, 860, DESIGN_W, DESIGN_H, fill="#050606", outline="edge")
        self.pic("foot_bee", 8, 864)
        self.text(70, 886, "bzz", 28, "gold", "w", True)
        state = self.snap["orchestrator"]
        colour = {"ok": "green", "warn": "amber", "bad": "red"}[state["level"]]
        self.dot(172, 886, colour, 7)
        label = self.text(188, 886, state["label"], 17, colour, "w", True)
        right = self.canvas.bbox(label)[2] / self.scale
        self.text(right + 8, 886, "▾", 17, "dim", "w")
        self.hit(160, 866, right + 34, 908, lambda _a: self.state_menu())
        x = right + 50
        self.text(x - 22, 886, "|", 17, "dim", "w")
        for name, action in (("status", self.status), ("bees", lambda: self.focus("bees")),
                             ("hives", lambda: self.focus("hives")), ("apiaries" if not self.project else "apiary:" + self.project, self.next_project),
                             ("logs", self.toggle_autoscroll),
                             ("questions", self.open_questions), ("config", self.open_config), ("help", self.help)):
            w = 13 * len(name)
            self.text(x, 886, name, 17, "text", "w")
            self.hit(x - 8, 866, x + w + 8, 908, lambda _a, f=action: f())
            x += w + 14
            self.text(x, 886, "|", 17, "dim", "w")
            x += 22
        self.canvas.create_line(self.px(x + 10), self.px(886), self.px(DESIGN_W - 440), self.px(886), fill=COL["edge"], width=2)
        self.pic("foot_hex", DESIGN_W - 412, 866)
        self.text(DESIGN_W - 22, 886, self.error or "BUILD  •  ORCHESTRATE  •  EVOLVE", 17, "red" if self.error else "gold", "e")

    # ---- actions ----

    # Keep a table selection inside its rows.
    def pick_clamp(self, table, n):
        """Limit the selected row of `table` to the n rows that exist."""
        self.sel[table] = max(0, min(self.sel[table], max(0, n - 1)))

    # Select a table row.
    def select(self, arg):
        """Select row i of table name (arg is the tuple (name, i)), make it the table that Enter acts on and redraw."""
        self.sel[arg[0]] = arg[1]
        self.table = arg[0]
        self.draw()

    # Move the selection to a table.
    def focus(self, table):
        """Select the first row of `table`."""
        self.sel[table] = 0
        self.draw()

    # Send a click to the area under the pointer.
    def on_click(self, event):
        """Grab a scroll bar under the pointer, else run the action of the last registered clickable area that contains it."""
        for key, (bx0, by0, bx1, by1, _total, _room, _thumb) in self.bars.items():
            if bx0 <= event.x <= bx1 and by0 <= event.y <= by1:
                self.drag = key
                self.drag_to(event.y)
                return
        for x0, y0, x1, y1, action, arg in reversed(self.hits):
            if x0 <= event.x <= x1 and y0 <= event.y <= y1:
                action(arg)
                return

    # Move a scroll bar's thumb to the pointer.
    def drag_to(self, y):
        """Scroll the grabbed list so that its thumb is centred on the pointer height `y`; scrolling the log by hand switches auto-scroll off."""
        bx0, by0, bx1, by1, total, room, thumb = self.bars[self.drag]
        span = max(1.0, by1 - by0 - thumb)
        self.scroll[self.drag] = round(max(0.0, min(1.0, (y - by0 - thumb / 2) / span)) * (total - room))
        if self.drag == "log":
            self.autoscroll = False
        self.draw()

    # Follow the pointer while a scroll bar is dragged.
    def on_motion(self, event):
        """Drag the grabbed scroll bar."""
        if self.drag and self.drag in self.bars:
            self.drag_to(event.y)

    # Let go of the scroll bar.
    def on_release(self, _event):
        """End dragging a scroll bar."""
        self.drag = None

    # Open the live view of the row under the pointer.
    def on_double(self, event):
        """A double click on a Hive or Bee row selects it and opens the live view of its agent."""
        for x0, y0, x1, y1, action, arg in reversed(self.hits):
            if x0 <= event.x <= x1 and y0 <= event.y <= y1 and action == self.select:
                self.select(arg)
                self.open_live()
                return

    # Open the Queen menu of the Hive under the pointer.
    def on_context(self, event):
        """A right click on a Hive row selects it and opens its Queen menu."""
        for x0, y0, x1, y1, action, arg in reversed(self.hits):
            if x0 <= event.x <= x1 and y0 <= event.y <= y1 and action == self.select and arg[0] == "hives":
                self.select(arg)
                self.hive_menu()
                return

    # Open the live view of the selected row.
    def open_live(self):
        """Open (or focus) the live text window of the selected row of the table Enter acts on: a Hive shows its running Queen, else its latest one; a Bee its own text."""
        rows = (self.snap or {}).get(self.table) or []
        if not rows:
            return
        row = rows[min(self.sel[self.table], len(rows) - 1)]
        if self.table == "hives":
            project, agent, title = row["apiary"], row.get("queen_agent"), "Queen %s | %s" % (row["queen"], row["name"])
        else:
            project, agent, title = row["project"], row.get("agent"), "Bee %s | %s" % (row["name"], row["hive"])
        if not agent:
            from tkinter import messagebox
            messagebox.showinfo("HaiveQueen", "This row has no agent process yet, so there is no live output.", parent=self.root)
            return
        view = self.live.get((project, agent))
        if view is not None and view.alive():
            view.focus()
            return
        self.live[(project, agent)] = LiveView(self.root, "HaiveQueen | " + title, gui_data.live_path(project, agent), self.mono)

    # Scroll the list under the pointer with the mouse wheel.
    def on_wheel(self, event):
        """Scroll the list under the pointer: one row per notch in the Hive and Bee tables, three rows elsewhere; scrolling the log up switches auto-scroll off."""
        for x0, y0, x1, y1, key in reversed(self.areas):
            if x0 <= event.x <= x1 and y0 <= event.y <= y1:
                step = (1 if key in ("hives", "bees") else 3) * (-1 if event.delta > 0 else 1)
                self.scroll[key] = max(0, self.scroll.get(key, 0) + step)
                if key == "log" and step < 0:
                    self.autoscroll = False
                self.draw()
                return

    # Map keys to actions.
    def on_key(self, event):
        """P pause/resume, Q answer the open questions, Space pause/resume the selected plan, F apiary filter, M queen menu of the selected Hive, X stop the selected Bee, C clear and S save the log, A toggle auto-scroll, Up/Down move the Hive selection, Enter opens the live view."""
        key = (event.keysym or "").lower()
        {"p": self.toggle_pause, "f": self.next_project, "m": self.hive_menu, "space": self.toggle_hive_pause, "x": self.stop_bee, "c": self.clear_log, "s": self.save_log,
         "a": self.toggle_autoscroll, "q": self.open_questions, "up": lambda: self.move(-1), "down": lambda: self.move(1), "return": self.open_live}.get(key, lambda: None)()

    # Move the Hive selection up or down.
    def move(self, step):
        """Move the selected Hive row by `step` rows and scroll the table so that the row stays visible."""
        rows = len(self.snap["hives"]) if self.snap else 0
        self.sel["hives"] = max(0, min(self.sel["hives"] + step, max(0, rows - 1)))
        room, off = self.rooms.get("hives", 6), self.scroll.get("hives", 0)
        self.scroll["hives"] = self.sel["hives"] if self.sel["hives"] < off else max(off, self.sel["hives"] - room + 1)
        self.draw()

    # Ask before doing something that changes the run.
    def confirm(self, text) -> bool:
        """Ask the user a yes/no question in a dialog."""
        from tkinter import messagebox
        return messagebox.askyesno("HaiveQueen", text, parent=self.root)

    # Pause the run or resume it.
    def toggle_pause(self):
        """Pause a running run or resume a paused one after a confirmation (key P)."""
        resume = runstate.get_state(self.conn)["state"] != "RUNNING"
        if self.confirm("Resume HaiveQueen?" if resume else "Pause HaiveQueen? Running agents keep working, no new agent starts."):
            (runstate.resume if resume else runstate.pause)(self.conn, "gui")
            self.refresh()

    # Step through the Apiary filter.
    def next_project(self):
        """Show the next Apiary only; after the last one all Apiaries again."""
        cycle = [None] + list(self.snap["projects"]) if self.snap else [None]
        self.project = cycle[(cycle.index(self.project) + 1) % len(cycle)] if self.project in cycle else None
        self.refresh()

    # Offer the Queen actions of the selected Hive.
    def hive_menu(self):
        """Open a menu at the pointer with Restart Queen and End Queen and Bees for the selected Hive."""
        import tkinter as tk
        rows = (self.snap or {}).get("hives") or []
        if not rows:
            return
        hive = rows[min(self.sel["hives"], len(rows) - 1)]
        menu = tk.Menu(self.root, tearoff=0, bg=COL["panel"], fg=COL["text"], activebackground=COL["select"], activeforeground=COL["gold"], font=(self.mono, 11))
        menu.add_command(label="Restart Queen of %s" % hive["name"], command=lambda: self.queen_action(hive, "requeen"))
        menu.add_command(label="End Queen and Bees of %s" % hive["name"], command=lambda: self.queen_action(hive, "dequeen"))
        menu.add_separator()
        menu.add_command(label=("Resume plan %s" if hive["paused"] else "Pause plan %s") % hive["name"], command=lambda: self.toggle_hive_pause(hive))
        try:
            menu.tk_popup(self.root.winfo_pointerx(), self.root.winfo_pointery())
        finally:
            menu.grab_release()

    # Pause or resume the plan of a Hive.
    def toggle_hive_pause(self, hive=None):
        """Set the status of the Hive's plan file to PAUSED (no new Queen or Bee is started for it; a running Queen keeps working) or back to READY; key Space acts on the selected Hive."""
        from . import runtime
        if hive is None:
            rows = (self.snap or {}).get("hives") or []
            if not rows:
                return
            hive = rows[min(self.sel["hives"], len(rows) - 1)]
        try:
            runtime.set_plan_status(self.conn, hive["apiary"], hive["plan_path"], "READY" if hive["paused"] else "PAUSED")
        except Exception as exc:
            from tkinter import messagebox
            messagebox.showerror("HaiveQueen", "Could not change the plan status: %s" % exc, parent=self.root)
        self.refresh()

    # Restart or end the Queen of a Hive through the bzz command.
    def queen_action(self, hive, command):
        """After a confirmation run `bzz requeen` (a replacement Queen) or `bzz dequeen` (end Queen and own-process Bees) for the Hive in the background and report a failure."""
        ask = {"requeen": "Restart the Queen of %s? A replacement Queen takes over.", "dequeen": "End the Queen and the Bees of %s? The hive stays queenless."}[command]
        if not self.confirm(ask % hive["name"]):
            return
        proc = subprocess.Popen([osplat.python_executable(), "-m", "haivequeen", command, "%s:%s" % (hive["apiary"], hive["plan_path"])], stdin=subprocess.DEVNULL,
                                stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True, encoding="utf-8", errors="replace", **NO_WINDOW)
        self.watch_command(proc, command)

    # Wait for a background command without blocking the window.
    def watch_command(self, proc, command):
        """Poll `proc` until it ends, then refresh the window and show the error text of a failed command."""
        if proc.poll() is None:
            self.root.after(300, lambda: self.watch_command(proc, command))
            return
        out, err = proc.communicate()
        if proc.returncode != 0:
            from tkinter import messagebox
            messagebox.showerror("HaiveQueen", "%s failed: %s" % (command, (err or out or "").strip()[:300] or "exit code %d" % proc.returncode), parent=self.root)
        self.refresh()

    # Stop the selected Bee.
    def stop_bee(self):
        """Kill the process tree of the selected Bee after a confirmation; the runner records the vanished process and the task is retried."""
        rows = self.snap["bees"] if self.snap else []
        if not rows:
            return
        bee = rows[min(self.sel["bees"], len(rows) - 1)]
        found = db.rows(self.conn, "SELECT pid FROM agents WHERE project=? AND name=? AND archived=0", (bee["project"], bee["agent"]))
        if found and found[0]["pid"] and self.confirm("Stop bee %s (%s)?" % (bee["name"], bee["task"])):
            osplat.kill_process_tree(found[0]["pid"])
            self.refresh()

    # Switch the log between following and top-aligned.
    def toggle_autoscroll(self):
        """Toggle the log auto-scroll."""
        self.autoscroll = not self.autoscroll
        self.draw()

    # Hide the lines shown so far.
    def clear_log(self):
        """Hide every log line that exists now; later lines still appear (the window keeps the newest lines only, so the cut is by count)."""
        self.cleared_at = len(self.snap["log"]) if self.snap else 0
        self.draw()

    # Write the log lines to a text file.
    def save_log(self):
        """Ask for a file name and write the visible log lines to it."""
        from tkinter import filedialog
        path = filedialog.asksaveasfilename(parent=self.root, defaultextension=".log", initialfile="haivequeen-%s.log" % datetime.now().strftime("%Y%m%d-%H%M"))
        if path and self.snap:
            Path(path).write_text("\n".join("%s [%s] %s" % ln for ln in self.snap["log"][self.cleared_at:]) + "\n", encoding="utf-8")

    # Open the state button's menu.
    def state_menu(self):
        """Open a menu at the pointer with the actions that fit the current state (see state_actions)."""
        import tkinter as tk
        menu = tk.Menu(self.root, tearoff=0, bg=COL["panel"], fg=COL["text"], activebackground=COL["select"], activeforeground=COL["gold"], font=(self.mono, 11))
        for item in state_actions(self.snap["orchestrator"]["label"]):
            if item is None:
                menu.add_separator()
            elif item[1] is None:
                menu.add_command(label=item[0], state="disabled")
            else:
                menu.add_command(label=item[0], command=getattr(self, item[1]))
        try:
            menu.tk_popup(self.root.winfo_pointerx(), self.root.winfo_pointery())
        finally:
            menu.grab_release()

    # Start the orchestrator loop.
    def start(self):
        """Run `bzz start` in the background (the orchestrator starts without a terminal window, this window stays the dashboard)."""
        subprocess.Popen([osplat.python_executable(), "-m", "haivequeen", "start"], stdin=subprocess.DEVNULL,
                         stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, **NO_WINDOW)

    # Request a graceful stop.
    def stop(self):
        """Run `bzz stop` after a confirmation: running agents finish, no new one starts."""
        if self.confirm("Stop HaiveQueen gracefully? Running agents finish their task first."):
            subprocess.Popen([osplat.python_executable(), "-m", "haivequeen", "stop"], stdin=subprocess.DEVNULL,
                             stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, **NO_WINDOW)

    # Show the run state in a dialog.
    def status(self):
        """Show the run state and the number of running agents."""
        from tkinter import messagebox
        st = runstate.get_state(self.conn)
        messagebox.showinfo("HaiveQueen", "Run state: %s\nRunning agents: %d" % (st["state"], st["running_agents"]), parent=self.root)

    # Open the window that answers the open questions.
    def open_questions(self):
        """Open the questions window (answer the open owner questions, mark rejected steps); a second click brings the open window to the front."""
        from . import gui_questions
        current = getattr(self, "questions_window", None)
        if current is not None and current.win.winfo_exists():
            current.win.lift()
            return
        self.questions_window = gui_questions.QuestionsWindow(self.root, self.conn, self.mono, on_answered=self.refresh)

    # Open the settings editor.
    def open_config(self):
        """Open the settings editor (config.toml and the project.toml files as forms); a second click brings the open editor to the front."""
        from . import gui_config
        current = getattr(self, "config_window", None)
        if current is not None and current.win.winfo_exists():
            current.win.lift()
            return
        config.config_dir().mkdir(parents=True, exist_ok=True)
        self.config_window = gui_config.ConfigWindow(self.root, self.conn, config.config_dir(), self.mono, on_saved=self.refresh)

    # Open the project page.
    def open_repo(self):
        """Open the project's GitHub repository in the default browser."""
        import webbrowser
        webbrowser.open(REPO_URL)

    # Show the keys.
    def help(self):
        """Show the keyboard and mouse controls."""
        from tkinter import messagebox
        messagebox.showinfo("HaiveQueen", "P  pause / resume\nQ  answer the open questions\nSpace  pause / resume the selected plan\nF  next apiary filter\nM / right click  queen menu of the hive\nUp/Down  select hive\nClick  select a row\nEnter / double click  live output of the row\n"
                            "X  stop the selected bee\nC  clear log   S  save log   A  auto-scroll", parent=self.root)


# The actions the state button offers in a given state.
def state_actions(label) -> list:
    """[(menu text, Dashboard method name or None)] for the state shown on the button; a None entry is a separator, a None method a disabled line."""
    if label == "STOPPED":
        return [("Start HaiveQueen", "start")]
    if label == "STOPPING":
        return [("Stopping: running agents finish their task first", None)]
    return [("Resume new starts" if label == "PAUSED" else "Pause new starts", "toggle_pause"), None, ("Stop HaiveQueen", "stop")]


# Give the window the HaiveQueen icon instead of tkinter's feather.
def set_window_icon(root):
    """Set the title bar, taskbar and Alt+Tab icon from assets/gui (app.ico on Windows, app_icon.png elsewhere); keeps the default when a file is missing."""
    import tkinter as tk
    try:
        if osplat.is_windows() and (ASSETS / "app.ico").is_file():
            root.iconbitmap(default=str(ASSETS / "app.ico"))
        else:
            root.app_icon = tk.PhotoImage(file=str(ASSETS / "app_icon.png"))   # keep a reference, otherwise the picture is dropped
            root.iconphoto(True, root.app_icon)
    except tk.TclError:
        pass


# Open the window and run until it is closed.
def main(argv=None) -> int:
    """Entry point of `bzz gui`: open the database, build the window and run its main loop."""
    import tkinter as tk
    args = list(sys.argv[1:] if argv is None else argv)
    project = args[args.index("--project") + 1] if "--project" in args else None
    if running_pid():
        print("The graphical dashboard is already open.", file=sys.stderr)
        return 0
    lock = lock_file()
    lock.parent.mkdir(parents=True, exist_ok=True)
    lock.write_text(str(os.getpid()), encoding="ascii")
    conn = db.connect(config.db_path())
    if osplat.is_windows():
        try:
            import ctypes
            ctypes.windll.shcore.SetProcessDpiAwareness(1)   # draw in real pixels instead of a blurry scaled bitmap
            ctypes.windll.shell32.SetCurrentProcessExplicitAppUserModelID("HaiveQueen.Dashboard")   # own taskbar group and icon instead of Python's
        except (AttributeError, OSError):
            pass
    root = tk.Tk()
    root.title("HaiveQueen | AI Agent Orchestrator")
    set_window_icon(root)
    root.geometry("%dx%d" % (min(DESIGN_W, root.winfo_screenwidth() - 80), min(DESIGN_H, root.winfo_screenheight() - 120)))
    root.configure(bg=COL["bg"])
    Dashboard(root, conn, project)
    try:
        root.mainloop()
    finally:
        conn.close()
        try:
            if lock.read_text(encoding="ascii").strip() == str(os.getpid()):
                lock.unlink()
        except OSError:
            pass
    return 0


if __name__ == "__main__":
    sys.exit(main())

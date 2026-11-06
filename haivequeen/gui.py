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

from . import config, db, gui_data, i18n, runstate
from . import platform as osplat
from .i18n import tr

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
    fits = [(n, m) for n in range(1, 9) for m in range(1, 9) if n / m <= factor]
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
        from . import gui_dialog
        bar = gui_dialog.scrollbar(self.win, None)
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
            self.box.insert("end", tr("No live output yet (%s is missing or empty).", self.path), "dim")
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
        self.fonts = {}   # measuring fonts by pixel size
        self.mono = next((f for f in FONTS if f in tkfont.families(root)), "TkFixedFont")
        from . import gui_dialog
        gui_dialog.apply_style(root, self.mono)   # the questions and settings windows get the dashboard look
        self.sel = {"hives": 0, "bees": 0}
        self.table, self.live = "hives", {}   # table that Enter acts on; open live windows by (project, agent)
        self.autoscroll, self.cleared_id, self.error = True, 0, ""   # cleared_id: log lines up to this event id are hidden
        self.log_filter, self.log_big, self.show_noise = None, False, False   # log: ("hive", apiary, name) or ("bee", name) or None; enlarged; routine round lines shown
        self.scroll, self.rooms, self.bars, self.areas, self.drag = {}, {}, {}, [], None   # scroll offsets, visible rows, drawn scroll bars, wheel areas, dragged bar
        self.canvas = tk.Canvas(root, bg=COL["bg"], highlightthickness=0)
        self.canvas.pack(fill="both", expand=True)
        self.canvas.bind("<Configure>", self.on_resize)
        self.canvas.bind("<Button-1>", self.on_click)
        self.canvas.bind("<Double-Button-1>", self.on_double)
        self.canvas.bind("<Button-3>", self.on_context)
        self.canvas.bind("<B1-Motion>", self.on_motion)
        self.canvas.bind("<Motion>", self.hover)
        self.canvas.bind("<Leave>", self.hover_leave)
        self.tile_boxes, self.overlay_key = [], None   # the six tiles (key, design rectangle) and the tile whose overlay is open
        self.canvas.bind("<ButtonRelease-1>", self.on_release)
        self.canvas.bind("<MouseWheel>", self.on_wheel)
        root.bind("<Key>", self.on_key)
        self.canvas.bind("<Destroy>", self.on_destroy)
        self.tick_job = None
        self.update_state, self.update_thread = {}, None   # H-42: the last update check and a running check (thread)
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
            self.error = tr("error: %s", exc)
        self.watch_update()
        self.draw()

    # The update function of the header (H-42).
    def watch_update(self, manual=False):
        """Read the last update check, start the daily check (or with `manual` Check now) in a thread, and restart the window after a finished install."""
        import threading
        from . import updates
        try:
            if updates.settle_install(db.last_self_update(self.conn)) == "ok":
                self.restart_window()
                return
            self.update_state = updates.load_state()
        except Exception:   # the update line must never break the dashboard
            self.update_state = {}
        if self.update_thread is not None and self.update_thread.is_alive():
            return
        machine = config.load_config()
        if manual or (not self.update_state.get("installing") and updates.due(machine, self.update_state)):
            self.update_thread = threading.Thread(target=(lambda: updates.check(machine, manual=True)) if manual else (lambda: updates.scheduled_check(machine)), daemon=True)
            self.update_thread.start()

    # Check for a new version now.
    def check_update(self):
        """The button Check now: ask GitHub at once; the header shows the result at the next refresh."""
        self.watch_update(manual=True)
        self.draw()

    # Install the offered version after a confirmation.
    def install_update(self):
        """The update button: after the confirmation the detached worker waits for running agents, installs the release and restarts HaiveQueen."""
        from . import updates
        found = updates.offered(self.update_state)
        if not found or not self.confirm(tr(updates.TEXT_CONFIRM, found["version"])):
            return
        try:
            updates.request_install(found["version"])
        except Exception as exc:
            self.inform(str(exc))
        self.refresh()

    # Start the window again with the installed code.
    def restart_window(self):
        """Close this window and open it again in a new process (after an install the new code shows the new version)."""
        try:
            if lock_file().read_text(encoding="ascii").strip() == str(os.getpid()):
                lock_file().unlink()
        except OSError:
            pass
        launch_detached()
        self.root.after(100, self.root.destroy)

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
        widths = [(self.tw(label, 15), self.tw("[%s]" % key, 15)) for key, label in keys]   # measured with the real font, so the room of the subtitle is not underestimated
        hints = sum(lw + 5 + kw + 10 for lw, kw in widths)   # width the right-aligned hints take
        if sub:
            sx = x0 + 52 + self.tw(title, size) + 14
            room = (x1 - 16 - hints) - sx - 12
            if room > 60:
                options = [sub] if isinstance(sub, str) else list(sub)   # several wordings, the longest first: the first that fits whole is drawn, else the last one is shortened
                text = next((o for o in options if self.tw("| " + o, 15) <= room), options[-1])
                self.text(sx, y0 + 26, self.fit("| " + text, room, 15), 15, "dim", "w")
        x = x1 - 16
        for (key, label), (lw, kw) in zip(reversed(keys), reversed(widths)):
            self.text(x, y0 + 26, label, 15, "dim", "e")
            x -= lw + 5
            self.text(x, y0 + 26, "[%s]" % key, 15, "cyan", "e")
            x -= kw + 10

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
        """`s` cut with an ellipsis so that it fits `width` design pixels at font size `size` (measured with the real font)."""
        s = str(s)
        if self.tw(s, size) <= width:
            return s
        while s and self.tw(s + "…", size) > width:
            s = s[:-1]
        return s.rstrip() + "…" if s else "…"

    # Width of a text in design pixels.
    def tw(self, s, size):
        """Design width of `s` drawn at font size `size`, measured with the dashboard font (an estimate before the font exists)."""
        px = -max(8, round(self.px(size)))
        if self.fonts.get(px) is None:
            try:
                from tkinter import font as tkfont
                self.fonts[px] = tkfont.Font(root=self.root, family=self.mono, size=px)
            except Exception:   # no display: estimate
                return 0.6 * size * len(str(s))
        return self.fonts[px].measure(str(s)) / max(self.scale, 0.01)

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
            self.text(20, 20, self.error or tr("loading…"), 18, "dim")
            return
        s = self.snap
        self.header(s)
        self.tiles(s)
        self.hive_table(s, 10, 267, 1035, 507)
        self.bee_table(s, 10, 515, 1035, 700)
        self.activity(s, 10, 710, 505, 850)
        self.system(s, 515, 710, 837, 850)
        self.queue(s, 846, 710, 1035, 850)
        if self.log_big:
            self.log_panel(s, 1045, 165, 1662, 850)
        else:
            self.log_panel(s, 1045, 165, 1662, 385)
            self.limits_panel(s, 1045, 395, 1662, 600)
            self.status_panel(s, 1045, 610, 1662, 850)
        self.footer()
        self.draw_overlay()

    # Draw the title block: the picture of the mockup plus the version line.
    def header(self, s):
        """Draw the header picture (logo, wordmark, honeycomb, slogan) and the version line over it."""
        self.pic("header", 0, 0)
        if i18n.is_german():   # the wording inside the header picture is English: cover it with the German wording
            at = self.fraction[0] / self.fraction[1] / self.scale   # the picture scales by a coarser fraction than the layout: the covers follow the picture
            self.rect(335 * at, 96 * at, 830 * at, 128 * at, fill="#02060a")
            self.text(342 * at, 112 * at, tr("AI AGENT ORCHESTRATOR"), 19, "text", "w")
            self.rect(1445 * at, 24 * at, 1650 * at, 134 * at, fill="#02060a")
            for n, part in enumerate(tr("A SWARM\nOF AGENTS.\nONE QUEEN.").split("\n")):
                self.text(1455 * at, (44 + n * 28) * at, part, 18, "gold", "w")
        first = self.text(342, 143, "v%s  |  bzz  |  " % s["version"], 17, "cyan", "w")
        left = self.canvas.bbox(first)[2] / self.scale   # the link starts where the first part ends (design units)
        link = self.text(left, 143, "Rokk001/HaiveQueen", 17, "cyan", "w", underline=True)
        self.hit(left - 4, 130, self.canvas.bbox(link)[2] / self.scale + 4, 156, lambda _a: self.open_repo())
        from . import updates
        text, level, action = updates.header_line(self.update_state, self.update_thread is not None and self.update_thread.is_alive())
        x = self.canvas.bbox(link)[2] / self.scale + 10
        bar = self.text(x, 143, "|", 17, "cyan", "w")
        x = self.canvas.bbox(bar)[2] / self.scale + 10
        text = self.fit(text, max(0, DESIGN_W - x - 12), 17)
        item = self.text(x, 143, text, 17, {"warn": "amber", "bad": "red"}.get(level, "dim"), "w", underline=action is not None)
        if action:
            self.hit(x - 4, 130, self.canvas.bbox(item)[2] / self.scale + 4, 156, lambda _a: self.install_update() if action == "install" else self.check_update())

    # Draw the six tiles.
    def tiles(self, s):
        """Draw the Apiaries, Hives, Queens, Busy Bees, Total I/O and Start Context tiles and remember where they are for the overlays."""
        t = s["tiles"]
        io = "%s / %s" % (gui_data.fmt_tokens(t["tokens_in"]), gui_data.fmt_tokens(t["tokens_out"]))
        items = ((tr("APIARIES"), str(t["apiaries"]), tr("active"), 10, 145, "t_apiaries", "apiaries"), (tr("HIVES"), str(t["hives"]), tr("active"), 153, 283, "t_hives", "hives"),
                 (tr("QUEENS"), str(t["queens"]), tr("online"), 291, 426, "t_queens", "queens"), (tr("BUSY BEES"), "%d / %d" % (t["bees"], t["bee_capacity"]), tr("active"), 434, 599, "t_bees", "bees"),
                 (tr("TOTAL I/O"), io, tr("in / out (today)"), 607, 822, "t_io", "io"))
        self.tile_boxes = []
        for title, value, sub, x0, x1, icon, key in items:
            self.rect(x0, 165, x1, 255, fill="panel", outline="edge", width=2)
            self.pic(icon, x0 + 6, 172)
            self.text(x0 + 74, 183, title, min(16, (x1 - x0 - 80) / (0.6 * len(title))), "text", "nw")
            self.text(x0 + 74, 208, value, max(12, min(28, (x1 - x0 - 80) / (0.62 * len(value)))), "cyan", "nw")
            self.text(x0 + 74, 236, sub, min(15, (x1 - x0 - 80) / (0.6 * len(sub))), "dim", "nw")
            self.tile_boxes.append((key, x0, 165, x1, 255))
        sc = t["start_ctx"]   # the Start Context tile: one value, the average start context of all of today's starts; the split is in the overlay
        x0, x1 = 830, 1035
        self.rect(x0, 165, x1, 255, fill="panel", outline="edge", width=2)
        self.pic("t_start", x0 + 6, 172)
        self.text(x0 + 74, 183, tr("START CONTEXT"), 16, "text", "nw")
        value = gui_data.fmt_tokens(sc["avg"]) if sc["avg"] is not None else "–"
        self.text(x0 + 74, 208, value, max(12, min(28, (x1 - x0 - 80) / (0.62 * len(value)))), "cyan", "nw")
        sub = tr("average today")
        self.text(x0 + 74, 236, sub, min(15, (x1 - x0 - 80) / (0.6 * len(sub))), "dim", "nw")
        self.tile_boxes.append(("start", x0, 165, x1, 255))

    # ---- overlays ----

    # The mouse moved over the canvas.
    def hover(self, event):
        """Open the overlay of the tile under the pointer (the state now, no period switch) and close it when the pointer leaves the tiles."""
        key = next((k for k, x0, y0, x1, y1 in self.tile_boxes if self.px(x0) <= event.x <= self.px(x1) and self.px(y0) <= event.y <= self.px(y1)), None)
        if key != self.overlay_key:
            self.overlay_key = key
            self.canvas.delete("overlay")
            self.draw_overlay()

    # The pointer left the window.
    def hover_leave(self, _event):
        """Close the open overlay."""
        if self.overlay_key:
            self.overlay_key = None
            self.canvas.delete("overlay")

    # Draw the overlay of the hovered tile under it.
    def draw_overlay(self):
        """Draw the lines of the open overlay in a panel under its tile, on top of everything else; nothing without a snapshot or an open overlay."""
        if not self.overlay_key or self.snap is None:
            return
        from . import gui_overlays
        rows = gui_overlays.lines(self.snap, self.overlay_key)
        limit = 34
        if len(rows) > limit:
            rows = rows[:limit - 1] + [(tr("… %d more lines", len(rows) - limit + 1), "dim")]
        size, step = 14, 19
        width = max(self.tw(text, size) for text, _col in rows) + 28
        box = next(b for b in self.tile_boxes if b[0] == self.overlay_key)
        x0 = max(10, min(box[1], 1660 - width))
        y0 = box[4] + 6
        before = set(self.canvas.find_all())
        self.rect(x0, y0, x0 + width, y0 + 12 + step * len(rows), fill="panel", outline="cyan", width=2)
        for i, (text, col) in enumerate(rows):
            self.text(x0 + 14, y0 + 8 + i * step, text, size, col, "nw")
        for item in set(self.canvas.find_all()) - before:
            self.canvas.addtag_withtag("overlay", item)

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
        """Draw the Hive table: Queen name, Bees, progress (finished packages), status and runtime; details of the selected row below. All other Queen data is in the Bees table."""
        rows = s["hives"]
        sub = tr("%d hives across %d apiaries", len(rows), s["tiles"]["apiaries"])
        short = tr("%d hives, %d apiaries", len(rows), s["tiles"]["apiaries"])
        state = s["run_state"]
        prefix = "" if state == "RUNNING" else tr(state) + "  "
        wordings = ([prefix + sub + "  (%s)" % s["project"]] if s["project"] else []) + [prefix + sub, prefix + short]   # the project name goes first, then the words, when the room is short
        self.panel(x0, y0, x1, y1, tr("ACTIVE HIVES"), wordings, ((tr("Enter"), tr("live")), ("P", tr("pause/resume")), (tr("Space"), tr("plan")), ("F", tr("apiary")), (tr("Tab"), tr("table"))))
        for name, x in (("#", 24), (tr("HIVE"), 90), (tr("APIARY"), 218), (tr("QUEEN"), 312), (tr("BEES"), 386), (tr("PROGRESS"), 436), (tr("STATUS"), 576)):
            self.text(x0 + x, y0 + 56, name, 13, "dim", "w")
        self.text(x1 - 26, y0 + 56, tr("RUNTIME"), 13, "dim", "e")
        self.pick_clamp("hives", len(rows))
        room = 6
        off = self.offset("hives", len(rows), room)
        self.area("hives", x0, y0, x1, y1)
        self.scrollbar("hives", x1 - 16, y0 + 70, y0 + 82 + room * 25 - 12, len(rows), room)
        for n, r in enumerate(rows[off:off + room]):
            i, y = off + n, y0 + 82 + n * 25
            chosen = i == self.sel["hives"] and self.table == "hives"
            if chosen:
                self.rect(x0 + 8, y - 12, x1 - 22, y + 12, fill="select", outline="selectedge")
            self.hit(x0 + 8, y - 12, x1 - 22, y + 12, self.select, ("hives", i))
            colour = STATUS_COL[r["kind"]]
            self.text(x0 + 24, y, str(i + 1), 15, "dim", "w")
            self.pic("row_icon" if chosen else "row_icon_dim", x0 + 58, y, "w")
            self.text(x0 + 90, y, self.fit(r["name"], 120, 14), 14, "gold" if chosen else "text", "w")
            self.text(x0 + 218, y, self.fit(r["apiary"], 86, 14), 14, "cyan", "w")
            self.text(x0 + 312, y, self.fit(r["queen"], 68, 14), 14, "text", "w")
            self.text(x0 + 386, y, "%d/%d" % (r["bees_running"], r["bees_total"]), 14, "text", "w")
            self.bar(x0 + 436, y - 7, 70, 14, r["progress"] / 100, "seg", 7)
            self.text(x0 + 513, y, "%d/%d" % (r["done"], r["total"]), 13, "text", "w")
            self.dot(x0 + 582, y, colour)
            self.text(x0 + 594, y, self.fit(r["status"], 300 if r["runtime"] else 380, 12), 12, colour, "w")
            self.text(x1 - 26, y, r["runtime"], 14, "text", "e")
        if rows:
            r = rows[self.sel["hives"]]
            line = tr("%s | %s | packages %d done, %d implemented%s%s of %d | hive total in/out %s/%s | %s",
                      r["name"], r["apiary"], r["done"], r["implemented"], tr(", %d rejected", r["rejected"]) if r["rejected"] else "",
                      tr(", %d waiting (%s)", r["waiting"], r["waiting_reasons"]) if r["waiting"] else "", r["total"],
                      gui_data.fmt_tokens(r["hive_tin"]), gui_data.fmt_tokens(r["hive_tout"]), r["status"])
            self.text(x0 + 24, y1 - 14, self.fit(line, x1 - x0 - 48, 12), 12, "dim", "w")
        else:
            self.text(x0 + 24, y0 + 90, tr("no active hives"), 15, "dim", "w")

    # Draw the table of busy Bees.
    def bee_table(self, s, x0, y0, x1, y1):
        """Draw the running Queens, each with its Bees (running processes and packages with a Bee assigned) directly below it, with model, Hive, task, context, tokens and runtime; the selected running Bee can be stopped with X."""
        rows = s["bees"]
        workers = [b for b in rows if b["kind"] == "bee"]
        running = sum(1 for b in workers if b["status"] == "running")
        parents = {(b["apiary"], b["plan_path"]) for b in rows if b["kind"] == "queen"}   # Hives whose Bees are drawn below their Queen
        hives = {b["hive"] for b in rows}
        counts = (running, len(workers) - running, len(hives))
        self.panel(x0, y0, x1, y1, tr("QUEENS AND BEES"), tr("%d running, %d waiting across %d hive", *counts) if len(hives) == 1 else tr("%d running, %d waiting across %d hives", *counts), ((tr("Enter"), tr("live")), ("X", tr("stop")), ("M", tr("queen"))))
        for name, x in (("#", 24), (tr("BEE"), 80), (tr("MODEL"), 170), (tr("HIVE"), 300), (tr("TASK"), 410), (tr("CONTEXT"), 580), (tr("TOKENS"), 690), (tr("STATUS"), 790), (tr("RUNTIME"), 920)):
            self.text(x0 + x, y0 + 56, name, 13, "dim", "w")
        self.pick_clamp("bees", len(rows))
        room = 5
        off = self.offset("bees", len(rows), room)
        self.area("bees", x0, y0, x1, y1)
        self.scrollbar("bees", x1 - 16, y0 + 66, y0 + 76 + room * 22 - 8, len(rows), room)
        for n, r in enumerate(rows[off:off + room]):
            i, y = off + n, y0 + 76 + n * 22
            chosen = i == self.sel["bees"] and self.table == "bees"
            if chosen:
                self.rect(x0 + 8, y - 10, x1 - 22, y + 10, fill="select", outline="selectedge")
            self.hit(x0 + 8, y - 10, x1 - 22, y + 10, self.select, ("bees", i))
            colour = "green" if r["status"] == "running" else "amber"
            self.text(x0 + 24, y, str(i + 1), 14, "dim", "w")
            icon = "row_queen" if r["kind"] == "queen" else "row_icon"   # the Queen shows the crown of the Queens tile, a Bee the bee
            self.pic(icon if chosen else icon + "_dim", x0 + 50, y, "w")
            # Every cell is shortened with an ellipsis inside its own column (the width up to the next column minus a gap), so columns never overlap.
            if r["kind"] == "queen":
                self.text(x0 + 80, y, self.fit(r["name"], 84, 14), 14, "gold", "w")
            elif (r["apiary"], r["plan_path"]) in parents:
                self.text(x0 + 80, y, "└", 14, "dim", "w")
                tree = self.tw("└", 14) + 8
                self.text(x0 + 80 + tree, y, self.fit(r["name"], 84 - tree, 14), 14, "gold" if chosen else "text", "w")
            else:
                self.text(x0 + 80, y, self.fit(r["name"], 84, 14), 14, "gold" if chosen else "text", "w")
            self.text(x0 + 170, y, self.fit(r["model"], 122, 14), 14, "text", "w")
            self.text(x0 + 300, y, self.fit(r["hive"], 102, 14), 14, "text", "w")
            if r.get("step"):   # a running Bee: its task name, a progress bar and the finished steps n/m
                self.text(x0 + 410, y, self.fit(r["task"], 72, 14), 14, "text", "w")
                self.bar(x0 + 486, y - 5, 36, 10, r["step_frac"], "seg", 5)
                self.text(x0 + 526, y, self.fit(r["step"], 46, 12), 12, "cyan", "w")
            else:
                self.text(x0 + 410, y, self.fit(r["task"], 160, 14), 14, "gold" if r["kind"] == "queen" else "text", "w")
            self.context_bar(x0 + 580, y, r["ctx"], r["ctx_limit"])
            self.text(x0 + 690, y, self.fit("%s/%s" % (gui_data.fmt_tokens(r["tin"]), gui_data.fmt_tokens(r["tout"])), 92, 13), 13, "cyan", "w")
            self.dot(x0 + 797, y, colour)
            self.text(x0 + 809, y, self.fit(tr(r["status"]), 100, 14), 14, colour, "w")
            self.text(x0 + 920, y, self.fit(r["runtime"], 90, 14), 14, "text", "w")
        if not rows:
            self.text(x0 + 24, y0 + 90, tr("no queens or bees"), 15, "dim", "w")

    # Draw the token use per Apiary and Hive.
    def activity(self, s, x0, y0, x1, y1):
        """Draw the token use (in/out) of the last seven days per Apiary (cyan names) and per Hive, with the share of each as a bar."""
        self.panel(x0, y0, x1, y1, tr("HIVE ACTIVITY"), tr("tokens in/out, last 7 days"))
        rows = s["activity"]
        room = 4
        off = self.offset("activity", len(rows), room)
        self.area("activity", x0, y0, x1, y1)
        self.scrollbar("activity", x1 - 16, y0 + 50, y1 - 8, len(rows), room)
        for n, r in enumerate(rows[off:off + room]):
            y = y0 + 62 + n * 21
            label = r["name"] if r["kind"] == "apiary" else "%s/%s" % (r["apiary"], r["name"])   # a Hive row is part of its Apiary, not another share of the total
            self.text(x0 + 14, y, self.fit(label, 132, 14), 14, "cyan" if r["kind"] == "apiary" else "text", "w")
            self.bar(x0 + 150, y - 7, 90, 14, r["pct"] / 100, "seg" if r["pct"] >= 50 else "green", 9)
            self.text(x0 + 246, y, "%d%%" % r["pct"], 14, "cyan", "w")
            self.text(x1 - 30, y, "%s/%s" % (gui_data.fmt_tokens(r["tin"]), gui_data.fmt_tokens(r["tout"])), 14, "text", "e")
        if not rows:
            self.text(x0 + 14, y0 + 66, tr("no usage yet"), 14, "dim", "w")

    # Draw CPU, RAM and disk.
    def system(self, s, x0, y0, x1, y1):
        """Draw the CPU, RAM and disk meters."""
        self.panel(x0, y0, x1, y1, tr("SYSTEM"), icon="icon_system")
        m = s["system"]
        load = lambda pct: "green" if pct < 70 else "yellow" if pct <= 85 else "red"   # the colour follows the load, like the context bars
        rows = (("CPU", m["cpu"], tr("(%d cores)", m["cores"]), load(m["cpu"]), "icon_cpu"), ("RAM", m["ram_pct"], i18n.decimal("%.1f/%.0f GB" % (m["ram_used_gb"], m["ram_total_gb"])), load(m["ram_pct"]), "icon_ram"),
                ("Disk", m["disk_pct"], i18n.decimal("%.1f/%.1f TB" % (m["disk_used_tb"], m["disk_total_tb"])), load(m["disk_pct"]), "icon_disk"))
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
        self.panel(x0, y0, x1, y1, tr("APIARY QUEUE"), icon="icon_queue")
        q = s["queue"]
        self.text(x0 + 14, y0 + 64, tr("questions: %d", q["open"]), 15, "text", "w")
        self.text(x0 + 14, y0 + 88, tr("queued: %d", q["queued"]), 15, "text", "w")
        self.text(x0 + 14, y0 + 112, self.fit(", ".join(q["labels"]), x1 - x0 - 28, 15), 15, "text", "w")

    # Draw the live log.
    def log_panel(self, s, x0, y0, x1, y1):
        """Draw the newest events with coloured source tags, a scroll bar and the auto-scroll, clear and save controls."""
        self.rect(x0, y0, x1, y1, fill="panel", outline="edge", width=2)
        self.pic("icon_logs", x0 + 12, y0 + 10)
        title = tr("LOGS")
        self.text(x0 + 52, y0 + 26, title, 20, "gold", "w")
        chip_x = max(x0 + 130, x0 + 52 + self.tw(title, 20) + 14)   # the filter chip starts behind the heading, also behind a longer translated one
        self.log_head = (self.px(x0 + 40), self.px(y0 + 8), self.px(chip_x), self.px(y0 + 44))
        flt = self.log_filter
        chip = tr("all") if flt is None else (flt[2] if flt[0] == "hive" else flt[1]) + "  ✕"
        self.text(chip_x, y0 + 26, "| " + chip, 15, "cyan" if flt else "dim", "w")
        if flt:
            self.hit(chip_x, y0 + 12, chip_x + 9 * (len(chip) + 2), y0 + 40, lambda _a: self.clear_log_filter())
        x = x1 - 16
        for key, label, action in (("S", tr("save"), self.save_log), ("C", tr("clear"), self.clear_log)):
            self.text(x, y0 + 26, label, 15, "dim", "e")
            self.text(x - 9 * len(label) - 8, y0 + 26, "[%s]" % key, 15, "cyan", "e")
            self.hit(x - 9 * len(label) - 40, y0 + 12, x, y0 + 40, lambda _a, f=action: f())
            x -= 9 * len(label) + 62
        self.text(x, y0 + 26, "[%s]" % (tr("On") if self.autoscroll else tr("Off")), 15, "cyan", "e")
        self.hit(x - 40, y0 + 12, x, y0 + 40, lambda _a: self.toggle_autoscroll())
        self.text(x - 46, y0 + 26, tr("Auto-scroll"), 15, "dim", "e")
        lines = self.visible_log()
        stamp_w = max([54] + [self.tw(line[0], 14) + 8 for line in lines])   # the system time format decides the width of the time column (for example with AM/PM)
        if self.log_big:   # in the big log long messages continue on the next line instead of being cut off
            chars = int((x1 - x0 - 142 - stamp_w) / 8.4)
            wrapped = []
            for stamp, tag, msg in lines:
                parts = textwrap.wrap(msg, chars) or [""]
                wrapped += [(stamp, tag, parts[0])] + [("", "", part) for part in parts[1:]]
            lines = wrapped
        room = int((y1 - y0 - 60) // 22)
        if self.autoscroll:
            self.scroll["log"] = max(0, len(lines) - room)
        off = self.offset("log", len(lines), room)
        self.area("log", x0, y0, x1, y1)
        self.scrollbar("log", x1 - 16, y0 + 50, y1 - 8, len(lines), room)
        for n, (stamp, tag, msg) in enumerate(lines[off:off + room]):
            y = y0 + 62 + n * 22
            kind, _, name = tag.partition(" ")
            self.text(x0 + 12, y, stamp, 14, "text", "w")
            self.text(x0 + 12 + stamp_w, y, self.fit(name or tr(kind), 96, 14), 14, TAG_COL.get(kind, "text"), "w")   # only the name: the colour tells Queen, Bee, Apiary
            self.text(x0 + 112 + stamp_w, y, self.fit(msg, x1 - x0 - 142 - stamp_w, 14), 14, "text", "w")
        if not lines:
            self.text(x0 + 14, y0 + 62, tr("no log lines"), 13, "dim", "w")

    # Draw the model limits as bars.
    def limits_panel(self, s, x0, y0, x1, y1):
        """Draw the Claude/Codex 5-hour windows and per provider the weekly quota and the used slots as bars with a note beside each; scrolls when there are more."""
        self.panel(x0, y0, x1, y1, tr("MODEL LIMITS"), tr("used share, resets and slots"), icon="icon_system")
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
            self.text(x0 + 298, y, "%d%%" % r["pct"] if r["pct"] is not None else tr("n/a"), 14, "cyan" if r["pct"] is not None else "dim", "w")
            self.text(x0 + 345, y, self.fit(r["note"], x1 - x0 - 345 - 30, 12), 12, "dim", "w")
        if not rows:
            self.text(x0 + 14, y0 + 66, tr("no limit data"), 14, "dim", "w")

    # Draw the status sections.
    def status_panel(self, s, x0, y0, x1, y1):
        """Draw run state, usage, system, plans, questions, round errors and self-update as text lines, with a scroll bar."""
        chars = int((x1 - x0 - 50) / (0.6 * 12))
        flat = []   # (is heading, text, level) after wrapping, so the panel can scroll by line
        for title, lines in s["info"]:
            flat.append((True, title, "gold"))
            for text, level in lines:
                width = x1 - x0 - 48
                limit = chars
                parts = textwrap.wrap(text, limit, subsequent_indent="  ") or [""]
                while limit > 3 and any(self.tw(part, 12) > width for part in parts):
                    limit -= 1
                    parts = textwrap.wrap(text, limit, subsequent_indent="  ") or [""]
                flat += [(False, part, level) for part in parts]
        room = int((y1 - y0 - 62) // 15)
        off = self.offset("status", len(flat), room)
        self.panel(x0, y0, x1, y1, tr("STATUS"), tr("state, usage, plans and questions"))
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
        label = self.text(188, 886, tr(state["label"]), 17, colour, "w", True)
        right = self.canvas.bbox(label)[2] / self.scale
        self.text(right + 8, 886, "▾", 17, "dim", "w")
        self.hit(160, 866, right + 34, 908, lambda _a: self.state_menu())
        x = right + 50
        self.text(x - 22, 886, "|", 17, "dim", "w")
        for name, action in ((tr("apiaries") if not self.project else tr("apiary:%s", self.project), self.next_project),
                             (tr("hives"), self.open_hives), (tr("questions"), self.open_questions), (tr("config"), self.open_config), (tr("help"), self.help)):
            w = 13 * len(name)
            self.text(x, 886, name, 17, "text", "w")
            self.hit(x - 8, 866, x + w + 8, 908, lambda _a, f=action: f())
            x += w + 14
            self.text(x, 886, "|", 17, "dim", "w")
            x += 22
        self.canvas.create_line(self.px(x + 10), self.px(886), self.px(DESIGN_W - 440), self.px(886), fill=COL["edge"], width=2)
        self.pic("foot_hex", DESIGN_W - 412, 866)
        self.text(DESIGN_W - 22, 886, self.error or tr("BUILD  •  ORCHESTRATE  •  EVOLVE"), 17, "red" if self.error else "gold", "e")

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
        self.set_log_filter()
        self.draw()

    # Read the selected row of a table.
    def selected(self, table=None):
        """The selected row of `table` (default: the table Enter acts on), or None when it has no rows."""
        table = table or self.table
        rows = (self.snap or {}).get(table) or []
        return rows[min(self.sel[table], len(rows) - 1)] if rows else None

    # Find the Hive of the selection.
    def selected_hive(self):
        """The Hive row of the selection: the selected Hive, or the Hive of the selected Queen row; None for a Bee row or when nothing is selected."""
        row = self.selected()
        if row is None or self.table == "hives":
            return row
        if row["kind"] != "queen":
            return None
        return next((h for h in self.snap["hives"] if (h["apiary"], h["plan_path"]) == (row["apiary"], row["plan_path"])), None)

    # Switch between the two tables.
    def switch_table(self):
        """Make the other table (Hives, Queens and Bees) the one that the arrow keys, Enter, M and Space act on."""
        self.table = "bees" if self.table == "hives" else "hives"
        self.draw()

    # The log lines that were not cleared.
    def visible_log(self) -> list:
        """The log lines newer than the last clear (by event id, so lines pushed out of the window cannot make new ones disappear)."""
        s = self.snap or {}
        meta = s.get("log_meta") or [{}] * len(s.get("log", []))
        flt = self.log_filter

        def wanted(line, event, info):
            """Newer than the last clear, not routine noise (unless shown) and inside the filter."""
            if event <= self.cleared_id or (info.get("noise") and not self.show_noise):
                return False
            if flt is None:
                return True
            if flt[0] == "hive":
                return (info.get("project"), info.get("hive")) == (flt[1], flt[2])
            kind, _, name = line[1].partition(" ")
            return kind in ("QUEEN", "BEE") and name == flt[1]
        return [line for line, event, info in zip(s.get("log", []), s.get("log_ids", []), meta) if wanted(line, event, info)]

    # Set the log filter from the selected row.
    def set_log_filter(self):
        """The selected Hive or Queen row filters the log to its Hive, a selected Bee row to that Bee (a waiting Bee without a name to its Hive)."""
        row = self.selected()
        if row is None:
            return
        if self.table == "hives":
            self.log_filter = ("hive", row["apiary"], row["name"])
        elif row["kind"] == "bee" and row["name"] != "–":
            self.log_filter = ("bee", row["name"])
        else:
            self.log_filter = ("hive", row["apiary"], row["hive"])

    # Show every line again.
    def clear_log_filter(self):
        """Remove the log filter (key Escape or a click on the filter chip)."""
        self.log_filter = None
        self.draw()

    # Make the log big or small.
    def toggle_log_big(self):
        """Switch between the log panel and the log over the whole right column (key L or a double click on the log heading)."""
        self.log_big = not self.log_big
        self.draw()

    # Show or hide the routine round lines.
    def toggle_noise(self):
        """Show or hide the routine round lines of the log (key N)."""
        self.show_noise = not self.show_noise
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
        """A double click on a Hive or Bee row selects it and opens the live view of its agent; on the log heading it makes the log big or small."""
        hx0, hy0, hx1, hy1 = getattr(self, "log_head", (0, 0, -1, -1))
        if hx0 <= event.x <= hx1 and hy0 <= event.y <= hy1:
            self.toggle_log_big()
            return
        for x0, y0, x1, y1, action, arg in reversed(self.hits):
            if x0 <= event.x <= x1 and y0 <= event.y <= y1 and action == self.select:
                self.select(arg)
                self.open_live()
                return

    # Open the Queen menu of the Hive under the pointer.
    def on_context(self, event):
        """A right click on a Hive row or a Queen row selects it and opens the Queen menu; on a stopped package it offers "Resume package"; on other Bee rows no menu opens."""
        from . import gui_dialog
        for x0, y0, x1, y1, action, arg in reversed(self.hits):
            if x0 <= event.x <= x1 and y0 <= event.y <= y1 and action == self.select:
                self.select(arg)
                row = self.selected(arg[0])
                if arg[0] == "bees" and row is not None and row.get("stopped"):
                    gui_dialog.popup_menu(self.root, [(tr("Resume package"), lambda: self.resume_package(row))], self.mono)
                    return
                self.hive_menu()
                return

    # Release a package the owner stopped.
    def resume_package(self, row):
        """After a confirmation release the stopped package of `row` again: its Queen may start a Bee for it."""
        from . import bees
        if not self.confirm(tr("Resume package %s?", row["task"])):
            return
        try:
            bees.resume_package(self.conn, row["project"], row["plan_path"], row.get("package") or row["task"])
        except Exception as exc:
            self.complain(tr("Could not resume the package: %s", exc))
        self.refresh()

    # Open the live view of the selected row.
    def open_live(self):
        """Open (or focus) the live text window of the selected row of the table Enter acts on: a Hive shows its running Queen, else its latest one; a Bee its own text."""
        row = self.selected()
        if row is None:
            return
        if self.table == "hives":
            project, agent, title = row["apiary"], row.get("queen_agent"), tr("Queen %s | %s", row["queen"], row["name"])
        else:
            project, agent, title = row["project"], row.get("agent"), (tr("Queen %s | %s", row["name"], row["hive"]) if row["kind"] == "queen" else tr("Bee %s | %s", row["name"], row["hive"]))
        if not agent:
            self.inform(tr("This row has no agent process yet, so there is no live output."))
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
        """P pause/resume, Q answer the open questions, Space pause/resume the selected plan, F apiary filter, M queen menu of the selected Hive, X stop the selected Bee, C clear and S save the log, A toggle auto-scroll, Tab switch between the Hives and the Queens-and-Bees table, Up/Down move the selection in that table, Enter opens the live view."""
        key = (event.keysym or "").lower()
        {"p": self.toggle_pause, "f": self.next_project, "m": self.hive_menu, "space": self.toggle_hive_pause, "x": self.stop_bee, "c": self.clear_log, "s": self.save_log,
         "a": self.toggle_autoscroll, "l": self.toggle_log_big, "n": self.toggle_noise, "escape": self.clear_log_filter, "q": self.open_questions, "tab": self.switch_table, "up": lambda: self.move(-1), "down": lambda: self.move(1), "return": self.open_live}.get(key, lambda: None)()

    # Move the selection of the active table up or down.
    def move(self, step):
        """Move the selected row of the active table by `step` rows and scroll the table so that the row stays visible."""
        table = self.table
        rows = len((self.snap or {}).get(table) or [])
        self.sel[table] = max(0, min(self.sel[table] + step, max(0, rows - 1)))
        self.set_log_filter()
        room, off = self.rooms.get(table, 6 if table == "hives" else 5), self.scroll.get(table, 0)
        self.scroll[table] = self.sel[table] if self.sel[table] < off else max(off, self.sel[table] - room + 1)
        self.draw()

    # Ask before doing something that changes the run.
    def confirm(self, text) -> bool:
        """Ask the user a yes/no question in a dialog of the dashboard look."""
        from . import gui_dialog
        return gui_dialog.ask(self.root, "HaiveQueen", text, self.mono)

    # Show a message.
    def inform(self, text, title="HaiveQueen"):
        """Show `text` in an information window of the dashboard look."""
        from . import gui_dialog
        gui_dialog.info(self.root, title, text, self.mono)

    # Show an error message.
    def complain(self, text):
        """Show `text` in an error window of the dashboard look."""
        from . import gui_dialog
        gui_dialog.error(self.root, "HaiveQueen", text, self.mono)

    # Pause the run or resume it.
    def toggle_pause(self):
        """Pause a running run or resume a paused one after a confirmation (key P)."""
        resume = runstate.get_state(self.conn)["state"] != "RUNNING"
        if self.confirm(tr("Resume HaiveQueen?") if resume else tr("Pause HaiveQueen? Running agents keep working, no new agent starts.")):
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
        """Open a menu at the pointer with Restart Queen, End Queen and Bees and Pause/Resume plan for the Hive of the selection (a Hive row or a Queen row; none for a Bee row)."""
        from . import gui_dialog
        hive = self.selected_hive()
        if hive is None:
            return
        items = [(tr("Restart Queen of %s", hive["name"]), lambda: self.queen_action(hive, "requeen"))]
        items += [(tr("Restart Queen of %s on %s", hive["name"], name), lambda name=name: self.queen_action(hive, "requeen", name)) for name in self.queen_providers(hive)]
        items.append((tr("End Queen and Bees of %s", hive["name"]), lambda: self.queen_action(hive, "dequeen")))
        items.append(None)
        gui_dialog.popup_menu(self.root, items + [(label + " " + hive["name"], lambda action=action: self.plan_control(hive, action)) for label, action in self.control_items(hive)], self.mono)

    # The plan control entries a Hive offers now.
    @staticmethod
    def control_items(hive) -> list:
        """(label, action) of the plan control: Pause and Stop for a running plan, Start again and Stop for a paused plan with a live Queen, Start again and Reject for a stopped Hive (paused, no live Queen), Start again for a rejected plan."""
        if hive.get("plan_rejected"):
            return [(tr("Start again"), "start")]
        if not hive["paused"]:
            return [(tr("Pause"), "pause"), (tr("Stop"), "stop")]
        if hive["live"]:
            return [(tr("Start again"), "start"), (tr("Stop"), "stop")]
        return [(tr("Start again"), "start"), (tr("Cancel plan"), "reject")]

    # The providers a replacement Queen of a Hive could run on instead of its own.
    def queen_providers(self, hive) -> list:
        """Names of the configured providers other than the Hive's current one that offer the Queen class (not excluded, no local endpoint); empty when the settings cannot be read."""
        from . import config, hives
        from . import scheduler as sch
        try:
            env = next(e for e in sch.load_projects(self.conn, config.load_config()) if e.name == hive["apiary"])
        except (StopIteration, Exception):
            return []
        queen_class = env.settings.get("queenClass", "medium")
        excluded = env.settings.get("excludedProviders", [])
        current = (hives.get(self.conn, hive["apiary"], hive["plan_path"]) or {}).get("provider")
        return [p["name"] for p in env.providers if p["name"] not in excluded and p["name"] != current and not p.get("endpoint")
                and (p.get("classes", {}).get(queen_class) or {}).get("model")]

    # Pause, stop, reject or start again the plan of a Hive.
    def plan_control(self, hive, action):
        """After a confirmation: pause (no new Queen or Bee starts, a running Queen keeps working), stop (end the Queen and her Bees, plan paused), reject (a stopped Hive) or start again (a paused plan gets its earlier status back, a rejected plan becomes a draft). Nothing is deleted."""
        from . import hives, loop
        ask = {"pause": i18n.N_("Pause %s? Running agents keep working, no new Queen or Bee starts."), "stop": i18n.N_("Stop %s? The Queen and her Bees are ended and the plan is paused. Handoff, packages, branch and worktrees stay."),
               "reject": i18n.N_("Cancel %s? The plan never starts again by itself; starting it again makes it a draft. Nothing is deleted."), "start": i18n.N_("Start %s again?")}[action]
        if not self.confirm(tr(ask, hive["name"])):
            return
        try:
            with loop.hive_schedule_lock():
                key = (hive["apiary"], hive["plan_path"])
                {"pause": hives.pause_plan, "stop": hives.stop_hive, "reject": hives.reject_plan, "start": hives.start_again}[action](self.conn, *key)
        except Exception as exc:
            self.complain(tr("Could not change the plan: %s", exc))
        self.refresh()

    # Pause or resume the plan of a Hive (key Space).
    def toggle_hive_pause(self, hive=None):
        """Key Space on the selected Hive: pause a running plan or start a paused one again, after a confirmation."""
        hive = hive or self.selected_hive()
        if hive is not None and not hive.get("plan_rejected"):
            self.plan_control(hive, "start" if hive["paused"] else "pause")

    # Restart or end the Queen of a Hive through the bzz command.
    def queen_action(self, hive, command, provider=None):
        """After a confirmation run `bzz requeen` (a replacement Queen, on `provider` when given: the Hive then keeps that provider) or `bzz dequeen` (end Queen and own-process Bees) for the Hive in the background and report a failure."""
        ask = {"requeen": i18n.N_("Restart the Queen of %s? A replacement Queen takes over."), "dequeen": i18n.N_("End the Queen and the Bees of %s? The hive stays queenless.")}[command]
        text = tr("Restart the Queen of %s on %s? The Hive moves to %s and keeps it until you change it again.", hive["name"], provider, provider) if provider else tr(ask, hive["name"])
        if not self.confirm(text):
            return
        proc = subprocess.Popen([osplat.python_executable(), "-m", "haivequeen", command, "%s:%s" % (hive["apiary"], hive["plan_path"])] + ([provider] if provider else []), stdin=subprocess.DEVNULL,
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
            self.complain(tr("%s failed: %s", command, (err or out or "").strip()[:300] or tr("exit code %d", proc.returncode)))
        self.refresh()

    # Stop the selected Bee.
    def stop_bee(self):
        """Kill the process tree of the selected Bee after a confirmation; its package stays stopped until the owner resumes it ("Resume package").
        A Bee that runs as a subagent of its Queen has no process: its package is stopped and its Queen told to end it; the stop stays pending and the Bee keeps
        its place until the Queen confirms its end (owner answer 220).

        A Queen row is refused with a hint to the Queen menu, a stopped package is offered for resuming, and a Bee that has not started yet gets a hint that there is nothing to stop."""
        bee = self.selected("bees")
        if bee is None:
            return
        if bee.get("stopped"):
            self.resume_package(bee)
            return
        if bee["kind"] == "queen":
            self.inform(tr("X stops Bees only. Use the Queen menu (M, or a right click on the Queen) to restart or end a Queen."))
            return
        if bee.get("stopping"):   # owner answer 220: the place stays held until the Queen confirms that her subagent ended
            self.inform(tr("The stop of %s (%s) is pending: the Bee keeps its place until its Queen confirms that it has ended.", bee["name"], bee["task"]))
            return
        if bee.get("embedded"):   # a subagent of its Queen has no process of its own: its Queen is told to end it and confirm its end (STOP-PACKAGES)
            if self.confirm(tr("Stop bee %s (%s)? The package stays stopped until you resume it.", bee["name"], bee["task"])):
                from . import bees
                bees.stop_package(self.conn, bee["project"], bee["plan_path"], bee.get("package") or bee["task"])
                self.refresh()
            return
        if not bee["agent"]:
            self.inform(tr("This Bee has not started yet, so there is nothing to stop."))
            return
        found = db.rows(self.conn, "SELECT pid, plan_path, package FROM agents WHERE project=? AND name=? AND archived=0", (bee["project"], bee["agent"]))
        if found and found[0]["pid"] and self.confirm(tr("Stop bee %s (%s)? The package stays stopped until you resume it.", bee["name"], bee["task"])):
            from . import bees
            if found[0]["package"]:
                bees.stop_package(self.conn, bee["project"], found[0]["plan_path"] or bee["plan_path"], found[0]["package"])
            osplat.kill_process_tree(found[0]["pid"])
            self.refresh()

    # Switch the log between following and top-aligned.
    def toggle_autoscroll(self):
        """Toggle the log auto-scroll."""
        self.autoscroll = not self.autoscroll
        self.draw()

    # Hide the lines shown so far.
    def clear_log(self):
        """Hide every log line that exists now; later lines still appear (the cut is by event id)."""
        ids = (self.snap or {}).get("log_ids") or []
        if ids:
            self.cleared_id = ids[-1]
        self.draw()

    # Write the log lines to a text file.
    def save_log(self):
        """Ask for a file name and write the visible log lines to it."""
        from . import gui_dialog
        folder = osplat.data_dir() / "exports"
        path = gui_dialog.ask_path(self.root, tr("Save the log as"), str(folder / ("haivequeen-%s.log" % datetime.now().strftime("%Y%m%d-%H%M"))), self.mono)
        if path and self.snap:
            Path(path).parent.mkdir(parents=True, exist_ok=True)
            Path(path).write_text("\n".join("%s [%s] %s" % ln for ln in self.visible_log()) + "\n", encoding="utf-8")

    # Open the state button's menu.
    def state_menu(self):
        """Open a menu at the pointer with the actions that fit the current state (see state_actions)."""
        from . import gui_dialog
        gui_dialog.popup_menu(self.root, [None if item is None else (item[0], getattr(self, item[1]) if item[1] else None)
                                          for item in state_actions(self.snap["orchestrator"]["label"])], self.mono)

    # Start the orchestrator loop.
    def start(self):
        """Run `bzz start` in the background (the orchestrator starts without a terminal window, this window stays the dashboard)."""
        subprocess.Popen([osplat.python_executable(), "-m", "haivequeen", "start"], stdin=subprocess.DEVNULL,
                         stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, **NO_WINDOW)

    # Request a graceful stop.
    def stop(self):
        """Run `bzz stop` after a confirmation: running agents finish, no new one starts."""
        if self.confirm(tr("Stop HaiveQueen gracefully? Running agents finish their task first.")):
            subprocess.Popen([osplat.python_executable(), "-m", "haivequeen", "stop"], stdin=subprocess.DEVNULL,
                             stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, **NO_WINDOW)

    # Open the window that answers the open questions.
    def open_questions(self):
        """Open the questions window (answer the open owner questions, mark rejected steps); a second click brings the open window to the front."""
        from . import gui_questions
        current = getattr(self, "questions_window", None)
        if current is not None and current.win.winfo_exists():
            current.win.lift()
            return
        self.questions_window = gui_questions.QuestionsWindow(self.root, self.conn, self.mono, on_answered=self.refresh)

    # Open the list of all plans.
    def open_hives(self):
        """Open the list of the plans of all Apiaries with their status and the right-click actions; a second click brings the open window to the front."""
        from . import gui_hives
        current = getattr(self, "hives_window", None)
        if current is not None and current.win.winfo_exists():
            current.win.lift()
            return
        self.hives_window = gui_hives.HivesWindow(self.root, self.conn, self.mono, on_changed=self.refresh)

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
        self.inform(tr("P  pause / resume\nQ  answer the open questions\nSpace  pause / resume the selected plan\nF  next apiary filter\nM / right click  queen menu of the hive or the Queen row\nTab  switch between the Hives and the Queens and Bees table\nUp/Down  select a row of that table\nClick  select a row\nEnter / double click  live output of the row\n"
                       "X  stop the selected bee\nC  clear log   S  save log   A  auto-scroll\nL / double click on LOGS  big log   N  routine round lines   Esc  remove the log filter"), tr("Keys"))


# The actions the state button offers in a given state.
def state_actions(label) -> list:
    """[(menu text, Dashboard method name or None)] for the state shown on the button; a None entry is a separator, a None method a disabled line."""
    if label == "STOPPED":
        return [(tr("Start HaiveQueen"), "start")]
    if label == "STOPPING":
        return [(tr("Stopping: running agents finish their task first"), None)]
    return [(tr("Resume new starts") if label == "PAUSED" else tr("Pause new starts"), "toggle_pause"), None, (tr("Stop HaiveQueen"), "stop")]


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
        print(tr("The graphical dashboard is already open."), file=sys.stderr)
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
    root.title(tr("HaiveQueen | AI Agent Orchestrator"))
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

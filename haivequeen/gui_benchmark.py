"""The window 'Benchmark': progress of the start-context measurement of a provider and the table of the stored results per Project.

Opened by the button 'Benchmark Start Context' of the provider page in the settings (gui_config). The measurement itself is startctx.benchmark.
"""
import threading
from datetime import datetime

from . import db, startctx
from .gui import COL
from .i18n import tr

POLL_MS = 300


# The rows of the table.
def table_rows(conn, provider) -> list:
    """One tuple per Project with a complete stored measurement of `provider`: (Project, HaiveQueen profile, own profile, saving as '-68 %', date)."""
    out = []
    for r in sorted(startctx.pairs(conn), key=lambda r: r["project"]):
        if r["provider"] == provider:
            out.append((r["project"], startctx._tok(r["hq"]), startctx._tok(r["own"]), "%+d %%" % round(r["saving"]), r["measured_at"][:10]))
    return out


# The window.
class BenchmarkWindow:
    """A dark window with the title, the progress line, an error text (red) and the table of the stored results of one provider."""

    def __init__(self, root, conn, provider, mono="Consolas", cfg=None):
        """Open the window for `provider`; the table shows the stored results at once."""
        import tkinter as tk
        from tkinter import ttk
        from . import gui_dialog
        self.tk, self.conn, self.provider, self.running = tk, conn, provider, False
        self.cfg, self.mono = cfg or {}, mono
        self.table_font, self.heading_font = (mono, 10), (mono, 10, "bold")
        self.win = tk.Toplevel(root)
        self.win.title("Benchmark")
        self.win.geometry("760x420")
        self.win.minsize(520, 300)
        self.win.configure(bg=COL["edge"])
        inner = tk.Frame(self.win, bg=COL["panel"])
        inner.pack(fill="both", expand=True, padx=2, pady=2)
        tk.Label(inner, text="Benchmark Start Context - %s" % provider, bg=COL["panel"], fg=COL["gold"], font=(mono, 14, "bold"), anchor="w").pack(fill="x", padx=14, pady=(12, 4))
        self.progress = tk.Label(inner, text="", bg=COL["panel"], fg=COL["dim"], font=(mono, 10), anchor="w", justify="left", wraplength=700)
        self.progress.pack(fill="x", padx=14)
        self.error = tk.Label(inner, text="", bg=COL["panel"], fg=COL["red"], font=(mono, 10), anchor="w", justify="left", wraplength=700)
        self.error.pack(fill="x", padx=14, pady=(2, 6))
        for label in (self.progress, self.error):
            label.bind("<Configure>", lambda e, w=label: w.config(wraplength=max(200, e.width - 4)))
        buttons = tk.Frame(inner, bg=COL["panel"])
        buttons.pack(side="bottom", fill="x", padx=14, pady=10)
        tk.Button(buttons, text="Close", width=12, font=(mono, 10), command=self.win.destroy).pack(side="right")
        self.start_button = tk.Button(buttons, text="Start benchmark", font=(mono, 10), command=self.start)
        self.start_button.pack(side="right", padx=(0, 8))
        columns = ("apiary", "hq", "own", "saving", "date")
        table_frame = tk.Frame(inner, bg=COL["panel"])
        table_frame.pack(fill="both", expand=True, padx=14)
        self.table = ttk.Treeview(table_frame, columns=columns, show="headings", selectmode="browse")
        for column, title, width in (("apiary", tr("Project"), 160), ("hq", "HaiveQueen profile", 150), ("own", "Own profile", 120), ("saving", "Saving", 90), ("date", "Date", 110)):
            self.table.heading(column, text=title)
            self.table.column(column, width=width, minwidth=width, stretch=False, anchor="w")
        bar = gui_dialog.scrollbar(table_frame, self.table.yview)
        horizontal = gui_dialog.scrollbar(table_frame, self.table.xview, orient="horizontal")
        self.table.configure(yscrollcommand=bar.set, xscrollcommand=horizontal.set)
        bar.pack(side="right", fill="y")
        horizontal.pack(side="bottom", fill="x")
        self.table.pack(fill="both", expand=True)
        self.refresh()

    # Fill the table from the stored results.
    def refresh(self):
        """Reload the table: the complete stored measurements of the provider (earlier ones and the new ones)."""
        self.table.delete(*self.table.get_children())
        from tkinter import font
        rows = table_rows(self.conn, self.provider)
        normal = font.Font(root=self.win, font=self.table_font)
        bold = font.Font(root=self.win, font=self.heading_font)
        for i, column in enumerate(self.table["columns"]):
            width = max([bold.measure(self.table.heading(column, "text"))] + [normal.measure(str(row[i])) for row in rows]) + 24
            self.table.column(column, width=width, minwidth=width, stretch=False)
        for row in rows:
            self.table.insert("", "end", values=row)

    def start(self):
        """Confirm and start a measurement from this window."""
        from . import gui_dialog
        if self.running:
            return
        count = 2 * self.conn.execute("SELECT COUNT(*) FROM projects").fetchone()[0]
        if gui_dialog.ask(self.win, "HaiveQueen", "Measure %s in every Project with both profiles? This starts the CLI %d times and uses login tokens." % (self.provider, count), self.mono):
            self.run(self.cfg)

    # Measure.
    def run(self, cfg, done=None, wait=False):
        """Measure the provider in every registered Project (in a thread, so the window stays usable; `wait` runs it here); the progress, the errors and the
        new results show in the window; `done()` is called at the end."""
        box = {}
        name = self.provider
        if self.running:
            return
        self.running = True
        self.start_button.config(state="disabled")
        self.error.config(text="")
        self.progress.config(text="measuring ...")
        path = next((r[2] for r in self.conn.execute("PRAGMA database_list") if r[1] == "main"), "")

        def work():
            """Measure on an own connection (SQLite connections belong to one thread)."""
            conn = self.conn if wait else db.connect(path)
            try:
                box["result"] = startctx.benchmark(conn, cfg, name, progress=lambda text: box.__setitem__("progress", text))
            except Exception as exc:
                box["result"] = {"excluded": False, "errors": [str(exc)], "results": []}
            finally:
                if conn is not self.conn:
                    conn.close()

        def finish():
            """Show the result and tell the owner of the window."""
            self.running = False
            res = box["result"]
            try:
                self.start_button.config(state="normal")
                if res["excluded"]:
                    self.progress.config(text="")
                    self.error.config(text="Provider %s is excluded (excludedProviders): nothing was measured." % name)
                else:
                    self.progress.config(text="Done: %d of %d measurements, %s" % (res.get("completed", 0), res.get("total", 0), datetime.now().strftime("%Y-%m-%d %H:%M:%S")))
                    self.error.config(text="\n".join("Error: " + e for e in res["errors"]))
                self.refresh()
            except self.tk.TclError:
                pass   # the window was closed meanwhile
            if done:
                done()

        def poll():
            """Wait for the thread without blocking the window."""
            try:
                if not self.win.winfo_exists():
                    self.running = False
                    if done:
                        done()
                    return
                if "result" in box:
                    finish()
                else:
                    if box.get("progress"):
                        self.progress.config(text=box["progress"])   # e.g. "2/4: ODP without it ..."
                    self.win.after(POLL_MS, poll)
            except self.tk.TclError:
                pass
        if wait:
            work()
            finish()
        else:
            threading.Thread(target=work, daemon=True).start()
            self.win.after(POLL_MS, poll)

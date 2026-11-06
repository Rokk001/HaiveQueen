"""The window 'Benchmark': progress of the start-context measurement of a provider and the table of the stored results per Apiary.

Opened by the button 'Benchmark Start Context' of the provider page in the settings (gui_config). The measurement itself is startctx.benchmark.
"""
import threading

from . import db, startctx
from .gui import COL

POLL_MS = 300


# The rows of the table.
def table_rows(conn, provider) -> list:
    """One tuple per Apiary with a complete stored measurement of `provider`: (Apiary, HaiveQueen profile, own profile, saving as '-68 %', date)."""
    out = []
    for r in sorted(startctx.pairs(conn), key=lambda r: r["project"]):
        if r["provider"] == provider:
            out.append((r["project"], startctx._tok(r["hq"]), startctx._tok(r["own"]), "%+d %%" % round(r["saving"]), r["measured_at"][:10]))
    return out


# The window.
class BenchmarkWindow:
    """A dark window with the title, the progress line, an error text (red) and the table of the stored results of one provider."""

    def __init__(self, root, conn, provider, mono="Consolas"):
        """Open the window for `provider`; the table shows the stored results at once."""
        import tkinter as tk
        from tkinter import ttk
        from . import gui_dialog
        self.tk, self.conn, self.provider, self.running = tk, conn, provider, False
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
        columns = ("apiary", "hq", "own", "saving", "date")
        self.table = ttk.Treeview(inner, columns=columns, show="headings", selectmode="browse")
        for column, title, width in (("apiary", "Apiary", 160), ("hq", "HaiveQueen profile", 150), ("own", "Own profile", 120), ("saving", "Saving", 90), ("date", "Date", 110)):
            self.table.heading(column, text=title)
            self.table.column(column, width=width, anchor="w")
        bar = gui_dialog.scrollbar(inner, self.table.yview)
        self.table.configure(yscrollcommand=bar.set)
        bar.pack(side="right", fill="y", padx=(0, 14))
        self.table.pack(side="left", fill="both", expand=True, padx=(14, 0))
        self.refresh()

    # Fill the table from the stored results.
    def refresh(self):
        """Reload the table: the complete stored measurements of the provider (earlier ones and the new ones)."""
        self.table.delete(*self.table.get_children())
        for row in table_rows(self.conn, self.provider):
            self.table.insert("", "end", values=row)

    # Measure.
    def run(self, cfg, done=None, wait=False):
        """Measure the provider in every registered Apiary (in a thread, so the window stays usable; `wait` runs it here); the progress, the errors and the
        new results show in the window; `done()` is called at the end."""
        box = {}
        name = self.provider
        self.running = True
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
                if res["excluded"]:
                    self.progress.config(text="")
                    self.error.config(text="Provider %s is excluded (excludedProviders): nothing was measured." % name)
                else:
                    self.progress.config(text=(box.get("progress", "") + "  - done").strip(" -"))
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

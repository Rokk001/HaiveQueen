"""Window with the list of all plans of all registered Apiaries and their status, with start, pause, reject and back to draft on a right click.

The list also shows plans that never had a Hive. The actions reuse the plan control of hives.py; each one asks first.
"""
import os

from . import config, db, gui_data, hives, loop, runtime
from . import plans as plan_mod

REFRESH_MS = 5000
LABELS = {"start": "Start", "pause": "Pause", "reject": "Reject", "draft": "Back to draft"}
ASK = {"pause": "Pause %s? Running agents keep working, no new Queen or Bee starts.",
       "reject": "Reject %s? The plan never starts again by itself; starting it again makes it a draft. Nothing is deleted.",
       "start": "Start %s?", "draft": "Put %s back to draft? Nothing is deleted."}


# Every plan of every Apiary.
def plan_rows(conn) -> list:
    """One row per plan file of every registered Apiary (and per plan the table knows): apiary, plan_path, name, status, has_hive, stopped.

    The status comes from the plans table when the plan is in it, else from the plan file."""
    known = {(r["project"], r["path"]): r["status"] for r in db.rows(conn, "SELECT project, path, status FROM plans")}
    hive_keys = {(r["project"], r["plan_path"]) for r in db.rows(conn, "SELECT project, plan_path FROM hives")}
    found = {}
    for project in db.rows(conn, "SELECT name, workspace FROM projects ORDER BY name"):
        ws = project["workspace"]
        if not ws or not os.path.isdir(ws):
            continue
        try:
            pcfg = config.load_project(ws)
            for plan in plan_mod.discover_plans(str(ws), pcfg["planGlobs"], pcfg["planExclude"]):
                found[(project["name"], os.path.relpath(plan.path, ws).replace("\\", "/"))] = plan.status
        except Exception:   # an unreadable Apiary must not hide the others
            continue
    rows = []
    for key in sorted(set(found) | set(known)):
        status = known.get(key) or found.get(key) or "DRAFT"
        stopped = status == "PAUSED" and key in known and hives.is_stopped(conn, *key)
        rows.append({"apiary": key[0], "plan_path": key[1], "name": gui_data.hive_name(key[1]), "status": status,
                     "has_hive": key in hive_keys, "stopped": stopped})
    return rows


# The actions a plan offers.
def actions(row) -> list:
    """(label, action) for a row: start for a draft, a paused or a rejected plan; pause for READY, SUBMITTED and IN_PROGRESS;
    reject for a draft or a stopped Hive; back to draft for a stopped or rejected plan; nothing for DONE."""
    status, stopped = row["status"], row.get("stopped")
    names = []
    if status in ("DRAFT", "PAUSED", "REJECTED"):
        names.append("start")
    if status in ("READY", "SUBMITTED", "IN_PROGRESS"):
        names.append("pause")
    if status == "DRAFT" or stopped:
        names.append("reject")
    if status == "REJECTED" or stopped:
        names.append("draft")
    return [(LABELS[n], n) for n in names]


# Ask, then change the plan.
def run_action(conn, row, action, confirm) -> bool:
    """After `confirm(text)` agrees, run the plan control `action` for the row; False when declined. Errors are raised."""
    if not confirm(ASK[action] % row["name"]):
        return False
    key = (row["apiary"], row["plan_path"])
    with loop.hive_schedule_lock():
        if not conn.execute("SELECT 1 FROM plans WHERE project=? AND path=?", key).fetchone():
            runtime.sync_plans(conn, key[0])   # a plan file the table does not know yet
        {"start": hives.start_plan, "pause": hives.pause_plan, "reject": hives.reject_plan, "draft": hives.back_to_draft}[action](conn, *key)
    return True


# The window.
class HivesWindow:
    """A table of all plans (Apiary, Hive, status, Hive yes/no); a right click opens the actions of the row."""

    def __init__(self, root, conn, mono="Consolas", on_changed=None):
        """Open the window below `root`; `on_changed()` is called after every change."""
        import tkinter as tk
        from tkinter import ttk
        from . import gui_dialog
        self.tk, self.conn, self.mono, self.on_changed, self.rows = tk, conn, mono, on_changed, []
        self.root = root
        self.win = tk.Toplevel(root)
        self.win.title("HaiveQueen Hives")
        self.win.geometry("900x560")
        self.tree = ttk.Treeview(self.win, columns=("apiary", "hive", "status", "has"), show="headings", selectmode="browse")
        for column, title, width in (("apiary", "Apiary", 160), ("hive", "Hive", 420), ("status", "Status", 130), ("has", "Has Hive", 80)):
            self.tree.heading(column, text=title)
            self.tree.column(column, width=width, anchor="w")
        bar = gui_dialog.scrollbar(self.win, self.tree.yview)
        self.tree.configure(yscrollcommand=bar.set)
        bar.pack(side="right", fill="y")
        self.tree.pack(side="left", fill="both", expand=True)
        self.tree.bind("<Button-3>", self.on_context)
        self.refresh()
        self.win.after(REFRESH_MS, self.tick)

    def tick(self):
        """Refresh now and again after REFRESH_MS while the window exists."""
        if self.win.winfo_exists():
            self.refresh()
            self.win.after(REFRESH_MS, self.tick)

    def refresh(self):
        """Reload all rows and keep the selection."""
        picked = self.tree.selection()
        self.rows = plan_rows(self.conn)
        self.tree.delete(*self.tree.get_children())
        for n, row in enumerate(self.rows):
            self.tree.insert("", "end", iid=str(n), values=(row["apiary"], row["name"], row["status"].lower(), "yes" if row["has_hive"] else "no"))
        if picked and picked[0] in self.tree.get_children():
            self.tree.selection_set(picked[0])

    def confirm(self, text) -> bool:
        """Ask a yes/no question in the dashboard look."""
        from . import gui_dialog
        return gui_dialog.ask(self.win, "HaiveQueen", text, self.mono)

    def menu_items(self, row) -> list:
        """The menu entries (label, callable) of a row; each callable asks first and then changes the plan."""
        return [(label, lambda action=action: self.apply(row, action)) for label, action in actions(row)]

    def apply(self, row, action):
        """Run an action for a row, show a problem, reload."""
        from . import gui_dialog
        try:
            run_action(self.conn, row, action, self.confirm)
        except Exception as exc:
            gui_dialog.error(self.win, "HaiveQueen", "Could not change the plan: %s" % exc, self.mono)
        self.refresh()
        if self.on_changed:
            self.on_changed()

    def on_context(self, event):
        """A right click selects the row under the pointer and opens its menu (nothing for DONE)."""
        from . import gui_dialog
        iid = self.tree.identify_row(event.y)
        if not iid:
            return
        self.tree.selection_set(iid)
        items = self.menu_items(self.rows[int(iid)])
        if items:
            gui_dialog.popup_menu(self.win, items, self.mono)

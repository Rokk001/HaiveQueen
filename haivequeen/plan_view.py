"""Plan listing and actions shared by browser clients."""
import os

from . import config, db, dashboard_data, hives, loop, runtime
from . import plans as plan_mod
from .i18n import N_, tr

REFRESH_MS = 5000
LABELS = {"start": N_("Start"), "pause": N_("Pause"), "reject": N_("Cancel plan"), "draft": N_("Back to draft")}
ASK = {"pause": N_("Pause %s? Running agents keep working, no new Queen or Agent starts."),
       "reject": N_("Cancel %s? The plan never starts again by itself; starting it again makes it a draft. Nothing is deleted."),
       "start": N_("Start %s?"), "draft": N_("Put %s back to draft? Nothing is deleted.")}


# Every plan of every Apiary.
def plan_rows(conn) -> list:
    """One row per plan file of every registered Project (and per plan the table knows): project, plan_path, name, status, has_hive, stopped, error, waiting_for.

    The status comes from the plans table when the plan is in it, else from the plan file. `error` names a broken plan header, an unknown status or class
    (the plan is then not silently a DRAFT or medium); `waiting_for` says what a WAITING plan waits for."""
    known = {(r["project"], r["path"]): r["status"] for r in db.rows(conn, "SELECT project, path, status FROM plans")}
    hive_keys = {(r["project"], r["plan_path"]) for r in db.rows(conn, "SELECT project, plan_path FROM hives")}
    found, errors = {}, {}
    for project in db.rows(conn, "SELECT name, workspace FROM projects ORDER BY name"):
        ws = project["workspace"]
        if not ws or not os.path.isdir(ws):
            continue
        try:
            pcfg = config.load_project(ws)
            for plan in plan_mod.discover_plans(str(ws), pcfg["planGlobs"], pcfg["planExclude"]):
                key = (project["name"], os.path.relpath(plan.path, ws).replace("\\", "/"))
                found[key] = plan.status
                if plan.errors:
                    errors[key] = "; ".join(plan.errors)
        except Exception:   # an unreadable Apiary must not hide the others
            continue
    rows = []
    for key in sorted(set(found) | set(known)):
        status = known.get(key) or found.get(key) or "DRAFT"
        stopped = status == "PAUSED" and key in known and hives.is_stopped(conn, *key)
        rows.append({"apiary": key[0], "plan_path": key[1], "name": dashboard_data.hive_name(key[1]), "status": status,
                     "has_hive": key in hive_keys, "stopped": stopped, "error": errors.get(key, ""),
                     "waiting_for": hives.waiting_for(conn, *key) if status == "WAITING" else ""})
    return rows


# The text of the status column.
def status_text(row) -> str:
    """The status in lower case; a header error is shown instead of a silent status, a WAITING plan says what it waits for."""
    if row.get("error"):
        return tr("error: %s", row["error"])
    if row.get("waiting_for"):
        return "%s (%s)" % (row["status"].lower(), row["waiting_for"])
    return row["status"].lower()


# The actions a plan offers.
def actions(row) -> list:
    """(label, action) for a row: start for a draft, a paused or a cancelled plan; pause for a sent, prepared or running plan;
    cancel for a draft or a stopped Plan; back to draft for a stopped or cancelled plan; nothing for DONE."""
    status, stopped = row["status"], row.get("stopped")
    names = []
    if status in ("DRAFT", "PAUSED", "CANCELLED"):
        names.append("start")
    if status in ("SENT", "PREPARING", "QUEUED", "RUNNING", "WAITING", "REVIEW"):
        names.append("pause")
    if status == "DRAFT" or stopped:
        names.append("reject")
    if status == "CANCELLED" or stopped:
        names.append("draft")
    return [(tr(LABELS[n]), n) for n in names]


# Ask, then change the plan.
def run_action(conn, row, action, confirm) -> bool:
    """After `confirm(text)` agrees, run the plan control `action` for the row; False when declined. Errors are raised."""
    if not confirm(tr(ASK[action], row["name"])):
        return False
    key = (row["apiary"], row["plan_path"])
    with loop.hive_schedule_lock():
        if not conn.execute("SELECT 1 FROM plans WHERE project=? AND path=?", key).fetchone():
            runtime.sync_plans(conn, key[0])   # a plan file the table does not know yet
        {"start": hives.start_plan, "pause": hives.pause_plan, "reject": hives.reject_plan, "draft": hives.back_to_draft}[action](conn, *key)
    return True

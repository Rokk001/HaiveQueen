"""Self-update of the live folder: `python -m haivequeen self-update` only spawns a detached worker and returns at once.

The worker (`self-update --worker`) takes the lock file, writes the STOP flag, waits until the loop and every running agent (all projects)
ended by themselves (it ends nothing), fast-forwards main of the live folder to origin/v2, pushes main, installs the package again only when
pyproject.toml changed, starts the loop and checks the first round. A failure after the merge is rolled back with `git reset --keep`
(rollback) and filed as an open question of project HaiveQueen. Every run is one row of the table self_updates.
The live folder is the folder of this package: no path of a machine is written into the tool.
"""
import json
import os
import subprocess
import sys
import time
from pathlib import Path

from . import db, loop, runstate
from . import platform as osplat

PROJECT = "HaiveQueen"
FAULT_KIND = "self_update_rollback"
DETACHED_PROCESS = 0x00000008
CREATE_NEW_PROCESS_GROUP = 0x00000200
CREATE_BREAKAWAY_FROM_JOB = 0x01000000
TASK_NAME = "HaiveQueenSelfUpdate"
STARTING_GRACE = 120   # seconds an agent row without a process id counts as starting (as routine.repair_stale_agents)


# ---- places ----

def live_folder() -> Path:
    """The live folder: the folder that holds this package (so `python -m haivequeen` there imports the live code)."""
    return Path(__file__).resolve().parent.parent


def lock_path() -> Path:
    """Path of the self-update lock file (the process id of the running worker) in the machine data folder."""
    return osplat.data_dir() / "self-update.lock"


# ---- starting the detached worker ----

def worker_argv() -> list:
    """Command line of the worker: the interpreter with `-m haivequeen self-update --worker` (never through bzz.exe, which pip cannot rewrite while it runs)."""
    return [osplat.python_executable(), "-m", "haivequeen", "self-update", "--worker"]


def spawn_worker(live=None) -> dict:
    """Start the worker without inherited handles (DEVNULL, close_fds, detached, own process group, outside the job) and return at once.

    Windows refusing the breakaway flag falls back to a one-shot scheduled task. Returns {"how": "detached"|"schtasks"}.
    """
    live = Path(live or live_folder())
    argv = worker_argv()
    common = {"cwd": str(live), "stdin": subprocess.DEVNULL, "stdout": subprocess.DEVNULL, "stderr": subprocess.DEVNULL, "close_fds": True}
    if not osplat.is_windows():
        subprocess.Popen(argv, start_new_session=True, **common)
        return {"how": "detached"}
    try:
        subprocess.Popen(argv, creationflags=DETACHED_PROCESS | CREATE_NEW_PROCESS_GROUP | CREATE_BREAKAWAY_FROM_JOB, **common)
        return {"how": "detached"}
    except OSError:
        command = 'cmd /c cd /d "%s" && "%s" -m haivequeen self-update --worker' % (live, argv[0])
        subprocess.run(["schtasks", "/create", "/tn", TASK_NAME, "/tr", command, "/sc", "once", "/st", "00:00", "/f"],
                       check=True, stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
        subprocess.run(["schtasks", "/run", "/tn", TASK_NAME], check=True,
                       stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
        return {"how": "schtasks"}


# ---- helpers of the worker ----

def run_command(argv: list, cwd) -> tuple:
    """Run a program in `cwd` and return (exit code, output text); a missing program gives (127, message)."""
    try:
        res = subprocess.run(argv, cwd=str(cwd), stdin=subprocess.DEVNULL, capture_output=True, text=True, encoding="utf-8", errors="replace")
    except OSError as exc:
        return 127, str(exc)
    return res.returncode, (res.stdout or "") + (res.stderr or "")


def busy_agents(conn, alive=None, now=None) -> list:
    """Names of the agent rows (all projects) that still run: starting or running with a living process.

    A row whose process is gone counts as ended (as when a stopped loop repairs it); a row without a process id counts for STARTING_GRACE seconds.
    """
    alive = alive or osplat.process_alive
    now = now or time.time()
    marks = ",".join("?" * len(runstate.ACTIVE))
    busy = []
    for r in conn.execute("SELECT name, pid, started_at FROM agents WHERE archived=0 AND status IN (%s)" % marks, runstate.ACTIVE):
        if r["pid"]:
            if alive(r["pid"]):
                busy.append(r["name"])
            continue
        started = db.parse_time(r["started_at"])
        if started is not None and now - started.timestamp() <= STARTING_GRACE:
            busy.append(r["name"])
    return busy


def wait_until_quiet(conn, sleep, poll: float, alive=None, loop_pid=None) -> None:
    """Wait without a time limit until no loop process and no agent of any project runs; nothing is ended."""
    loop_pid = loop_pid or loop.loop_running
    while loop_pid() or busy_agents(conn, alive):
        sleep(poll)


def start_row(conn, old_commit: str) -> int:
    """Insert the self_updates row of a starting run and return its id."""
    with db.transaction(conn):
        return conn.execute("INSERT INTO self_updates (started, old_commit) VALUES (?,?)", (db.now(), old_commit)).lastrowid


def end_row(conn, row_id: int, new_commit: str, result: str, note: str) -> None:
    """Write end time, new commit, result (ok, rollback or failed) and note into the row and log one event."""
    with db.transaction(conn):
        conn.execute("UPDATE self_updates SET ended=?, new_commit=?, result=?, note=? WHERE id=?", (db.now(), new_commit, result, note, row_id))
        db.add_event(conn, runstate.GLOBAL_PROJECT, "self-update", "self-update %s: %s" % (result, note), data={"result": result, "id": row_id})


def first_round_problems(conn, since_event: int, panes=None) -> list:
    """Problems seen after the start: an ERROR line in the rounds log or a 'vanished' line since `since_event`, not exactly one Queen tab.

    `panes` returns the list of live Queen pane ids, or None when the terminal cannot be read (that check is then skipped).
    """
    problems = []
    for r in conn.execute("SELECT kind, text FROM events WHERE id>? ORDER BY id", (since_event,)):
        text = r["text"] or ""
        if r["kind"] == "rounds" and "ERROR" in text:
            problems.append("ERROR in ROUNDS.log: " + text[:200])
        if "vanished" in text:
            problems.append("vanished: " + text[:200])
    ids = (panes or queen_panes)()
    if ids is not None and len(ids) != 1:
        problems.append("%d Queen tabs instead of 1" % len(ids))
    return problems


def queen_panes():
    """Ids of the live Queen panes of the configured window, or None when the terminal cannot be read."""
    from . import config
    from . import scheduler as sch
    from .terminal import get_terminal
    try:
        settings = sch.build_settings(config.load_config(), {})
        term = get_terminal(settings.get("terminal", "auto"), str(osplat.runs_dir() / "_panes"))
        return [p.id for p in term.list_panes(settings.get("window", "HaiveQueen")) if p.alive and "| all projects | QUEEN |" in p.title]
    except Exception:
        return None


def wait_first_round(conn, started_after: str, sleep, poll: float, timeout: float, loop_pid=None) -> bool:
    """True once the new loop runs and has finished a round after `started_after`; False when `timeout` seconds pass first."""
    loop_pid = loop_pid or loop.loop_running
    start = time.monotonic()
    after = db.parse_time(started_after)
    while time.monotonic() - start <= timeout:
        state = loop.read_state(conn)
        since = db.parse_time((state or {}).get("since"))
        if loop_pid() and state and state.get("status") == "idle" and since and after and since >= after:
            return True
        sleep(poll)
    return False


def file_fault_rollback(conn, text: str) -> None:
    """File the rollback as an open question of project HaiveQueen (file_fault of haivequeen.faults, imported only here)."""
    from haivequeen.faults import file_fault
    file_fault(conn, PROJECT, FAULT_KIND, text)


def changed_files(live, old: str, new: str, run) -> list:
    """Names of the files that differ between two commits of the live folder."""
    code, out = run(["git", "-C", str(live), "diff", "--name-only", old, new], live)
    return out.split() if code == 0 else []


def rollback(conn, live, old: str, new: str, why: str, run, restart) -> tuple:
    """Reset the live folder to `old` (git reset --keep), install again when pyproject.toml differed, start the loop, file the open question.

    Returns (result, note): "rollback" when the old state runs again, else "failed" with what went wrong. v2 and origin are not touched.
    """
    pyproject = "pyproject.toml" in changed_files(live, old, new, run)
    code, out = run(["git", "-C", str(live), "reset", "--keep", old], live)
    if code != 0:
        return "failed", "%s; rollback failed: git reset --keep: %s" % (why, out.strip()[:300])
    if pyproject:
        code, out = run([osplat.python_executable(), "-m", "pip", "install", "-e", str(live)], live)
        if code != 0:
            return "failed", "%s; rollback: pip install -e failed: %s" % (why, out.strip()[:300])
    ok, out = restart()
    note = "%s; rolled back to %s" % (why, old[:12])
    try:
        file_fault_rollback(conn, "The self-update to %s failed (%s) and was rolled back to %s." % (new[:12], why, old[:12]))
    except Exception as exc:   # the question is a report: failing to file it must not hide the rollback
        note += "; question not filed: %s" % exc
    if not ok:
        return "failed", "%s; start after rollback failed: %s" % (note, out)
    return "rollback", note


# ---- the update ----

def run_update(conn, live=None, run=run_command, sleep=time.sleep, alive=None, poll: float = 5.0, round_timeout: float = 1800.0,
               panes=None, loop_pid=None) -> dict:
    """Do one self-update of the live folder and return {"result", "note", "old", "new"} (also written to self_updates).

    Takes the lock (a second run ends at once with result "locked" and no row), writes STOP, waits for loop and agents, updates and checks the
    first round. Injectable parts (`run`, `sleep`, `alive`, `panes`, `loop_pid`) serve the tests, which never touch the real flag, lock or agents.
    """
    live = Path(live or live_folder())
    holder = loop._take_pid_lock(lock_path(), own_ok=False, wait=0.5)
    if holder:
        return {"result": "locked", "note": "another self-update runs (pid %s)" % holder, "old": "", "new": ""}
    row_id = None
    old = new = ""
    try:
        old = run(["git", "-C", str(live), "rev-parse", "HEAD"], live)[1].strip()
        row_id = start_row(conn, old)
        stop = loop.stop_flag()
        stop.parent.mkdir(parents=True, exist_ok=True)
        stop.write_text("stop requested %s (self-update)\n" % db.now(), encoding="utf-8")
        wait_until_quiet(conn, sleep, poll, alive, loop_pid)

        def restart():
            """Start the loop through the live code; (True, '') when `start` answered with exit 0."""
            code, out = run([osplat.python_executable(), "-m", "haivequeen", "start"], live)
            return code == 0, out.strip()[:300]

        for argv in (["fetch", "origin"], ["merge", "--ff-only", "origin/v2"]):
            code, out = run(["git", "-C", str(live)] + argv, live)
            if code != 0:
                ok, why = restart()   # nothing was changed: only the stopped loop is started again
                result, note = "failed", "git %s: %s%s" % (" ".join(argv), out.strip()[:300], "" if ok else "; start failed: " + why)
                end_row(conn, row_id, old, result, note)
                return {"result": result, "note": note, "old": old, "new": old}
        new = run(["git", "-C", str(live), "rev-parse", "HEAD"], live)[1].strip()
        failure = ""
        if new != old:
            code, out = run(["git", "-C", str(live), "push", "origin", "main"], live)
            if code != 0:
                failure = "git push origin main: " + out.strip()[:300]
            elif "pyproject.toml" in changed_files(live, old, new, run):
                code, out = run([osplat.python_executable(), "-m", "pip", "install", "-e", str(live)], live)
                if code != 0:
                    failure = "pip install -e: " + out.strip()[:300]
        since_event = conn.execute("SELECT COALESCE(MAX(id), 0) FROM events").fetchone()[0]
        began = db.now()
        if not failure:
            ok, out = restart()
            if not ok:
                failure = "start: " + out
            elif not wait_first_round(conn, began, sleep, poll, round_timeout, loop_pid):
                failure = "no finished first round within %d s" % round_timeout
            else:
                found = first_round_problems(conn, since_event, panes)
                if found:
                    failure = "; ".join(found)
        if failure and new != old:
            result, note = rollback(conn, live, old, new, failure, run, restart)
        elif failure:
            result, note = "failed", failure
        else:
            result, note = "ok", ("updated to %s" % new[:12] if new != old else "already current at %s" % new[:12])
        end_row(conn, row_id, new, result, note)
        return {"result": result, "note": note, "old": old, "new": new}
    finally:
        try:
            lock_path().unlink()
        except OSError:
            pass


# ---- command line ----

def cmd_self_update(args, conn) -> int:
    """`self-update`: spawn the detached worker and return at once; `--worker` runs the update in this process."""
    if args.worker:
        res = run_update(conn)
        print(json.dumps({"ok": res["result"] in ("ok", "rollback"), **res}))
        return 0 if res["result"] in ("ok", "rollback") else 1
    print(json.dumps({"ok": True, **spawn_worker()}))
    return 0


def register(sub) -> None:
    """Add the command self-update to the sub-parser collection of haivequeen/cli.py."""
    p = sub.add_parser("self-update")
    p.add_argument("--worker", action="store_true", help="run the update in this process (the detached worker; the integrator never uses it)")
    p.set_defaults(fn=cmd_self_update)

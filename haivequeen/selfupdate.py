"""Self-update of the live folder: `python -m haivequeen self-update` only spawns a detached worker and returns at once.

The worker (`self-update --worker`) takes the lock file, writes the STOP flag, waits until the loop and every running agent (all projects)
ended by themselves (it ends nothing), fast-forwards main of the live folder to origin/<integrationBranch> of the settings of project HaiveQueen
(main when unset), pushes main, installs the package again only when
pyproject.toml changed, starts the loop and checks the first round. A failure after the merge is rolled back with `git reset --keep`
(rollback) and filed as an open question of project HaiveQueen. Every run is one row of the table self_updates.
The live folder is the folder of this package: no path of a machine is written into the tool.

With a version (`self-update --worker --version X`, started by the update button) the same worker installs the release X instead: it
fetches the tag vX from origin, fast-forwards to it and never pushes. While the worker holds its lock no Queen or Agent starts; its own
restart carries the variable BZZ_UPDATE_RESTART, which `start` accepts while the lock is held.

The checkout update without a version is the one-time transition: it is retired (refused with result "retired", no row, no
merge, no push) as soon as the release path has started, i.e. HaiveQueen published a release for project HaiveQueen or the live folder
is at a release tag vX.Y.Z. From then on the common release update (`bzz update`, the update button) is the only way.
"""
import json
import os
import subprocess
import sys
import time
from pathlib import Path

from . import config, db, loop, runstate
from . import platform as osplat

PROJECT = "HaiveQueen"
FAULT_KIND = "self_update_rollback"
PANE_FAULT_KIND = "self_update_pane"   # first-round problems (a pane that did not start): the new code stays live
DETACHED_PROCESS = 0x00000008
CREATE_NEW_PROCESS_GROUP = 0x00000200
CREATE_BREAKAWAY_FROM_JOB = 0x01000000
TASK_NAME = "HaiveQueenSelfUpdate"
RESTART_VAR = "BZZ_UPDATE_RESTART"   # set for the restart of the worker (see loop.cmd_start)
STARTING_GRACE = 120   # seconds an agent row without a process id counts as starting (as routine.repair_stale_agents)
ROUND_TIMEOUT = 1800.0   # seconds the worker waits for the first finished round of the restarted loop
ROUND_GRACE = 60.0   # seconds after that limit before a start ends a worker that is still there
OWNER_STOP = "stopped by the owner"   # in the note of a self-update that the owner ended with `bzz stop` or the Stop button


class OwnerStop(Exception):
    """The owner stopped HaiveQueen (`bzz stop`, Stop button) while the worker was waiting."""


def owner_stopped() -> bool:
    """True when the STOP flag exists and was not written by a self-update (the owner's `bzz stop` overwrites the text of the worker's flag)."""
    try:
        return "(self-update" not in loop.stop_flag().read_text(encoding="utf-8", errors="replace")
    except OSError:
        return False


# ---- places ----

def live_folder() -> Path:
    """The live folder: the folder that holds this package (so `python -m haivequeen` there imports the live code)."""
    return Path(__file__).resolve().parent.parent


def lock_path() -> Path:
    """Path of the self-update lock file (the process id of the running worker) in the machine data folder."""
    return osplat.data_dir() / "self-update.lock"


def round_path() -> Path:
    """Path of the marker (start time, seconds since 1970) that the worker keeps while it waits for the first round of the restarted loop."""
    return osplat.data_dir() / "self-update.round"


def integration_branch(conn) -> str:
    """The integrationBranch setting of project HaiveQueen (its project.toml, read fresh); "main" when the project or the setting is missing."""
    from . import scheduler
    env = next((e for e in scheduler.load_projects(conn, config.load_config()) if e.name == PROJECT), None)
    return str((env.settings if env else {}).get("integrationBranch") or "main")


def legacy_retired(conn, live=None, run=None) -> str:
    """Why the checkout update (no version) is retired, or "" while the one-time transition is still open.

    Retired once a release was published for project HaiveQueen to the repository of its code (kv release-last with release-last-repository;
    a release recorded without its repository went to the workspace remote and does not count) or the live folder's HEAD has a release tag
    vX.Y.Z in its history (it was installed from a release)."""
    import re
    from . import publish, runtime
    try:
        last = runtime.kv_get(conn, PROJECT, publish.LAST_KEY) if conn is not None and runtime.kv_get(conn, PROJECT, publish.LAST_REPO_KEY) else None
    except Exception:
        last = None
    if last:
        return "release %s was published" % last
    live = Path(live or live_folder())
    run = run or run_command
    code, out = run(["git", "-C", str(live), "describe", "--tags", "--abbrev=0", "--match", "v[0-9]*", "HEAD"], live)
    tag = (out or "").strip()
    if code == 0 and re.fullmatch(r"v(0|[1-9]\d*)\.(0|[1-9]\d*)\.(0|[1-9]\d*)", tag):
        return "the live folder is at release %s" % tag
    return ""


RETIRED_NOTE = "the checkout self-update was a one-time transition and is retired: %s; use `bzz update` or the update button"


# ---- starting the detached worker ----

def worker_argv(version=None) -> list:
    """Command line of the worker: the interpreter with `-m haivequeen self-update --worker` (never through bzz.exe, which pip cannot rewrite while it runs); with a release version `--version X`."""
    return [osplat.python_executable(), "-m", "haivequeen", "self-update", "--worker"] + (["--version", str(version)] if version else [])


def spawn_worker(live=None, version=None) -> dict:
    """Start the worker without inherited handles (DEVNULL, close_fds, detached, own process group, outside the job) and return at once.

    Windows refusing the breakaway flag falls back to a one-shot scheduled task. Returns {"how": "detached"|"schtasks"}.
    """
    live = Path(live or live_folder())
    argv = worker_argv(version)
    common = {"cwd": str(live), "stdin": subprocess.DEVNULL, "stdout": subprocess.DEVNULL, "stderr": subprocess.DEVNULL, "close_fds": True}
    if not osplat.is_windows():
        subprocess.Popen(argv, start_new_session=True, **common)
        return {"how": "detached"}
    try:
        subprocess.Popen(argv, creationflags=DETACHED_PROCESS | CREATE_NEW_PROCESS_GROUP | CREATE_BREAKAWAY_FROM_JOB, **common)
        return {"how": "detached"}
    except OSError:
        command = 'cmd /c cd /d "%s" && "%s" -m haivequeen self-update --worker%s' % (live, argv[0], " --version " + str(version) if version else "")
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
    from . import agent_process
    for r in conn.execute("SELECT name, status, pid, started_at, ended_at, extra_json FROM agents WHERE archived=0 AND status IN (%s)" % marks, runstate.ACTIVE):
        if r["pid"]:
            if agent_process.is_live(r, alive):
                busy.append(r["name"])
            continue
        started = db.parse_time(r["started_at"])
        if started is not None and now - started.timestamp() <= STARTING_GRACE:
            busy.append(r["name"])
    return busy


def wait_until_quiet(conn, sleep, poll: float, alive=None, loop_pid=None, stopped=None) -> None:
    """Wait without a time limit until no loop process and no agent of any project runs; nothing is ended. Raises OwnerStop when the owner stops HaiveQueen meanwhile."""
    loop_pid = loop_pid or loop.loop_running
    stopped = stopped or owner_stopped
    while loop_pid() or busy_agents(conn, alive):
        if stopped():
            raise OwnerStop()
        sleep(poll)


def start_row(conn, old_commit: str) -> int:
    """Insert the self_updates row of a starting run and return its id."""
    with db.transaction(conn):
        return conn.execute("INSERT INTO self_updates (started, old_commit) VALUES (?,?)", (db.now(), old_commit)).lastrowid


def end_row(conn, row_id: int, new_commit: str, result: str, note: str) -> None:
    """Write end time, new commit, result (ok, rollback or failed) and note into the row and log one event.

    The result "skipped" (stopped by the owner before anything changed) is stored as "failed" with the note "skipped, stopped by the owner": the table allows only the three results."""
    with db.transaction(conn):
        conn.execute("UPDATE self_updates SET ended=?, new_commit=?, result=?, note=? WHERE id=?",
                     (db.now(), new_commit, "failed" if result == "skipped" else result, note, row_id))
        db.add_event(conn, runstate.GLOBAL_PROJECT, "self-update", "self-update %s: %s" % (result, note), data={"result": result, "id": row_id})


def first_round_problems(conn, since_event: int) -> list:
    """Problems seen after the start: an ERROR line in the rounds log or a 'vanished' line since `since_event`."""
    problems = []
    for r in conn.execute("SELECT kind, text FROM events WHERE id>? ORDER BY id", (since_event,)):
        text = r["text"] or ""
        if r["kind"] == "rounds" and "ERROR" in text:
            problems.append("ERROR in ROUNDS.log: " + text)
        if "vanished" in text:
            problems.append("vanished: " + text)
    return problems


def wait_first_round(conn, started_after: str, sleep, poll: float, timeout: float, loop_pid=None, stopped=None) -> bool:
    """True once the new loop runs and has finished a round after `started_after`; False when `timeout` seconds pass first. Raises OwnerStop when the owner stops HaiveQueen meanwhile."""
    loop_pid = loop_pid or loop.loop_running
    stopped = stopped or owner_stopped
    start = time.monotonic()
    after = db.parse_time(started_after)
    while time.monotonic() - start <= timeout:
        if stopped():
            raise OwnerStop()
        state = loop.read_state(conn)
        since = db.parse_time((state or {}).get("since"))
        if loop_pid() and state and state.get("status") == "idle" and since and after and since >= after:
            return True
        sleep(poll)
    return False


def file_fault_rollback(conn, text: str, kind: str = FAULT_KIND) -> None:
    """File the rollback (or, with kind self_update_pane, the pane problems of a kept update) as an open question of project HaiveQueen (file_fault of haivequeen.faults, imported only here)."""
    from haivequeen.faults import file_fault
    file_fault(conn, PROJECT, kind, text)


def changed_files(live, old: str, new: str, run) -> list:
    """Names of the files that differ between two commits of the live folder."""
    code, out = run(["git", "-C", str(live), "diff", "--name-only", old, new], live)
    return out.split() if code == 0 else []


def start_pending_refusal(out: str) -> bool:
    """True when `start` refused (exit 1) only because another start is already bringing the loop up: that loop counts, the first round is awaited."""
    for line in reversed((out or "").splitlines()):
        if line.startswith("{"):
            try:
                return bool(json.loads(line).get("starting"))
            except ValueError:
                return False
    return False


def prepare_rollback(conn, sleep, poll, alive=None, loop_pid=None):
    """Drain the restarted runtime before changing files; a timeout is not proof of exit.

    Keep the update lock while draining. Even an old pending-start marker is
    inconclusive: only its removal by startup proves that launch has settled.
    """
    round_path().unlink(missing_ok=True)
    if owner_stopped():
        raise OwnerStop()
    stop = loop.stop_flag()
    stop.write_text("stop requested %s (self-update rollback)\n" % db.now(), encoding="utf-8")
    loop_pid = loop_pid or loop.loop_running
    while True:
        if owner_stopped():
            raise OwnerStop()
        pending = loop.start_pending_file().exists()
        running = loop_pid()
        agents = busy_agents(conn, alive)
        if not pending and not running and not agents:
            return
        sleep(poll)


def rollback(conn, live, old: str, new: str, why: str, run, restart) -> tuple:
    """Reset the live folder to `old` (git reset --keep), install again when pyproject.toml differed, start the loop, file the open question.

    Returns (result, note): "rollback" when the old state runs again, else "failed" with what went wrong. origin is not touched.
    """
    pyproject = "pyproject.toml" in changed_files(live, old, new, run)
    code, out = run(["git", "-C", str(live), "reset", "--keep", old], live)
    if code != 0:
        return "failed", "%s; rollback failed: git reset --keep: %s" % (why, out.strip()[:300])
    if pyproject:
        code, out = run([osplat.python_executable(), "-m", "pip", "install", "-e", str(live)], live)
        if code != 0:
            return "failed", "%s; rollback: pip install -e failed: %s" % (why, out.strip()[:300])
    if owner_stopped():
        return "failed", "%s; rolled back to %s; %s, not restarted" % (why, old[:12], OWNER_STOP)
    ok, out = restart()
    note = "%s; rolled back to %s" % (why, old[:12])
    try:
        file_fault_rollback(conn, "The self-update to %s failed (%s) and was rolled back to %s." % (new[:12], why, old[:12]))
    except Exception as exc:   # the question is a report: failing to file it must not hide the rollback
        note += "; question not filed: %s" % exc
    if not ok:
        return "failed", "%s; start after rollback failed: %s" % (note, out)
    return "rollback", note


# ---- stale lock ----

def is_update_worker(command_line: str) -> bool:
    """True when the command line is that of the HaiveQueen update worker (`-m haivequeen self-update --worker`)."""
    low = (command_line or "").lower()
    return "haivequeen" in low and "self-update" in low and "--worker" in low


def clear_stale_lock(conn, alive=None, cmdline=None, now=None, kill=None, head=None) -> str:
    """Delete the self-update lock when its process does not run, is not the update worker, or is a worker past its limit for the first round.

    Returns why the lock was cleared ("" when there is no lock or a living worker inside its limit holds it, or the process cannot be examined).
    A worker past its limit is ended. An update that is already in place stays (no rollback); the open row of the update history is closed with
    "ended at start: stale lock". One log line (event and beekeeper.log) is written. Injectable parts serve the tests."""
    try:
        pid = int(lock_path().read_text(encoding="ascii").strip())
    except (OSError, ValueError):
        return ""   # no lock, or one that is just being written
    alive = alive or osplat.process_alive
    if pid <= 0:
        why = "the lock names no process"
    elif not alive(pid):
        why = "process %d does not run" % pid
    else:
        line = (cmdline or osplat.process_command_line)(pid)
        if line is None:
            return ""   # cannot be told apart from a worker: the lock stays
        if not is_update_worker(line):
            why = "process %d is not the update worker" % pid
        else:
            try:
                began = float(round_path().read_text(encoding="ascii").strip())
            except (OSError, ValueError):
                return ""   # still waiting for running agents: it has no limit
            if (now if now is not None else time.time()) - began <= ROUND_TIMEOUT + ROUND_GRACE:
                return ""
            why = "the update worker (process %d) is past its limit for the first round" % pid
            (kill or osplat.kill_process_tree)(pid)
    for path in (round_path(), lock_path()):
        try:
            path.unlink()
        except OSError:
            pass
    text = "stale update lock cleared at start: " + why
    try:
        row = conn.execute("SELECT id, old_commit FROM self_updates WHERE ended IS NULL ORDER BY id DESC LIMIT 1").fetchone()
        if row:
            current = (head or (lambda: run_command(["git", "-C", str(live_folder()), "rev-parse", "HEAD"], live_folder())[1].strip()))()
            kept = bool(current) and current != (row["old_commit"] or "")
            note = "ended at start: stale lock (%s)" % why
            end_row(conn, row["id"], current if kept else (row["old_commit"] or ""), "ok" if kept else "skipped", note + ("; update kept" if kept else ""))
        else:
            with db.transaction(conn):
                db.add_event(conn, runstate.GLOBAL_PROJECT, "self-update", text)
    except Exception:   # the log must not keep HaiveQueen from starting
        pass
    try:
        log = osplat.runs_dir() / "beekeeper.log"
        log.parent.mkdir(parents=True, exist_ok=True)
        with open(log, "a", encoding="utf-8") as out:
            out.write("%s %s\n" % (db.now(), text))
    except OSError:
        pass
    return why


# ---- the update ----

def run_update(conn, live=None, run=run_command, sleep=time.sleep, alive=None, poll: float = 5.0, round_timeout: float = ROUND_TIMEOUT,
               loop_pid=None, version=None) -> dict:
    """Do one self-update of the live folder and return {"result", "note", "old", "new"} (also written to self_updates).

    Takes the lock (a second run ends at once with result "locked" and no row), writes STOP, waits for loop and agents, updates and checks the
    first round. With `version` the release tag v<version> is installed instead of origin/<integrationBranch>, without a push. Injectable parts (`run`, `sleep`, `alive`, `loop_pid`) serve the tests, which never touch the real flag, lock or agents.
    """
    live = Path(live or live_folder())
    if version is not None:
        from . import releases
        releases.next_version(str(version))   # only a plain version number reaches git
        from . import updates
        updates.validate_install_repository(version, live=live, run=run)
    else:
        why = legacy_retired(conn, live, run)
        if why:
            return {"result": "retired", "note": RETIRED_NOTE % why, "old": "", "new": ""}
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
        stop.write_text("stop requested %s (self-update%s)\n" % (db.now(), " to %s" % version if version else ""), encoding="utf-8")
        try:
            wait_until_quiet(conn, sleep, poll, alive, loop_pid)
        except OwnerStop:   # nothing was changed yet: the update is not applied; the owner's STOP flag stays for the next start to remove
            note = "skipped, " + OWNER_STOP
            end_row(conn, row_id, old, "skipped", note)
            return {"result": "skipped", "note": note, "old": old, "new": old}

        def restart():
            """Start the loop through the live code; (True, '') when `start` answered with exit 0 (or refused only because a start is pending)."""
            os.environ[RESTART_VAR] = "1"   # the started process inherits it: its start is allowed while this worker holds the lock
            try:
                code, out = run([osplat.python_executable(), "-m", "haivequeen", "start", "--keep-pause"], live)
            finally:
                os.environ.pop(RESTART_VAR, None)
            return code == 0 or start_pending_refusal(out), out.strip()

        if version is not None:
            steps = (["fetch", "origin", "tag", "v%s" % version, "--no-tags"], ["merge", "--ff-only", "v%s" % version])
        else:
            branch = integration_branch(conn)
            steps = (["fetch", "origin"], ["merge", "--ff-only", "origin/" + branch])
        for argv in steps:
            code, out = run(["git", "-C", str(live)] + argv, live)
            if code != 0:
                ok, why = restart()   # nothing was changed: only the stopped loop is started again
                result, note = "failed", "git %s: %s%s" % (" ".join(argv), out.strip()[:300], "" if ok else "; start failed: " + why)
                end_row(conn, row_id, old, result, note)
                return {"result": result, "note": note, "old": old, "new": old}
        new = run(["git", "-C", str(live), "rev-parse", "HEAD"], live)[1].strip()
        failure = ""
        pane_problems = ""
        owner_ended = False
        if new != old:
            code, out = run(["git", "-C", str(live), "push", "origin", "main"], live) if version is None else (0, "")   # a release install never pushes
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
            else:
                try:
                    round_path().write_text(str(time.time()), encoding="ascii")   # a start finds a worker that stays past its limit
                    finished = wait_first_round(conn, began, sleep, poll, round_timeout, loop_pid)
                except OwnerStop:   # the update is in place and stays (no rollback)
                    finished, owner_ended = True, True
                if not finished:
                    failure = "no finished first round within %d s" % round_timeout
                elif not owner_ended:
                    found = first_round_problems(conn, since_event)
                    pane_problems = "; ".join(found)   # the loop runs: pane problems are reported, never rolled back
        if failure and new != old:
            try:
                prepare_rollback(conn, sleep, poll, alive, loop_pid)
            except OwnerStop:
                result, note = "failed", failure + "; rollback cancelled, " + OWNER_STOP + "; update kept"
            except OSError as exc:
                result, note = "failed", failure + "; rollback could not establish quiescence: " + str(exc)
            else:
                result, note = rollback(conn, live, old, new, failure, run, restart)
        elif failure:
            result, note = "failed", failure
        else:
            result, note = "ok", ("updated to %s" % new[:12] if new != old else "already current at %s" % new[:12])
            if version is not None:
                note = "version %s: %s" % (version, note)
            if owner_ended:
                note += "; " + OWNER_STOP + " during the wait"
            if pane_problems:
                note += "; kept despite first round problems: " + pane_problems
                try:
                    file_fault_rollback(conn, "The self-update to %s is live; its first round had problems: %s" % (new[:12], pane_problems), PANE_FAULT_KIND)
                except Exception as exc:   # the question is a report: failing to file it must not hide the result
                    note += "; question not filed: %s" % exc
        end_row(conn, row_id, new, result, note)
        return {"result": result, "note": note, "old": old, "new": new}
    finally:
        for path in (round_path(), lock_path()):
            try:
                path.unlink()
            except OSError:
                pass


# ---- command line ----

def cmd_self_update(args, conn) -> int:
    """`self-update`: spawn the detached worker and return at once; `--worker` runs the update in this process."""
    if args.worker:
        res = run_update(conn, version=getattr(args, "version", None))
        print(json.dumps({"ok": res["result"] in ("ok", "rollback", "skipped"), **res}))
        return 0 if res["result"] in ("ok", "rollback", "skipped") else 1
    version = getattr(args, "version", None)
    if not version:
        why = legacy_retired(conn)
        if why:
            print(json.dumps({"ok": False, "result": "retired", "note": RETIRED_NOTE % why}))
            return 1
    print(json.dumps({"ok": True, **(spawn_worker(version=version) if version else spawn_worker())}))
    return 0


def register(sub) -> None:
    """Add the command self-update to the sub-parser collection of haivequeen/cli.py."""
    p = sub.add_parser("self-update")
    p.add_argument("--worker", action="store_true", help="run the update in this process (the detached worker; the integrator never uses it)")
    p.add_argument("--version", help="install this release tag v<version> (the update button) instead of origin/<integrationBranch>")
    p.set_defaults(fn=cmd_self_update)

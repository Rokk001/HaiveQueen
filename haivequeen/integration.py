"""The integration helpers of a Project: the progress-watched command runner (compile check, Build, Deploy), the machine slots of the compile checks and the
start text of the Queen. The Orchestrator (orchestrator.py) runs the steps; retire_legacy_steps removes the step packages of the time before it.
"""
import json
import errno
from contextlib import contextmanager
import os
import re
import subprocess
import time
from datetime import datetime

from . import db, localtime, plans, platform as osplat, proctree, runtime

COMPILE_MILESTONE = re.compile(r"^(\w+)\t\S+ Compile test\b")   # the live line of a compile check in milestones.txt (the dashboard shows it)
COMPILE_KEY = "compile-check:"   # + Queen name: the stored result of `bzz compile-check` for that Queen run
POLL_SECONDS = 1.0   # how often a compile check looks at its process and at the lock of the machine
COMPILE_AGENT_KEY = "compile-agent:"   # + name or worktree: the stored result of an Agent; it never counts for `bzz ready`


# Whether the pipeline builds or deploys.
def builds(settings):
    """True when the Project has a Build command or a Deploy command (then an integration needs free RAM and a job count)."""
    return bool(str(settings.get("buildCommand") or "").strip() or str(settings.get("deployCommand") or "").strip())


# Step packages of the time before the Orchestrator.
def retire_legacy_steps(conn):
    """Remove the packages INTEGRATION-BUILD and INTEGRATION-DEPLOY of every plan with their results and close their open questions (the capability
    owner question included): the Orchestrator runs the Build and Deploy commands itself, so these packages and questions block no plan. Returns the
    number of packages and questions removed."""
    from . import bees
    bees.ensure_attempts(conn)
    ids = tuple(plans.LEGACY_STEP_PACKAGES)
    marks = ",".join("?" * len(ids))
    removed = 0
    with db.transaction(conn):
        removed += conn.execute("DELETE FROM packages WHERE id IN (%s)" % marks, ids).rowcount
        conn.execute("DELETE FROM package_progress WHERE package IN (%s)" % marks, ids)
        conn.execute("DELETE FROM bee_completed WHERE package IN (%s)" % marks, ids)
        for row in conn.execute("SELECT id, project FROM questions WHERE status IN ('open','triage','fault_check') AND item IN (%s)" % marks, ids).fetchall():
            conn.execute("UPDATE questions SET status='answered', answer=?, answered_by='auto', answered_at=? WHERE id=?",
                         ("No longer needed: the Orchestrator runs the Build and Deploy commands, no provider is involved.", db.now(), row["id"]))
            removed += 1
    return removed


# The commit a worktree stands on.
def head_commit(cwd):
    """The full commit ID of HEAD in the folder, or '' when it cannot be read."""
    if not cwd:
        return ""
    try:
        done = subprocess.run(["git", "-C", str(cwd), "rev-parse", "HEAD"], capture_output=True, text=True, encoding="utf-8", errors="replace", timeout=30)
    except (OSError, subprocess.SubprocessError):
        return ""
    return done.stdout.strip() if done.returncode == 0 else ""


# The stored result of the compile check of a Queen run.
def compile_result(conn, project, queen_name):
    """The result `bzz compile-check` stored for the Queen ({ok, exit, commit, time, seconds, command}), or None."""
    raw = runtime.kv_get(conn, project, COMPILE_KEY + queen_name)
    try:
        data = json.loads(raw) if raw else None
    except ValueError:
        data = None
    return data if isinstance(data, dict) else None


# The slots that let a limited number of compile checks run at the same time per machine.
COMPILE_LIMIT_KEY = "maxConcurrentCompileChecks"   # the General setting "Max concurrent compile checks"
DEFAULT_COMPILE_LIMIT = 2


def compile_limit(settings) -> int:
    """The maximum number of compile checks that run at the same time on this computer: the setting maxConcurrentCompileChecks (a whole number from 1,
    default 2); a wrong value gives the default so that no wrong value stops all checks."""
    value = (settings or {}).get(COMPILE_LIMIT_KEY, DEFAULT_COMPILE_LIMIT)
    return value if isinstance(value, int) and not isinstance(value, bool) and value >= 1 else DEFAULT_COMPILE_LIMIT


def _compile_lock_path():
    """The file of the machine (in the machine data folder) that lists the compile checks that run now."""
    from . import config
    return config.data_dir() / "compile-check.lock"


@contextmanager
def _compile_lock_guard(path, poll):
    """Serialize publication and recovery; the OS releases this guard when a process dies."""
    guard = os.open(str(path) + ".guard", os.O_CREAT | os.O_RDWR, 0o600)
    try:
        if os.name == "nt":
            import msvcrt
            def lock():
                os.lseek(guard, 0, os.SEEK_SET)
                msvcrt.locking(guard, msvcrt.LK_NBLCK, 1)
            def unlock():
                os.lseek(guard, 0, os.SEEK_SET)
                msvcrt.locking(guard, msvcrt.LK_UNLCK, 1)
        else:
            import fcntl
            def lock():
                fcntl.flock(guard, fcntl.LOCK_EX | fcntl.LOCK_NB)
            def unlock():
                fcntl.flock(guard, fcntl.LOCK_UN)
        while True:
            try:
                lock()
                break
            except OSError as exc:
                if exc.errno not in (errno.EACCES, errno.EAGAIN, errno.EDEADLK):
                    raise
                time.sleep(poll)
        try:
            yield
        finally:
            unlock()
    finally:
        os.close(guard)


def _read_holders(path) -> list:
    """The listed checks [{token, pid}] of the slot file; an unreadable or wrong file lists none."""
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
        holders = data.get("holders") if isinstance(data, dict) else None
    except (OSError, ValueError):
        return []
    return [h for h in holders if isinstance(h, dict) and isinstance(h.get("pid"), int)] if isinstance(holders, list) else []


def _live_holders(path) -> list:
    """The listed checks whose process still exists (the slot of a process that died is free again)."""
    return [h for h in _read_holders(path) if osplat.process_alive(h["pid"])]


def _compile_holders() -> list:
    """The compile checks that run now on this computer (without a wait for the guard that blocks a writer)."""
    return _live_holders(_compile_lock_path())


def running_checks() -> int:
    """How many compile checks run now on this computer; builds count them with the builds when they share the cores."""
    return len(_compile_holders())


def _running_builds() -> int:
    """How many agents run a build now (the database of this computer)."""
    from . import buildjobs, config
    conn = None
    try:
        conn = db.connect(config.db_path())
        return len(buildjobs.running_builds(conn))
    except Exception:
        return 0
    finally:
        if conn is not None:
            conn.close()


def _slot_wait(settings, holders, builds) -> str:
    """Why a compile check must wait now (text), or None when it may start: the limit is reached, the free RAM is below the minimum free RAM before a
    build, or the cores of CPU cores per build are used up by the running builds and compile checks together (a check needs one core)."""
    from . import buildjobs
    if len(holders) >= compile_limit(settings):
        return "%d compile checks run and the maximum is %d" % (len(holders), compile_limit(settings))
    bj = buildjobs.settings_from((settings or {}).get("buildJobs") or 0)
    reason = buildjobs.waiting_for_ram(bj, osplat.free_ram_mb())
    if reason:
        return reason
    cores = buildjobs.core_limit(bj, osplat.logical_cores())
    if builds() + len(holders) >= cores:
        return "all %d CPU cores for builds are in use by builds and compile checks" % cores
    return None


# Wait for a slot of the machine.
def _acquire_compile_slot(settings, poll, builds=None):
    """Wait until a compile check may start (see _slot_wait), list it and return its token for _release_compile_slot."""
    import uuid
    path = _compile_lock_path()
    path.parent.mkdir(parents=True, exist_ok=True)
    builds = builds or _running_builds
    while True:
        with _compile_lock_guard(path, poll):
            holders = _live_holders(path)
            if _slot_wait(settings, holders, builds) is None:
                token = uuid.uuid4().hex
                path.write_text(json.dumps({"holders": holders + [{"token": token, "pid": os.getpid()}]}), encoding="utf-8")
                return token
            path.write_text(json.dumps({"holders": holders}), encoding="utf-8")
        time.sleep(poll)


# Give a slot back.
def _release_compile_slot(token, poll):
    """Remove the check with this token from the list."""
    path = _compile_lock_path()
    with _compile_lock_guard(path, poll):
        path.write_text(json.dumps({"holders": [h for h in _live_holders(path) if h.get("token") != token]}), encoding="utf-8")


# Run the compile command and watch its progress.
def _watch_compile(command, cwd, stall_seconds, max_seconds, poll, env=None, label="compile check", on_start=None):
    """Run `command` in `cwd` until it ends; returns (exit code, output text). The check ends red (124) after max_seconds of running and red (125,
    "stalled") when for stall_seconds neither a new output line appeared nor the CPU time of its process tree grew. `env` adds environment variables,
    `label` names the command in the messages and `on_start(pid)` is called once the process runs (the Orchestrator keeps it to end the command)."""
    import collections
    import threading
    lines = collections.deque(maxlen=400)
    state = {"lines": 0}

    def read(stream):
        for line in iter(stream.readline, ""):
            lines.append(line.rstrip("\r\n"))
            state["lines"] += 1

    # The configured command line is the owner's own text, so the system shell runs it (named explicitly, no shell flag): cmd /s keeps a quoted program path intact.
    line = 'cmd.exe /d /s /c "%s"' % command if os.name == "nt" else ["/bin/sh", "-c", command]
    proc = subprocess.Popen(line, cwd=cwd, stdin=subprocess.DEVNULL, stdout=subprocess.PIPE, stderr=subprocess.STDOUT, text=True, encoding="utf-8", errors="replace",
                            env={**os.environ, **env} if env else None)
    if on_start:
        on_start(proc.pid)
    proctree.exempt_register(proc.pid)   # started by HaiveQueen, not by the agent: the cleanup of the agent's leftover processes leaves it alone
    reader = threading.Thread(target=read, args=(proc.stdout,), daemon=True)
    reader.start()
    started = last_progress = time.monotonic()
    seen_lines, seen_cpu, ended = 0, -1.0, None
    try:
        while proc.poll() is None:
            now = time.monotonic()
            cpu = proctree.tree_cpu_seconds(proc.pid)
            if state["lines"] != seen_lines or abs(cpu - seen_cpu) > 0.001:
                seen_lines, seen_cpu, last_progress = state["lines"], cpu, now
            if now - started > max_seconds:
                ended = (124, "the %s ran longer than %g minutes (compileMaxMinutes)" % (label, max_seconds / 60.0))
            elif now - last_progress > stall_seconds:
                ended = (125, "stalled: the %s made no output and used no CPU for %g minutes (compileStallMinutes)" % (label, stall_seconds / 60.0))
            if ended:
                osplat.kill_process_tree(proc.pid)
                break
            time.sleep(poll)
        try:
            proc.wait(timeout=30)
        except subprocess.TimeoutExpired:
            pass
        reader.join(timeout=5)
    finally:
        proctree.exempt_clear(proc.pid)
    text = "\n".join(lines)
    if ended:
        return ended[0], ended[1] + ("; last output lines:\n" + "\n".join(list(lines)[-20:]) if lines else "; no output at all")
    return proc.returncode, text


# Run the compile check of the project for a Queen or an Agent.
def run_compile_check(env, queen, cwd=None, key=None, poll=None, milestones=True):
    """Run the setting compileCheck in the worktree `cwd` (default: that of `queen`, the row of the caller), store {ok, exit, commit, time, seconds, command} under
    `key` (default: the result of the Queen's run that `bzz ready` reads), add the live line to the milestones of the caller and return the result with the end of the
    output under "output". At most the setting maxConcurrentCompileChecks checks run at the same time per machine, and only while RAM and cores are free: waiting counts for neither limit. The settings compileStallMinutes (10) and
    compileMaxMinutes (120) end a check red; the commit is the one the worktree stood on before the run."""
    poll = POLL_SECONDS if poll is None else poll
    command = str(env.settings.get("compileCheck") or "").strip()
    cwd = str(cwd or queen.get("cwd") or "")
    commit = head_commit(cwd)
    stall = float(env.settings.get("compileStallMinutes", 10)) * 60.0
    longest = float(env.settings.get("compileMaxMinutes", 120)) * 60.0
    lock = None
    try:
        lock = _acquire_compile_slot(env.settings, poll)
        started = time.time()   # after the wait for a slot
        try:
            code, output = _watch_compile(command, cwd, stall, longest, poll)
        except OSError as exc:
            code, output = 127, "the compile check could not start: %s" % exc
    finally:
        if lock:
            _release_compile_slot(lock, poll)
    result = {"ok": code == 0, "exit": code, "commit": commit, "time": db.now(), "seconds": round(time.time() - started, 1), "command": command}
    if code == 125:
        result["stalled"] = True
    runtime.kv_set(env.conn, env.name, key or COMPILE_KEY + queen["name"], json.dumps(result))
    last = next((" ".join(line.split()) for line in reversed(output.splitlines()) if line.strip()), "")
    live = "%s\t%s Compile test - Exit %s%s\n" % ("Green" if result["ok"] else "Red", localtime.time(datetime.now()), code, (": " + last[:140]) if last else "")
    try:
        if not milestones:
            raise OSError
        folder = env.run_dir(queen["name"])
        folder.mkdir(parents=True, exist_ok=True)
        with open(str(folder / "milestones.txt"), "ab") as handle:
            handle.write(live.encode("utf-8"))
    except OSError:
        pass
    return {**result, "output": "\n".join(output.strip().splitlines()[-40:])[-4000:]}


# The end of the Queen's start text.
def start_text(env, plan, provider_name):
    """What the Queen does at the end: she does not integrate. HaiveQueen's Orchestrator merges, checks, builds, pushes and deploys after her `bzz ready`."""
    text = ("When every requirement is done, merge the branches of your Agents into the plan branch, write the completeness answer, call bzz ready --project %s --plan %s and end your run. "
            "Do nothing after that: the Orchestrator of HaiveQueen takes over the plan after an accepted bzz ready." % (env.name, plan))
    text += " If only open owner questions remain, write a handoff and end your final report with WAITING_FOR_OWNER."
    return text


# The project integration configuration a Queen gets.
def configuration(env, plan):
    """Branch, remote and worktree location of the project (the Orchestrator runs the commands; the Queen gets none of them)."""
    s = env.settings
    return {"integrationBranch": str(s.get("integrationBranch") or "main"), "remoteName": str(s.get("remoteName") or "origin"),
            "worktreeLocation": str(s.get("worktreeLocation") or "beside the repository")}


# The owner topics in the Queen's start text.
def owner_topics_text(env):
    """The sentence with the topics that always go to the owner; empty without topics."""
    topics = [str(t).strip() for t in (env.settings.get("ownerTopics") or []) if str(t).strip()]
    if not topics:
        return ""
    return ("Questions on these topics of this Project always go to the owner with bzz ask, even a technical question you could decide yourself; this wins over your own triage: "
            + json.dumps(topics, ensure_ascii=False))

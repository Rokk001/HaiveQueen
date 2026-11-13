"""The Orchestrator pipeline: after an accepted `bzz ready` HaiveQueen itself (no language model) integrates the plan in the plan's worktree.

Each plan runs Merge, Check and Build speculatively on the cumulative state of its predecessors. Push and Deploy wait for those predecessors to close.
Steps, one after another within a plan: Merge, Check (the project's compile check command), Build
(Build command), Push (the plan branch HEAD to the integration branch, never forced), Deploy (Deploy command) and Close (queue row integrated, release,
integration place released, worktree cleanup). A step whose command is empty is skipped. A red step starts a fix agent (phase "fix") that works in the
plan's worktree; after its fix the pipeline starts again at step 1. The state of a plan is stored in the kv table (key orch:<plan>), so the pipeline
goes on after a pause, a fix, an owner answer and a restart of HaiveQueen.

Check runs in a detached supervisor and publishes its result in the database. Merge, Build and Deploy run in threads and store their results in files. Push and the fix agent start run in the round itself.
"""
import json
import os
import re
import sqlite3
import subprocess
import threading
import time
import uuid
from pathlib import Path

from . import agent_process, buildjobs, db, hives, integration, platform as osplat, roles, runtime, scheduler as sch
from . import plans as plan_mod

KEY = "orch:"              # + plan: the state of the pipeline of a plan while the Orchestrator integrates it
LAST_KEY = "orch-last:"    # + plan: what the last pipeline did (commit, steps, last fix agent, push and deploy done for a commit)
ATTEMPTS_KEY = "orch-fix:"  # + plan: {cls, n}: the fix attempts of the plan in the current class (the escalation as for Agents)
STEPS = ("Merge", "Check", "Build", "Push", "Deploy")   # the chain shown to the owner; Close follows the last one
COMMAND_STEPS = ("Check", "Build", "Deploy")
SETTING = {"Check": "compileCheck", "Build": "buildCommand", "Deploy": "deployCommand"}
MOVED_LIMIT = 3            # how often a moved remote sends the pipeline back to step 1 before a fix agent looks at it
ASYNC = None               # None: commands run in a thread unless BZZ_TEST_NOSTART is set (tests run them inline)
POLL = 0.2
JOBS = {}                  # (project, plan) -> Job: the commands that run now in this process


class Job:
    """One running pipeline command."""

    def __init__(self, token):
        self.token = token
        self.pid = None
        self.cancelled = False
        self.thread = None
        self.compile_job = None

    def alive(self):
        """True while the thread of the command runs."""
        if self.compile_job:
            proc = integration.SUPERVISORS.get(self.token)
            if proc is not None:
                return proc.poll() is None
            return osplat.process_alive(self.pid)
        return bool(self.thread and self.thread.is_alive())


# ---------------------------------------------------------------- state

def load(conn, project, plan):
    """The stored pipeline state of the plan, or None."""
    raw = runtime.kv_get(conn, project, KEY + plan)
    try:
        value = json.loads(raw) if raw else None
    except ValueError:
        return None
    return value if isinstance(value, dict) else None


def save(conn, project, plan, state):
    """Store the pipeline state of the plan."""
    runtime.kv_set(conn, project, KEY + plan, json.dumps(state))


def active(conn, project, plan):
    """True while the Orchestrator holds the plan (pipeline, fix agent or waiting for the owner)."""
    return load(conn, project, plan) is not None


def last_info(conn, project, plan):
    """What the last pipeline of the plan did: {commit, steps, fix_agent, pushed, deployed} (empty values when unknown)."""
    raw = runtime.kv_get(conn, project, LAST_KEY + plan)
    try:
        value = json.loads(raw) if raw else {}
    except ValueError:
        value = {}
    return value if isinstance(value, dict) else {}


def _save_last(conn, project, plan, **fields):
    info = last_info(conn, project, plan)
    info.update(fields)
    runtime.kv_set(conn, project, LAST_KEY + plan, json.dumps(info))


def commands(env):
    """{Check, Build, Deploy}: the command line of each step ('' = the step is skipped)."""
    from . import projsettings
    settings = projsettings.working_settings(env.settings)
    return {step: str(settings.get(key) or "").strip() if settings[step.lower() + "Enabled"] else ""
            for step, key in SETTING.items()}


def chain(env):
    """The steps that run for this project, in order (a step without a command is not part of the chain)."""
    cmds = commands(env)
    return [step for step in STEPS if step not in COMMAND_STEPS or cmds[step]]


def _short(plan):
    return Path(plan).stem


def _log(env, text):
    """One line of the log, tagged Orch."""
    runtime.append_event(env.conn, env.name, text, kind="orch", agent="Orchestrator", mirror="events", data={"tag": "Orch."})


def _counter(env, step):
    steps = chain(env)
    return "(%d/%d)" % (steps.index(step) + 1, len(steps)) if step in steps else ""


def _set_status(conn, env, plan, status):
    """Move the plan to the status while it runs under the Orchestrator; a paused, cancelled or finished plan keeps its status."""
    row = conn.execute("SELECT status FROM plans WHERE project=? AND path=?", (env.name, plan)).fetchone()
    if row and row[0] in ("QUEUED", "RUNNING", "WAITING"):
        hives._move_status(conn, env, plan, status)


def _blocker(conn, env, plan, text):
    runtime.kv_set(conn, env.name, "hive-blocker:" + plan, text)


def _hive_state(conn, project, plan, state):
    conn.execute("UPDATE hives SET state=? WHERE project=? AND plan_path=?", (state, project, plan))


# ---------------------------------------------------------------- git

def _git(cwd, *args, timeout=600, job=None):
    """(exit code, output) of a git command in the folder."""
    try:
        argv = ["git", "-C", str(cwd)] + list(args)
        if job is not None:
            if job.cancelled:
                return 143, "Merge cancelled"
            kwargs = {} if osplat.is_windows() else {"start_new_session": True}
            with subprocess.Popen(argv, stdout=subprocess.PIPE, stderr=subprocess.PIPE,
                                  text=True, encoding="utf-8", errors="replace", **kwargs) as proc:
                job.pid = proc.pid
                # Cancellation can arrive between the check above and process creation.
                if job.cancelled:
                    osplat.kill_process_tree(proc.pid)
                try:
                    out, err = proc.communicate(timeout=timeout)
                except subprocess.TimeoutExpired:
                    osplat.kill_process_tree(proc.pid)
                    proc.communicate()
                    raise
                finally:
                    job.pid = None
                return proc.returncode, (out + err).strip()
        done = subprocess.run(argv, capture_output=True, text=True, encoding="utf-8", errors="replace", timeout=timeout)
    except (OSError, subprocess.SubprocessError) as exc:
        return 127, "git could not run: %s" % exc
    return done.returncode, (done.stdout + done.stderr).strip()


def _remote(env):
    return str(env.settings.get("remoteName") or "origin"), str(env.settings.get("integrationBranch") or "main")


def _worktree(env, plan):
    """The plan's worktree folder, prepared again when it is missing; None when the header names none."""
    named = __import__("haivequeen.bees", fromlist=["x"]).plan_worktree(env, plan)
    if not named:
        return None
    folder = Path(named[1])
    if not folder.is_dir():
        from . import bees
        try:
            prepared = bees.prepare_queen_worktree(env, plan)
        except (OSError, ValueError):
            prepared = None
        return Path(prepared) if prepared else None
    return folder


# ---------------------------------------------------------------- entry points of the round

def run_round(conn, env, free_mb, alive=None):
    """One step of the pipeline of every plan of the project; returns the plans that were closed in this round."""
    closed = []
    integration.retire_legacy_steps(conn)   # step packages and capability questions of the time before the Orchestrator block no plan
    ready = dict(conn.execute(
        "SELECT h.plan_path, q.id FROM hives h JOIN queue q ON q.id=h.ready_id "
        "WHERE h.project=? AND q.project=h.project AND q.plan_path=h.plan_path AND q.status='ready'",
        (env.name,),
    ).fetchall())
    plans = runtime.list_plans(conn, env.name)
    plans.sort(key=lambda plan: (0, ready[plan["path"]]) if plan["path"] in ready else (1, 0))
    for plan in plans:
        try:
            if advance(conn, env, plan["path"], free_mb, alive) == "closed":
                closed.append(plan["path"])
        except Exception as exc:   # a broken plan must not hold back the others
            env.log("Orchestrator: plan %s: %s" % (plan["path"], exc))
    return closed


def _plan_status(conn, project, plan):
    row = conn.execute("SELECT status FROM plans WHERE project=? AND path=?", (project, plan)).fetchone()
    return row[0] if row else ""


def _live_agents(conn, project, plan, alive=None, phases=("queen", "bee", "fix")):
    return [a for a in runtime.list_agents(conn, project, "active")
            if a.get("plan_path") == plan and a.get("phase") in phases and agent_process.counts(a, alive)]


def takeover_due(conn, env, plan, hive, alive=None):
    """True when the plan's Queen has called `bzz ready` (accepted), has ended and the queue row of the plan still waits: the Orchestrator takes over."""
    if _plan_status(conn, env.name, plan) not in ("QUEUED", "RUNNING", "WAITING"):
        return False
    row = conn.execute("SELECT * FROM queue WHERE project=? AND plan_path=? ORDER BY id DESC LIMIT 1", (env.name, plan)).fetchone()
    if not row or row["status"] != "ready" or not hive.get("ready_id") or hive["ready_id"] != row["id"]:
        return False
    if runtime.kv_get(conn, env.name, "hive-rejected-ready:" + plan) == str(row["id"]):
        return False
    return not _live_agents(conn, env.name, plan, alive)


def advance(conn, env, plan, free_mb, alive=None):
    """Move the pipeline of one plan on by what can be done now; returns 'closed' when the plan was closed in this call."""
    hive = hives.get(conn, env.name, plan)
    if not hive or hive["state"] == "done":
        return None
    state = load(conn, env.name, plan)
    if state is None:
        if not takeover_due(conn, env, plan, hive, alive):
            return None
        if __import__("haivequeen.bees", fromlist=["x"]).plan_worktree(env, plan) is None:
            _blocker(conn, env, plan, "the plan header names no branch and worktree: the Orchestrator cannot integrate it")
            return None
        state = _new_state(conn, env, plan, hive)
        _save_last(conn, env.name, plan, fix_agent="", steps=[], commit="")
        _log(env, "%s: Queen done, integration taken over" % _short(plan))
    if state.get("job", {}).get("cancelling"):
        if not _cancel_pipeline_job(conn, env.name, plan, state):
            return None
    if _plan_status(conn, env.name, plan) in ("PAUSED", "CANCELLED", "DONE", "DRAFT"):
        _harvest_only(conn, env, plan, state)   # a running command ends; nothing new starts
        return None
    result = None
    for _ in range(4):   # a red step starts its fix agent, an ended fix agent lets the pipeline start again: in the same round
        before = (state["phase"], bool((state.get("fix") or {}).get("agent")))
        if state["phase"] == "fix":
            result = _advance_fix(conn, env, plan, state, free_mb, alive)
        elif state["phase"] == "blocked":
            result = _advance_blocked(conn, env, plan, state)
        else:
            result = _advance_pipeline(conn, env, plan, state, free_mb)
        if result == "closed":
            return result
        state = load(conn, env.name, plan)
        if state is None or (state["phase"], bool((state.get("fix") or {}).get("agent"))) == before:
            break
    return result


def _new_state(conn, env, plan, hive, fix=None):
    last = last_info(conn, env.name, plan)
    state = {"phase": "fix" if fix else "pipeline", "step": "Merge", "placed": False, "ran": [], "steps": {}, "moved": 0,
             "pushed": last.get("pushed", ""), "deployed": last.get("deployed", ""), "fix": fix, "started": db.now()}
    save(conn, env.name, plan, state)
    _hive_state(conn, env.name, plan, "integrating")
    return state


def begin_owner_fix(conn, project, plan, text):
    """The owner rejected the integrated plan: the Orchestrator starts a fix agent with his answer, then the pipeline from step 1 (no Queen starts)."""
    if load(conn, project, plan) is not None:
        return
    last = last_info(conn, project, plan)
    state = {"phase": "fix", "step": "Merge", "placed": False, "ran": [], "steps": {}, "moved": 0, "pushed": last.get("pushed", ""),
             "deployed": last.get("deployed", ""), "started": db.now(),
             "fix": {"agent": None, "reason": "owner", "step": "Owner check", "error": str(text or "")}}
    save(conn, project, plan, state)
    _hive_state(conn, project, plan, "integrating")


# ---------------------------------------------------------------- the pipeline

def _ahead(conn, project, plan):
    """Reservations before this plan, including legacy exclusive integrations."""
    return [row[0] for row in conn.execute(
        "SELECT plan_path FROM hive_integration WHERE project=? AND id < "
        "(SELECT id FROM hive_integration WHERE project=? AND plan_path=?) ORDER BY id",
        (project, project, plan))]


def _pending_before(conn, env, plan, state):
    """Do not overtake accepted ready plans whose reservation is not prepared yet."""
    hive = hives.get(conn, env.name, plan)
    ready_id = (hive or {}).get("ready_id") or 0
    rows = conn.execute(
        "SELECT h.plan_path, q.id FROM hives h JOIN queue q ON q.id=h.ready_id "
        "WHERE h.project=? AND q.status='ready' AND h.plan_path!=? AND NOT EXISTS "
        "(SELECT 1 FROM hive_integration i WHERE i.project=h.project AND i.plan_path=h.plan_path)",
        (env.name, plan)).fetchall()
    for other, ticket in rows:
        previous = load(conn, env.name, other)
        if previous and (previous.get("phase") != "pipeline" or previous.get("rejoining")):
            continue
        if not state.get("rejoining") and ticket >= ready_id:
            continue
        if _plan_status(conn, env.name, other) not in ("QUEUED", "RUNNING", "WAITING"):
            continue
        if runtime.kv_get(conn, env.name, "hive-rejected-ready:" + other) == str(ticket):
            continue
        return True
    return False


def _replay_followers(conn, env, plan):
    """Invalidate speculative results durably before asking their workers to stop."""
    rows = conn.execute("SELECT plan_path FROM hive_integration WHERE project=? AND id > "
                        "(SELECT id FROM hive_integration WHERE project=? AND plan_path=?) ORDER BY id",
                        (env.name, env.name, plan)).fetchall()
    for row in rows:
        follower = load(conn, env.name, row[0])
        if not follower or follower.get("phase") != "pipeline" or follower.get("step") in ("Deploy", "Close"):
            continue
        follower.update(replay=True, step="Merge", steps={}, ran=[])
        save(conn, env.name, row[0], follower)
        _cancel_pipeline_job(conn, env.name, row[0], follower)


def _advance_pipeline(conn, env, plan, state, free_mb):
    folder = _worktree(env, plan)
    if folder is None:
        _blocker(conn, env, plan, "the worktree of the plan is missing and could not be prepared")
        _set_status(conn, env, plan, "WAITING")
        return None
    if not state["placed"]:
        from . import projsettings
        reason = projsettings.remote_wait_reason(env.settings)
        if reason:
            _blocker(conn, env, plan, "waiting for integration: " + reason)
            _set_status(conn, env, plan, "WAITING")
            return None
        # Reserve in ready order, but let speculative work proceed behind the head.
        if _pending_before(conn, env, plan, state):
            _blocker(conn, env, plan, "waiting for an earlier ready plan to join the queue")
            _set_status(conn, env, plan, "WAITING")
            return None
        hives.take_integration(conn, env.name, plan)
        state["placed"] = True
        state.pop("rejoining", None)
        save(conn, env.name, plan, state)
    if state.get("replay"):
        if not _cancel_pipeline_job(conn, env.name, plan, state):
            return None
        if state.get("source"):
            code, out = _git(folder, "reset", "--hard", state["source"])
            if code:
                return _red(conn, env, plan, state, "Merge", "could not restore the plan before replay:\n" + out)
        state.pop("replay", None)
        state.pop("merged", None)
        save(conn, env.name, plan, state)
    _blocker(conn, env, plan, "")
    _set_status(conn, env, plan, "RUNNING")
    for _ in range(4 * (len(STEPS) + 2)):   # inline steps follow each other in one round (also after a moved remote); a running command ends the round
        step = state["step"]
        ahead = _ahead(conn, env.name, plan)
        if step in ("Push", "Deploy", "Close") and ahead:
            # Waiting through Close also serializes Deploy and release publication.
            _blocker(conn, env, plan, "waiting for earlier plans to push and deploy")
            _set_status(conn, env, plan, "WAITING")
            return None
        if step == "Merge":
            outcome = _merge(conn, env, plan, state, folder)
        elif step == "Push":
            outcome = _push(conn, env, plan, state, folder)
        elif step in COMMAND_STEPS:
            outcome = _command(conn, env, plan, state, folder, step, free_mb)
        else:
            return "closed" if _close(conn, env, plan, state, folder) else None
        if outcome != "next":
            return None
    return None


def _next_step(env, state, step):
    """The step after `step` in the chain of the project (Close after the last one); steps without a command are skipped."""
    steps = chain(env)
    state["step"] = steps[steps.index(step) + 1] if step in steps and steps.index(step) + 1 < len(steps) else "Close"
    return state["step"]


def _done(conn, env, plan, state, step, text=""):
    state["steps"][step] = "done"
    if step not in state["ran"]:
        state["ran"].append(step)
    _log(env, "%s: %s done %s%s" % (_short(plan), step, _counter(env, step), (" - " + text) if text else ""))
    _next_step(env, state, step)
    save(conn, env.name, plan, state)
    return "next"


def _merge(conn, env, plan, state, folder):
    key = (env.name, plan)
    if state.get("job"):
        result = _harvest(conn, env, plan, state)
        if result is None:
            return None
        state.pop("job", None)
        JOBS.pop(key, None)
        if result == "lost":
            # A lost worker may have left a partially applied merge behind.
            state["replay"] = True
            save(conn, env.name, plan, state)
            return None
        code, out = result
        if code:
            return _red(conn, env, plan, state, "Merge", out)
        state["merged"] = integration.head_commit(folder)
        return _done(conn, env, plan, state, "Merge")
    remote, branch = _remote(env)
    ahead = _ahead(conn, env.name, plan)
    target = "%s/%s" % (remote, branch)
    if ahead:
        predecessor = load(conn, env.name, ahead[-1])
        if not predecessor or predecessor.get("replay") or not predecessor.get("merged"):
            _blocker(conn, env, plan, "waiting for the preceding plan to merge")
            _set_status(conn, env, plan, "WAITING")
            return None
        target = predecessor["merged"]
    if not state.get("source"):
        state["source"] = integration.head_commit(folder)
    state["steps"]["Merge"] = "running"
    job = Job(uuid.uuid4().hex)
    result_file = _result_file(env.name, plan, job.token)
    JOBS[key] = job
    state["job"] = {"step": "Merge", "token": job.token, "file": str(result_file), "pid": None}
    save(conn, env.name, plan, state)
    _log(env, "%s: Merge started %s" % (_short(plan), _counter(env, "Merge")))
    job.thread = threading.Thread(target=_merge_work, args=(job, folder, remote, branch, target, result_file), daemon=False)
    job.thread.start()
    if not _async():
        job.thread.join()
        return _merge(conn, env, plan, state, folder)
    if job.pid:
        state["job"]["pid"] = job.pid
        save(conn, env.name, plan, state)
    return None


def _merge_work(job, folder, remote, branch, target, result_file):
    """Run cancellable Git commands without holding up other plans in the round."""
    code, out = _merge_git(job, folder, remote, branch, target)
    Path(result_file).write_text(json.dumps({"token": job.token, "code": code, "output": out}), encoding="utf-8")


def _merge_git(job, folder, remote, branch, target):
    code, out = _git(folder, "fetch", remote, branch, job=job)
    if code != 0:
        return code, "git fetch %s %s failed:\n%s" % (remote, branch, out)
    if _git(folder, "rev-parse", "-q", "--verify", "MERGE_HEAD", job=job)[0] == 0:
        return 1, "an earlier merge is not finished in the worktree of the plan (merge conflict):\n" + _git(folder, "status", "--short", job=job)[1]
    code, out = _git(folder, "merge", "--no-edit", target, job=job)
    if code != 0:
        return code, "merge of %s/%s into the plan branch failed (merge conflict; the merge is left open in the worktree):\n%s" % (remote, branch, out)
    return 0, out


def _push(conn, env, plan, state, folder):
    remote, branch = _remote(env)
    head = integration.head_commit(folder)
    if not head:
        return _red(conn, env, plan, state, "Push", "the commit of the plan worktree cannot be read")
    if state.get("pushed") == head:
        return _done(conn, env, plan, state, "Push", "already pushed")
    state["steps"]["Push"] = "running"
    save(conn, env.name, plan, state)
    _log(env, "%s: Push started %s" % (_short(plan), _counter(env, "Push")))
    code, out = _git(folder, "fetch", remote, branch)
    if code != 0:
        return _red(conn, env, plan, state, "Push", "git fetch %s %s failed:\n%s" % (remote, branch, out))
    if _git(folder, "merge-base", "--is-ancestor", "refs/remotes/%s/%s" % (remote, branch), "HEAD")[0] != 0:
        return _moved(conn, env, plan, state, "Push")
    code, out = _git(folder, "push", remote, "HEAD:refs/heads/%s" % branch)   # a normal push, never forced
    if code != 0:
        _git(folder, "fetch", remote, branch)
        if _git(folder, "merge-base", "--is-ancestor", "refs/remotes/%s/%s" % (remote, branch), "HEAD")[0] != 0:
            return _moved(conn, env, plan, state, "Push")
        return _red(conn, env, plan, state, "Push", "git push %s HEAD:%s failed:\n%s" % (remote, branch, out))
    state["pushed"] = head
    _save_last(conn, env.name, plan, pushed=head)
    return _done(conn, env, plan, state, "Push")


def _moved(conn, env, plan, state, step):
    """The remote integration branch moved after the merge: the plan is integrated onto its current state, from step 1 again."""
    state["moved"] = int(state.get("moved", 0)) + 1
    if state["moved"] > MOVED_LIMIT:
        return _red(conn, env, plan, state, step, "the remote integration branch moved %d times while the plan was integrated" % state["moved"])
    _log(env, "%s: the remote integration branch moved, integration starts again at Merge" % _short(plan))
    _replay_followers(conn, env, plan)
    state["step"] = "Merge"
    state["steps"] = {}
    state["ran"] = []
    state.pop("merged", None)
    save(conn, env.name, plan, state)
    return "next"


def _work(env_settings, job, step, command, folder, extra_env, result_file):
    """The thread of one command: run it (the Check in a slot of the machine), store the result in a file."""
    if step == "Check":
        from types import SimpleNamespace
        from . import config
        conn = db.connect(config.db_path())
        try:
            env = SimpleNamespace(settings={**env_settings, "compileCheck": command}, conn=conn, name="orchestrator")
            result = integration.run_compile_check(env, {"name": job.token}, cwd=str(folder), poll=POLL, milestones=False)
            Path(result_file).write_text(json.dumps({"token": job.token, "code": result["exit"], "output": result["output"]}), encoding="utf-8")
        finally:
            conn.close()
        return
    poll = POLL
    stall = float(env_settings.get("compileStallMinutes", 10)) * 60.0
    longest = float(env_settings.get("compileMaxMinutes", 120)) * 60.0 if step == "Check" else float("inf")   # Build and Deploy have no fixed overall limit
    lock = None
    try:
        if job.cancelled:
            code, output = 143, "stopped before the command started"
        else:
            try:
                code, output = integration._watch_compile(command, str(folder), stall, longest, poll, env=extra_env, label=step.lower() + " command",
                                                          on_start=lambda pid: setattr(job, "pid", pid))
            except OSError as exc:
                code, output = 127, "the %s command could not start: %s" % (step, exc)
    finally:
        if lock:
            integration._release_compile_slot(lock, poll)
    if job.cancelled:
        code = code or 143
    try:
        Path(result_file).write_text(json.dumps({"token": job.token, "code": code, "output": output[-6000:]}), encoding="utf-8")
    except OSError:
        pass


def _result_file(project, plan, token):
    folder = osplat.data_dir() / "orchestrator" / re.sub(r"[^\w.-]", "_", project)
    folder.mkdir(parents=True, exist_ok=True)
    return folder / ("%s-%s.json" % (re.sub(r"[^\w.-]", "_", plan)[-80:], token))


def _async():
    return ASYNC if ASYNC is not None else not os.environ.get("BZZ_TEST_NOSTART")


def _command(conn, env, plan, state, folder, step, free_mb):
    command = commands(env)[step]
    if not command:
        return _done(conn, env, plan, state, step, "skipped")
    job = state.get("job")
    key = (env.name, plan)
    if job and job["step"] != step:
        state.pop("job", None)
        job = None
    if job is None:
        head = integration.head_commit(folder)
        if step == "Deploy" and head and state.get("deployed") == head:
            return _done(conn, env, plan, state, step, "already deployed")
        with buildjobs.reservation_guard():
            extra = planned = None
            if step in ("Build", "Deploy"):
                bj = buildjobs.settings_from(env.settings.get("buildJobs"))
                low = buildjobs.waiting_for_ram(bj, free_mb)
                if low:   # below the minimum free RAM before a build or deploy: wait, the next round asks again
                    _blocker(conn, env, plan, "waiting for the %s: %s" % (step.lower(), low))
                    _set_status(conn, env, plan, "WAITING")
                    return None
                planned = buildjobs.plan_build(env, free_mb=free_mb) if bj is not None else None
                if planned is not None and planned["Jobs"] == 0:
                    _blocker(conn, env, plan, "waiting for the %s: waiting for build cores" % step.lower())
                    _set_status(conn, env, plan, "WAITING")
                    return None
                extra = planned["Environment"] if planned else None
            token = uuid.uuid4().hex
            result_file = _result_file(env.name, plan, token)
            j = Job(token)
            JOBS[key] = j
            state["job"] = {"step": step, "token": token, "file": str(result_file), "pid": None, "head": head}
            if planned:
                state['job'].update(owner_pid=os.getpid(), buildJobs=planned['Jobs'], buildParallel=planned['Parallel'])
            state["steps"][step] = "running"
            save(conn, env.name, plan, state)
        _log(env, "%s: %s started %s" % (_short(plan), step, _counter(env, step)))
        if step == "Check":
            compile_job = integration.start_compile_check(env, {"name": token}, cwd=str(folder), key="orch-check:" + plan, poll=POLL, milestones=False)
            j.token = compile_job["token"]
            j.pid = compile_job["pid"]
            j.compile_job = compile_job
            state["job"].update(compile_job)
            state["job"]["compile"] = True
            save(conn, env.name, plan, state)
            if not _async():
                while integration.poll_compile_check(conn, env.name, compile_job) is None:
                    time.sleep(POLL)
        else:
            j.thread = threading.Thread(target=_work, args=(env.settings, j, step, command, folder, extra, result_file), daemon=False)
            j.thread.start()
            if not _async():
                j.thread.join()
    result = _harvest(conn, env, plan, state)
    if result is None:
        return None
    if result == "lost":   # the process of the command is gone without a result (HaiveQueen ended): run the step again
        state.pop("job", None)
        save(conn, env.name, plan, state)
        return "next"
    code, output = result
    state.pop("job", None)
    JOBS.pop(key, None)
    if code != 0:
        return _red(conn, env, plan, state, step, "the %s command (exit %s) failed:\n%s" % (step, code, output))
    if step == "Deploy":
        head = integration.head_commit(folder)
        state["deployed"] = head
        _save_last(conn, env.name, plan, deployed=head)
    return _done(conn, env, plan, state, step)


def _harvest(conn, env, plan, state):
    """None while the command runs, 'lost' when it vanished, else (exit code, output) read from the result file."""
    job = state.get("job")
    if not job:
        return None
    if job.get("compile"):
        result = integration.poll_compile_check(conn, env.name, job)
        if result is None:
            return None
        JOBS.pop((env.name, plan), None)
        return result["exit"], result.get("output", "")
    key = (env.name, plan)
    j = JOBS.get(key)
    if j and j.token == job["token"]:
        if j.pid != job.get("pid"):
            job["pid"] = j.pid
            save(conn, env.name, plan, state)
        if j.alive():
            return None
    try:
        data = json.loads(Path(job["file"]).read_text(encoding="utf-8"))
    except (OSError, ValueError):
        data = None
    if data and data.get("token") == job["token"]:
        try:
            Path(job["file"]).unlink()
        except OSError:
            pass
        JOBS.pop(key, None)
        return int(data["code"]), str(data.get("output") or "")
    if j and j.token == job["token"]:
        JOBS.pop(key, None)
        return 1, "the command ended without a result"
    pid = job.get("pid")
    if pid and osplat.process_alive(pid):
        return None   # a command of an earlier run of HaiveQueen still runs: wait for it, then run the step again
    return "lost"


def _harvest_only(conn, env, plan, state):
    """A paused plan: take over the result of a command that has ended, start nothing."""
    if state.get("phase") != "pipeline" or not state.get("job"):
        return
    result = _harvest(conn, env, plan, state)
    if result is None:
        return
    job = state.pop("job")
    step = job["step"]
    if result == "lost" or result[0] != 0:
        state["steps"].pop(step, None)   # the step runs again at the resume
    else:
        state["steps"][step] = "done"
        state["ran"].append(step)
        if step == "Deploy":
            state["deployed"] = job.get("head") or state.get("deployed", "")
        if step == "Merge":
            state["merged"] = integration.head_commit(_worktree(env, plan))
        _next_step(env, state, step)
    save(conn, env.name, plan, state)


def _red(conn, env, plan, state, step, error):
    """A step is red: the place is released and a fix agent starts."""
    if step != "Deploy":
        _replay_followers(conn, env, plan)
    state["steps"][step] = "red"
    _log(env, "%s: %s red %s - %s" % (_short(plan), step, _counter(env, step), " ".join(error.split())[:200]))
    state["phase"] = "fix"
    state["fix"] = {"agent": None, "reason": "step", "step": step, "error": error[-4000:]}
    state["placed"] = False
    save(conn, env.name, plan, state)
    hives.release_integration(conn, env.name, plan)
    return "red"


def _close(conn, env, plan, state, folder):
    head = integration.head_commit(folder)
    row = conn.execute("SELECT * FROM queue WHERE project=? AND plan_path=? ORDER BY id DESC LIMIT 1", (env.name, plan)).fetchone()
    branch = row["branch"] if row else (__import__("haivequeen.bees", fromlist=["x"]).plan_worktree(env, plan) or ("", ""))[0]
    if row is None:
        db.add_ready(conn, env.name, branch, head, plan, "integrated by the Orchestrator")
    runtime.queue_upsert(conn, env.name, branch, tip=head, plan=plan, status="integrated", note="integrated by the Orchestrator")
    newest = conn.execute("SELECT id FROM queue WHERE project=? AND plan_path=? ORDER BY id DESC LIMIT 1", (env.name, plan)).fetchone()
    if newest:
        conn.execute("UPDATE hives SET ready_id=? WHERE project=? AND plan_path=?", (newest[0], env.name, plan))
    state["steps"]["Close"] = "done"
    try:
        from . import publish
        publish.after_integration(conn, env, plan)   # HaiveQueen publishes the release itself; a failure is retried by the loop
    except Exception as exc:
        db.add_event(conn, env.name, "release", "release step failed: %s" % exc)
    hives.release_integration(conn, env.name, plan)
    # the worktree cleanup runs at the start of the next round (it takes the scheduling lock, which this round holds)
    bees = __import__("haivequeen.bees", fromlist=["x"])
    for package in bees.rejected_packages(conn, env.name, plan):   # the packages the owner rejected count as done again
        bees.set_package_state(conn, env.name, plan, package, "done", "Orchestrator")
    _save_last(conn, env.name, plan, commit=head, steps=list(state["ran"]))
    _log(env, "%s: integration done, commit %s" % (_short(plan), head[:7]))
    runtime.kv_set(conn, env.name, KEY + plan, "")
    runtime.kv_set(conn, env.name, "hive-blocker:" + plan, "")
    _hive_state(conn, env.name, plan, "waiting")
    return True


# ---------------------------------------------------------------- fix agent

def _fix_prompt(env, plan, state, owner_text=""):
    fix = state["fix"]
    folder = _worktree(env, plan)
    try:
        plan_text = (env.workspace / plan).read_text(encoding="utf-8", errors="replace")[:60000]
    except OSError:
        plan_text = ""
    remote, branch = _remote(env)
    if fix["reason"] == "owner":
        what = "The owner checked the integrated result of this plan and rejected it. His answer:\n%s\n" % fix["error"]
    else:
        what = "The integration pipeline of this plan failed at the step %s. The error:\n%s\n" % (fix["step"], fix["error"])
    if owner_text:
        what += "\nInstruction of the owner:\n%s\n" % owner_text
    text = ("You are the fix agent of the plan %s. Work only in the plan worktree %s on the plan branch.\n%s\n"
            "Fix exactly this. On a merge conflict keep the content of both sides (the merge of %s/%s is left open in the worktree: resolve it and commit it). "
            "Commit your fix and end. Do not push, do not merge into the integration branch, do not call bzz ready and do not change the queue: the Orchestrator of HaiveQueen "
            "starts the integration again at the first step after your fix. Write your final report as the answer of your last message.\n\nTHE PLAN\n\n%s"
            % (plan, folder, what, remote, branch, plan_text))
    return text


def _advance_fix(conn, env, plan, state, free_mb, alive):
    fix = state["fix"]
    if fix.get("agent"):
        row = next((a for a in runtime.list_agents(conn, env.name, "all") if a.get("name") == fix["agent"] and a.get("phase") == "fix"), None)
        if row is not None and agent_process.counts(row, alive):
            _set_status(conn, env, plan, "RUNNING")
            return None
        _log(env, "%s: fix agent %s ended" % (_short(plan), fix["agent"]))
        _save_last(conn, env.name, plan, fix_agent=fix["agent"])
        if fix.get("owner_stopped"):   # M-4: an agent the owner stopped is never restarted; the plan waits for his instruction
            return _block(conn, env, plan, state, "stopped")
        state.update(phase="pipeline", step="Merge", placed=False, steps={}, ran=[], fix=None, moved=0, rejoining=True)
        # The fix commit becomes the new original state when joining the tail.
        state.pop("source", None)
        state.pop("merged", None)
        save(conn, env.name, plan, state)
        return None
    return _start_fix(conn, env, plan, state, free_mb, alive, str(fix.get("instruction") or ""))


def _start_fix(conn, env, plan, state, free_mb, alive, owner_text=""):
    from . import bees, loop
    fix = state["fix"]
    excluded = list(env.settings.get("excludedProviders") or [])
    threshold = int(env.settings.get("escalateAfter", 3))
    attempts = _attempts(conn, env, plan)
    cls = attempts.get("cls") or str(env.settings.get("queenClass", "medium"))
    n = int(attempts.get("n", 0))
    if n >= threshold:
        higher = bees.next_class(env.providers, cls, excluded)
        if higher:
            cls, n = higher, 0
        else:
            attempts.update(cls=cls, n=n)
            runtime.kv_set(conn, env.name, ATTEMPTS_KEY + plan, json.dumps(attempts))
            return _block(conn, env, plan, state, "escalation")
    if bees.drain_refusal():
        _blocker(conn, env, plan, "waiting to start the fix agent: HaiveQueen waits for an update")
        return None
    if free_mb / 1024.0 < loop._min_ram(env):
        _blocker(conn, env, plan, "waiting to start the fix agent: free RAM %.2f GB is below the minimum %.2f GB" % (free_mb / 1024.0, loop._min_ram(env)))
        _set_status(conn, env, plan, "WAITING")
        return None
    running = [{**a, "status": "running"} for a in runtime.list_agents(conn, None, "active") if agent_process.counts(a, alive)]
    free = sch.provider_free(env.providers, running, conn)
    providers = [{**p, "use": "preferred"} for p in env.providers if p["name"] not in excluded and "Coding" in sch.provider_capabilities(p) and not sch.reserve_zero(p, conn)]
    pick = sch.select_provider(providers, cls, [], free, conn=conn)
    if not pick:
        _blocker(conn, env, plan, "waiting to start the fix agent: no provider with a free slot offers class %s" % cls)
        _set_status(conn, env, plan, "WAITING")
        return None
    folder = _worktree(env, plan)
    if folder is None:
        _blocker(conn, env, plan, "the worktree of the plan is missing and could not be prepared")
        _set_status(conn, env, plan, "WAITING")
        return None
    existing = [a["name"] for a in runtime.list_agents(conn, env.name, "all")]
    name = loop.agent_identity(conn)["Person"]
    action = {"name": name, "type": pick["name"], "phase": "fix", "plan": plan, "package": "", "cwd": str(folder), "prompt": _fix_prompt(env, plan, state, owner_text), "class": cls}
    st = {"names": existing, "busy": [], "plan_status": {sch.norm_plan(plan): "RUNNING"}, "by_path": {sch.norm_plan(plan): {"path": plan}}}
    ctx = {"env": env, "state": st, "pool": loop.Pool(free, 0), "report": [], "dry": False, "settle": 0}
    if not loop._do_start(ctx, action, forced_pick=pick):
        return None
    started = st["names"][-1]
    fix["agent"] = started
    fix["head"] = integration.head_commit(folder)
    attempts.update(cls=cls, n=n + 1)
    runtime.kv_set(conn, env.name, ATTEMPTS_KEY + plan, json.dumps(attempts))
    state["fix"] = fix
    save(conn, env.name, plan, state)
    _blocker(conn, env, plan, "")
    _set_status(conn, env, plan, "RUNNING")
    _log(env, "%s: fix agent %s started (%s)" % (_short(plan), started, "Fix: " + (fix["step"] if fix["reason"] == "step" else "owner rejection")))
    return None


def _attempts(conn, env, plan):
    raw = runtime.kv_get(conn, env.name, ATTEMPTS_KEY + plan)
    try:
        value = json.loads(raw) if raw else {}
    except ValueError:
        value = {}
    return value if isinstance(value, dict) else {}


def _block(conn, env, plan, state, kind):
    """The plan waits for the owner: one question (item fix-escalation or fix-stopped) is filed; his answer lets the next fix agent start."""
    language = roles.language(env.settings)
    from .i18n import tr
    if kind == "stopped":
        item = "fix-stopped"
        text = tr("The fix agent of plan %s was stopped by you. It is not restarted automatically. What should happen next?", _short(plan), language=language)
    else:
        item = "fix-escalation"
        text = tr("The fix agents of plan %s could not fix the integration (step: %s) in any offered class. How should it go on?", _short(plan), state["fix"]["step"], language=language)
    qid = db.add_question(conn, env.name, plan, item, text, tr("Give an instruction for the next fix agent.", language=language))
    state["phase"] = "blocked"
    state["blocked"] = {"kind": kind, "question": qid}
    save(conn, env.name, plan, state)
    hives.release_integration(conn, env.name, plan)
    _blocker(conn, env, plan, "waiting for the answer of the owner (question %s)" % qid)
    _set_status(conn, env, plan, "WAITING")
    _log(env, "%s: fix agent %s, the owner is asked (question %s)" % (_short(plan), kind, qid))
    return None


def _advance_blocked(conn, env, plan, state):
    qid = state["blocked"]["question"]
    row = conn.execute("SELECT status, answer FROM questions WHERE id=?", (qid,)).fetchone()
    if row is None or row[0] != "answered":
        _set_status(conn, env, plan, "WAITING")
        return None
    state["phase"] = "fix"
    state["fix"]["agent"] = None
    state["fix"]["owner_stopped"] = False
    state["fix"]["instruction"] = str(row[1] or "")
    state.pop("blocked", None)
    attempts = _attempts(conn, env, plan)
    attempts["n"] = 0
    runtime.kv_set(conn, env.name, ATTEMPTS_KEY + plan, json.dumps(attempts))
    save(conn, env.name, plan, state)
    return None


# ---------------------------------------------------------------- stops

def owner_stop(conn, project, agent_name):
    """The owner stops a fix agent: it is never restarted automatically, its plan waits for an instruction of the owner (HQ-OWNER-MSG-FIX M-4)."""
    for plan in {a.get("plan_path") for a in runtime.list_agents(conn, project, "active") if a.get("name") == agent_name and a.get("phase") == "fix"}:
        state = load(conn, project, plan)
        if state and state.get("fix") and state["fix"].get("agent") == agent_name:
            state["fix"]["owner_stopped"] = True
            save(conn, project, plan, state)
            return True
    return False


def _cancel_pipeline_job(conn, project, plan, state, stop=None):
    """Keep the job recorded until its command and supervisor have actually stopped."""
    record = state.get("job")
    if not record:
        return True
    stop = stop or osplat.kill_process_tree
    record["cancelling"] = True
    job = JOBS.get((project, plan))
    if job and job.token != record.get("token"):
        job = None
    if job:
        job.cancelled = True
    pid = job.pid if job else record.get("pid")
    if pid:
        record["pid"] = pid
    if pid and record.get("cancel_pid") != pid:
        stop(pid)
        record["cancel_pid"] = pid
    save(conn, project, plan, state)
    if job and job.alive():
        return False
    if record.get("compile"):
        # A supervisor may publish before exiting, and recovery must also confirm
        # that an orphaned check has ended before the worktree can be reused.
        if osplat.process_alive(record["pid"]):
            return False
        if integration.poll_compile_check(conn, project, record) is None:
            return False
    elif pid and osplat.process_alive(pid):
        return False
    JOBS.pop((project, plan), None)
    state.pop("job", None)
    state["steps"] = {k: v for k, v in state["steps"].items() if v != "running"}
    save(conn, project, plan, state)
    return True


def stop_plan(conn, project, plan, stop=None):
    """Stopping the plan ends its running pipeline command and its fix agent; the pipeline goes on at the resume."""
    stop = stop or osplat.kill_process_tree
    state = load(conn, project, plan)
    if state is None:
        return
    _cancel_pipeline_job(conn, project, plan, state, stop)
    fix = state.get("fix")
    if state.get("phase") == "fix" and fix and fix.get("agent"):
        for row in runtime.list_agents(conn, project, "active"):
            if row.get("name") == fix["agent"] and row.get("phase") == "fix" and row.get("pid"):
                stop(row["pid"])
                runtime.upsert_agent(conn, project, row["name"], {"status": "done", "pid": None, "ended_at": db.now(), "last_line": "stopped with the plan"})
        fix["agent"] = None   # a stop of the plan is no stop of the owner at the agent: a fix agent starts again at the resume
        att = runtime.kv_get(conn, project, ATTEMPTS_KEY + plan)
        try:
            value = json.loads(att) if att else {}
        except ValueError:
            value = {}
        if value.get("n"):
            value["n"] = int(value["n"]) - 1   # this attempt was not the fix agent's failure
            runtime.kv_set(conn, project, ATTEMPTS_KEY + plan, json.dumps(value))
    state["placed"] = False
    save(conn, project, plan, state)
    hives.release_integration(conn, project, plan)


def running_jobs():
    """True while a pipeline command of this process runs (a stop of HaiveQueen waits for it)."""
    return any(j.alive() for j in list(JOBS.values()))


def holds_branch(env, branch):
    """True when the Orchestrator integrates a plan of this project whose plan branch is `branch` (its worktree stays while it works)."""
    from . import bees
    if getattr(env, "conn", None) is None:
        return False
    try:
        keys = [row[0] for row in env.conn.execute("SELECT key FROM kv WHERE project=? AND key LIKE ? AND value!=''", (env.name, KEY + "%"))]
    except sqlite3.Error:
        return False
    for key in keys:
        try:
            named = bees.plan_worktree(env, key[len(KEY):])
        except (OSError, ValueError):
            named = None
        if named and named[0] == branch:
            return True
    return False

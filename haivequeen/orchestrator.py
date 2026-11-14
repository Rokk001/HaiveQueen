"""The Orchestrator pipeline: after an accepted `bzz ready` HaiveQueen itself (no language model) integrates the plan in the plan's worktree.

Each plan runs Merge, Check and Build speculatively on the cumulative state of its predecessors. Push and Deploy wait for those predecessors to close.
Steps, one after another within a plan: Merge, Check (the project's compile check command), Build
(Build command), Push (the plan branch HEAD to the integration branch, never forced), Deploy (Deploy command) and Close (queue row integrated, release,
integration place released, worktree cleanup). A step whose command is empty is skipped. A red step starts a fix agent (phase "fix") that works in the
plan's worktree; after its fix the pipeline starts again at step 1. The state of a plan is stored in the kv table (key orch:<plan>), so the pipeline
goes on after a pause, a fix, an owner answer and a restart of HaiveQueen.

Check runs in a detached supervisor and publishes its result in the database. Merge, Build and Deploy run in threads and store their results in files. Push and the fix agent start run in the round itself.

Each step follows its Local or Git provider setting. Provider Merge pushes the plan branch and waits for an external PR merge; it never merges or enables auto-merge.
Provider command steps poll the exact commit's pipeline through the adapter. Their state survives restarts, and a failed pipeline enters the same fix-agent flow as a local failure.
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

from . import action_logs, agent_process, buildjobs, db, git_provider, hives, integration, platform as osplat, roles, runtime, scheduler as sch
from . import plans as plan_mod

KEY = "orch:"              # + plan: the state of the pipeline of a plan while the Orchestrator integrates it
LAST_KEY = "orch-last:"    # + plan: what the last pipeline did (commit, steps, last fix agent, push and deploy done for a commit)
ATTEMPTS_KEY = "orch-fix:"  # + plan: {cls, n}: the fix attempts of the plan in the current class (the escalation as for Agents)
STEPS = ("Merge", "Check", "Build", "Push", "Deploy", "Release")   # the chain shown to the owner; Close follows the last one
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
        self.identity = None
        self.started = threading.Event()
        self.cancelled = False
        self.thread = None
        self.compile_job = None
        self.log_file = None

    def record_pid(self, pid):
        self.identity = osplat.process_identity(pid) if pid else None
        self.pid = pid
        self.started.set()

    def alive(self):
        """True while the thread of the command runs."""
        if self.compile_job:
            proc = integration.SUPERVISORS.get(self.token)
            if proc is not None:
                return proc.poll() is None
            return agent_process.recorded_alive(self.pid, self.identity)
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


def save_step_log(conn, project, plan, step, code, output):
    """Keep the latest command result independently of disposable job files."""
    runtime.kv_set(conn, project, "orch-log:" + plan + ":" + step,
                   json.dumps({"step": step, "exit_code": code, "output": str(output)}))


def _start_log(conn, env, plan, state, step, command=None):
    path = action_logs.start(env.name, plan, step)
    assignment = __import__('haivequeen.bees', fromlist=['x']).plan_worktree(env, plan)
    folder = assignment[1] if assignment else None
    action_logs._write(path, {'event': 'execution', 'time': action_logs._now(),
                              'commit': integration.head_commit(folder) if folder else '',
                              'cwd': str(folder or ''), 'command': commands(env).get(step, step) if command is None else command,
                              'predecessors': _ahead(conn, env.name, plan)})
    state["log_file"] = path
    save(conn, env.name, plan, state)
    return path


def _finish_log(state, code, result=None, message=""):
    action_logs.finish(state.get("log_file"), code, result, message)


def commands(env):
    """Local command lines; disabled and provider steps have no local command."""
    from . import projsettings
    settings = projsettings.working_settings(env.settings)
    return {step: str(settings.get(key) or "").strip() if settings[step.lower() + "Enabled"] and settings[step.lower() + "Mode"] == "local" else ""
            for step, key in SETTING.items()}


def chain(env):
    """The steps that run for this project, in order (a step without a command is not part of the chain)."""
    from . import projsettings
    settings = projsettings.working_settings(env.settings)
    cmds = commands(env)
    return [step for step in STEPS if (settings["releaseEnabled"] if step == "Release" else
            step not in COMMAND_STEPS or cmds[step] or
            (settings[step.lower() + "Enabled"] and settings[step.lower() + "Mode"] == "git"))]


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
    return _git_run(cwd, *args, timeout=timeout, job=job)


def _git_run(cwd, *args, timeout=600, job=None):
    """(exit code, output) of a git command in the folder."""
    try:
        argv = ["git", "-C", str(cwd)] + list(args)
        if job is not None:
            if job.cancelled:
                action_logs.append(getattr(job, 'log_file', None), 'Merge cancelled')
                return 143, "Merge cancelled"
            kwargs = {} if osplat.is_windows() else {"start_new_session": True}
            with subprocess.Popen(argv, stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
                                  text=True, encoding="utf-8", errors="replace", **kwargs) as proc:
                job.record_pid(proc.pid)
                # Cancellation can arrive between the check above and process creation.
                if job.cancelled:
                    osplat.kill_process_tree(proc.pid)
                chunks, log_errors = [], []
                def drain():
                    for line in proc.stdout:
                        chunks.append(line)
                        try:
                            action_logs.append(getattr(job, 'log_file', None), line)
                        except OSError as exc:
                            # A broken journal must never stop draining the child's pipe.
                            if not log_errors:
                                log_errors.append(str(exc))
                reader = threading.Thread(target=drain, daemon=True)
                reader.start()
                code = None
                try:
                    code = proc.wait(timeout=timeout)
                except subprocess.TimeoutExpired:
                    osplat.kill_process_tree(proc.pid)
                    proc.wait()
                    code = 124
                finally:
                    reader.join()
                    job.pid = None
                output = ''.join(chunks)
                if code == 124:
                    output += '\ngit command timed out\n'
                    try:
                        action_logs.append(getattr(job, 'log_file', None), '\ngit command timed out\n')
                    except OSError:
                        pass
                if log_errors:
                    output += '\nGit log write failed: ' + log_errors[0] + '\n'
                return code, output
        done = subprocess.run(argv, capture_output=True, text=True, encoding="utf-8", errors="replace", timeout=timeout)
    except (OSError, subprocess.SubprocessError) as exc:
        message = "git could not run: %s" % exc
        if job is not None:
            job.pid = None
            try:
                action_logs.append(getattr(job, 'log_file', None), message)
            except OSError:
                pass
        return 127, message
    return done.returncode, (done.stdout + done.stderr)


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
    if state.get("stopping"):
        stop_plan(conn, env.name, plan)
        return None
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
        elif state['phase'] == 'shared-repair':
            result = _advance_shared_repair(conn, env, plan, state)
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
    answered = runtime.list_questions(conn, project, status='answered', plan=plan, item='owner-check-answered')
    state['owner_check'] = max((q['id'] for q in answered), default=0)
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
    from . import projsettings
    settings = projsettings.working_settings(env.settings)
    if settings["mergeMode"] == "git" or any(settings[s.lower() + "Enabled"] and settings[s.lower() + "Mode"] == "git" for s in COMMAND_STEPS):
        provider, errors = git_provider.for_project(env)
        if provider is None:
            return _provider_wait(conn, env, plan, state, state["step"],
                                  "; ".join(errors) or "Git provider is not supported; choose Local in project settings")
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
        state.pop("release", None)
        save(conn, env.name, plan, state)
    _blocker(conn, env, plan, "")
    _set_status(conn, env, plan, "RUNNING")
    for _ in range(4 * (len(STEPS) + 2)):   # inline steps follow each other in one round (also after a moved remote); a running command ends the round
        step = state["step"]
        ahead = _ahead(conn, env.name, plan)
        if step == "Build" and settings.get("serialIntegrationBuild") and ahead and not state.get("job"):
            _blocker(conn, env, plan, "waiting for earlier plans to finish integration before building")
            _set_status(conn, env, plan, "WAITING")
            return None
        if step in ("Push", "Deploy", "Release", "Close") and ahead:
            # Waiting through Close also serializes Deploy and release publication.
            _blocker(conn, env, plan, "waiting for earlier plans to push and deploy")
            _set_status(conn, env, plan, "WAITING")
            return None
        if step == "Merge":
            outcome = (_provider_step(conn, env, plan, state, folder, step) if settings["mergeMode"] == "git"
                       else _merge(conn, env, plan, state, folder))
        elif step == "Push":
            outcome = (_done(conn, env, plan, state, step, "integrated through pull request") if settings["mergeMode"] == "git"
                       else _push(conn, env, plan, state, folder))
        elif step in COMMAND_STEPS:
            outcome = (_provider_step(conn, env, plan, state, folder, step) if settings[step.lower() + "Mode"] == "git"
                       else _command(conn, env, plan, state, folder, step, free_mb))
        elif step == "Release":
            outcome = _release(conn, env, plan, state, folder)
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
    _finish_log(state, 0, message=text)
    state["steps"][step] = "done"
    if step not in state["ran"]:
        state["ran"].append(step)
    _log(env, "%s: %s done %s%s" % (_short(plan), step, _counter(env, step), (" - " + text) if text else ""))
    _next_step(env, state, step)
    save(conn, env.name, plan, state)
    return "next"


def _provider_wait(conn, env, plan, state, step, reason):
    state["steps"][step] = "running"
    save(conn, env.name, plan, state)
    _blocker(conn, env, plan, reason)
    _set_status(conn, env, plan, "WAITING")


def _provider_token(provider):
    return provider.credential()


def _provider_push(conn, env, plan, state, folder):
    """Push only the plan branch, persisting the exact revision for restart and retry."""
    from . import bees
    named = bees.plan_worktree(env, plan)
    remote, target = _remote(env)
    source = named[0] if named else ""
    if not source or source == target:
        raise git_provider.ProviderError("provider integration needs a separate plan branch")
    head = integration.head_commit(folder)
    if not head:
        raise git_provider.ProviderError("the commit of the plan worktree cannot be read")
    record = state.setdefault("provider", {})
    if record.get("commit") == head:
        return record
    if record.get("pushed") != head:
        code, out = _git(folder, "push", remote, "HEAD:refs/heads/" + source)
        if code:
            raise git_provider.ProviderError("plan branch push failed: " + out)
        if record.get("pr_state") in ("merged", "closed"):
            record.pop("pr", None)
            record.pop("pr_state", None)
        record.pop("commit", None)
        record.pop("pipeline", None)
        record.update(pushed=head, source=source, target=target)
        save(conn, env.name, plan, state)
    return record


def _provider_step(conn, env, plan, state, folder, step):
    """Provider work uses durable identifiers and read-only polling, never a merge API."""
    try:
        provider, errors = git_provider.for_project(env)
        if provider is None:
            return _provider_wait(conn, env, plan, state, step,
                                  "; ".join(errors) or "Git provider is not supported; choose Local in project settings")
        if not provider.supports_step(step):
            reason = "provider %s does not support %s; skipped" % (provider.name, step)
            _log(env, "%s: %s" % (_short(plan), reason))
            state["steps"][step] = "skipped"
            state.setdefault("skipped", {})[step] = reason
            _next_step(env, state, step)
            save(conn, env.name, plan, state)
            return "next"
        token = _provider_token(provider)
        record = _provider_push(conn, env, plan, state, folder)
        record["name"] = provider.name
        state["steps"][step] = "running"
        save(conn, env.name, plan, state)
        if step == "Merge":
            if not record.get("pr"):
                record["pr"] = provider.ensure_pr(_short(plan), record["source"], record["target"], token, commit=record["pushed"])
                save(conn, env.name, plan, state)
            # Detect a red branch pipeline even while a maintainer has not merged yet.
            from . import projsettings
            settings = projsettings.working_settings(env.settings)
            monitor = any(settings[s.lower() + "Enabled"] and settings[s.lower() + "Mode"] == "git" for s in COMMAND_STEPS)
            if monitor and provider.supports_step("Check") and provider.pipeline_state(record["pushed"], token) == "failure":
                return _red(conn, env, plan, state, step, "provider %s pipeline failed for commit %s (PR %s)" %
                            (provider.name, record["pushed"], record["pr"]))
            status = provider.pr_state(record["pr"], token)
            record["pr_state"] = status
            if status == "closed":
                return _provider_wait(conn, env, plan, state, step, "pull request %s closed without merge; waiting for owner" % record["pr"])
            if status != "merged":
                return _provider_wait(conn, env, plan, state, step, "waiting for external merge of pull request %s" % record["pr"])
            commit = provider.pr_commit(record["pr"], token)
            if not commit:
                return _provider_wait(conn, env, plan, state, step, "waiting for the pull request merge commit")
            if integration.head_commit(folder) != commit:
                # A squash or merge commit is what subsequent local commands must check.
                code, out = _git(folder, "status", "--porcelain")
                if code or out:
                    return _red(conn, env, plan, state, step, "cannot prepare merged commit: plan worktree is not clean")
                remote, target = _remote(env)
                code, out = _git(folder, "fetch", remote, target)
                if code:
                    raise git_provider.ProviderError("cannot fetch merged pull request: " + out)
                code, out = _git(folder, "reset", "--hard", commit)
                if code:
                    raise git_provider.ProviderError("cannot prepare merged pull request commit: " + out)
            record["commit"] = commit
            state["merged"] = commit
            return _done(conn, env, plan, state, step, "pull request merged externally")
        commit = record.get("commit") or record["pushed"]
        status = provider.pipeline_state(commit, token)
        record.update(pipeline=status, pipeline_commit=commit)
        if status == "failure":
            return _red(conn, env, plan, state, step, "provider %s pipeline failed for commit %s" % (provider.name, commit))
        if status != "success":
            return _provider_wait(conn, env, plan, state, step, "waiting for provider %s %s pipeline at %s" % (provider.name, step, commit))
        return _done(conn, env, plan, state, step, "provider pipeline green")
    except git_provider.ProviderError as exc:
        if authentication_failure(str(exc)):
            return _access_wait(conn, env, plan, state, step, str(exc))
        # Transient network failures may be retried without spending model tokens.
        return _provider_wait(conn, env, plan, state, step, str(exc))


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
    job.log_file = _start_log(conn, env, plan, state, "Merge")
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
        state["job"].update(pid=job.pid, identity=job.identity)
        save(conn, env.name, plan, state)
    return None


def _merge_work(job, folder, remote, branch, target, result_file):
    """Run cancellable Git commands without holding up other plans in the round."""
    try:
        code, out = _merge_git(job, folder, remote, branch, target)
    except Exception:
        import traceback
        code, out = 1, traceback.format_exc()
        action_logs.append(job.log_file, out)
    action_logs.finish(job.log_file, code, result='cancelled' if job.cancelled else None,
                       message='HQ cancelled this speculative run' if job.cancelled else (out if code else ''))
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
    job = Job(uuid.uuid4().hex)
    job.log_file = _start_log(conn, env, plan, state, "Push")
    remote, branch = _remote(env)
    head = integration.head_commit(folder)
    if not head:
        return _red(conn, env, plan, state, "Push", "the commit of the plan worktree cannot be read")
    if state.get("pushed") == head:
        return _done(conn, env, plan, state, "Push", "already pushed")
    state["steps"]["Push"] = "running"
    save(conn, env.name, plan, state)
    _log(env, "%s: Push started %s" % (_short(plan), _counter(env, "Push")))
    code, out = _git(folder, "fetch", remote, branch, job=job)
    if code != 0:
        return _red(conn, env, plan, state, "Push", "git fetch %s %s failed:\n%s" % (remote, branch, out))
    if _git(folder, "merge-base", "--is-ancestor", "refs/remotes/%s/%s" % (remote, branch), "HEAD", job=job)[0] != 0:
        return _moved(conn, env, plan, state, "Push")
    code, out = _git(folder, "push", remote, "HEAD:refs/heads/%s" % branch, job=job)   # a normal push, never forced
    save_step_log(conn, env.name, plan, "Push", code, out)
    if code != 0:
        if authentication_failure(out):
            return _red(conn, env, plan, state, "Push", out)
        _git(folder, "fetch", remote, branch, job=job)
        if _git(folder, "merge-base", "--is-ancestor", "refs/remotes/%s/%s" % (remote, branch), "HEAD", job=job)[0] != 0:
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
    _finish_log(state, 1, "retry", "remote integration branch moved")
    _log(env, "%s: the remote integration branch moved, integration starts again at Merge" % _short(plan))
    _replay_followers(conn, env, plan)
    state["step"] = "Merge"
    state["steps"] = {}
    state["ran"] = []
    state.pop("merged", None)
    state.pop("release", None)
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
                                                          on_start=job.record_pid, log_file=job.log_file)
            except OSError as exc:
                code, output = 127, "the %s command could not start: %s" % (step, exc)
    finally:
        if lock:
            integration._release_compile_slot(lock, poll)
    if job.cancelled:
        code = code or 143
    if job.log_file and not action_logs.output(job.log_file):
        action_logs.append(job.log_file, output)
    action_logs.finish(job.log_file, code, result='cancelled' if job.cancelled else None,
                       message='HQ cancelled this speculative run' if job.cancelled else '')
    try:
        Path(result_file).write_text(json.dumps({"token": job.token, "code": code, "output": output}), encoding="utf-8")
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
            j.log_file = _start_log(conn, env, plan, state, step)
            JOBS[key] = j
            state["job"] = {"step": step, "token": token, "file": str(result_file), "pid": None, "head": head}
            if planned:
                state['job'].update(owner_pid=os.getpid(), buildJobs=planned['Jobs'], buildParallel=planned['Parallel'])
            state["steps"][step] = "running"
            save(conn, env.name, plan, state)
        _log(env, "%s: %s started %s" % (_short(plan), step, _counter(env, step)))
        if step == "Check":
            compile_job = integration.start_compile_check(env, {"name": token}, cwd=str(folder), key="orch-check:" + plan, poll=POLL, milestones=False, log_file=j.log_file)
            j.token = compile_job["token"]
            j.pid = compile_job["pid"]
            j.identity = compile_job.get("identity")
            j.compile_job = compile_job
            state["job"].update(compile_job)
            state["job"]["compile"] = True
            save(conn, env.name, plan, state)
            if not _async():
                while integration.poll_compile_check(conn, env.name, compile_job) is None:
                    time.sleep(POLL)
        else:
            def launch():
                try:
                    _work(env.settings, j, step, command, folder, extra, result_file)
                except Exception:
                    import traceback
                    output = traceback.format_exc()
                    action_logs.append(j.log_file, output)
                    if agent_process.recorded_alive(j.pid, j.identity):
                        # The observer failed, not necessarily its command. Keep
                        # the reservation until the durable receipt can be read.
                        return
                    action_logs.finish(j.log_file, 1)
                    Path(result_file).write_text(json.dumps({"token": j.token, "code": 1, "output": output}), encoding="utf-8")
                finally:
                    j.started.set()  # A failed launch must release the scheduling round too.
            j.thread = threading.Thread(target=launch, daemon=False)
            j.thread.start()
            # Publish the PID before releasing the scheduling lock. Force stop may
            # arrive before another round has a chance to harvest this command.
            j.started.wait()
            if j.pid:
                state['job'].update(pid=j.pid, identity=j.identity)
                save(conn, env.name, plan, state)
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
        save_step_log(conn, env.name, plan, job["step"], result["exit"], result.get("output", ""))
        return result["exit"], result.get("output", "")
    key = (env.name, plan)
    j = JOBS.get(key)
    if j and j.token == job["token"]:
        if j.pid != job.get("pid"):
            job.update(pid=j.pid, identity=j.identity)
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
        save_step_log(conn, env.name, plan, job["step"], int(data["code"]), data.get("output") or "")
        return int(data["code"]), str(data.get("output") or "")
    pid = job.get("pid")
    if agent_process.recorded_alive(pid, job.get("identity")):
        return None
    receipt = Path(state["log_file"]).with_suffix('.exit') if state.get('log_file') else None
    try:
        recovered_code = int(receipt.read_text(encoding='ascii')) if receipt else None
    except (OSError, ValueError):
        recovered_code = None
    if recovered_code is not None:
        output = action_logs.output(state['log_file'])
        action_logs.finish(state['log_file'], recovered_code)
        JOBS.pop(key, None)
        save_step_log(conn, env.name, plan, job['step'], recovered_code, output)
        return recovered_code, output
    if j and j.token == job["token"]:
        JOBS.pop(key, None)
        _finish_log(state, 1, message="the command ended without a result")
        return 1, "the command ended without a result"
    _finish_log(state, 143, "aborted", "worker ended without a result")
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


def authentication_failure(error):
    """Recognize explicit remote authentication/authorization failures, not code errors."""
    text = str(error).lower()
    return any(marker in text for marker in (
        'authentication failed', 'permission denied (publickey)',
        'could not read username', 'terminal prompts disabled',
        'bad credentials', 'resource not accessible by',
        'http 401', 'http 403', 'error: 401', 'error: 403',
        'requested url returned error: 401', 'requested url returned error: 403',
        'without workflow scope', "without `workflow` scope", "without 'workflow' scope",
    )) or ('permission to ' in text and ' denied to ' in text)


def _access_wait(conn, env, plan, state, step, error):
    """Require an owner answer before retrying this exact step; keep completed work."""
    if step in ('Merge', 'Check', 'Build', 'Push'):
        _replay_followers(conn, env, plan)
    item = 'integration-access-' + step.lower()
    pending = runtime.list_questions(conn, env.name, status='open', plan=plan, item=item)
    qid = pending[0]['id'] if pending else db.add_question(
        conn, env.name, plan, item,
        'Integration stopped at %s because access was refused. Correct the Git login, token scopes or repository permissions, then answer to retry this step. Error: %s' % (step, error),
        'Correct access and confirm when this step may be retried. Completed integration steps are retained.')
    _finish_log(state, 1, message=error)
    state['steps'][step] = 'red'
    state['phase'] = 'blocked'
    state['blocked'] = {'kind': 'access', 'question': qid, 'step': step}
    state['placed'] = False
    state.pop('fix', None)
    save(conn, env.name, plan, state)
    hives.release_integration(conn, env.name, plan)
    _blocker(conn, env, plan, 'waiting for access correction (question %s)' % qid)
    _set_status(conn, env, plan, 'WAITING')
    return None


def _red(conn, env, plan, state, step, error):
    """A step is red: the place is released and a fix agent starts."""
    if authentication_failure(error):
        return _access_wait(conn, env, plan, state, step, error)
    path = state.get("log_file")
    if not path or action_logs.read(path)[0]["step"] != step:
        path = _start_log(conn, env, plan, state, step)
        action_logs.append(path, error)
    # Only a failure before publication removes a speculative predecessor.
    # Deploy and Release run after Push: its code remains in the target branch.
    if step in ("Merge", "Check", "Build", "Push"):
        _replay_followers(conn, env, plan)
    _finish_log(state, 1, message=error)
    state["steps"][step] = "red"
    _log(env, "%s: %s red %s - %s" % (_short(plan), step, _counter(env, step), " ".join(error.split())[:200]))
    state["phase"] = "fix"
    state["fix"] = {"agent": None, "reason": "step", "step": step, "error": error, "log_file": state.get("log_file")}
    _coordinate_repair(conn, env, plan, state, step, error)
    _record_repair(conn, env.name, plan, 'failure', step=step, error=error,
                   log_file=state.get('log_file'))
    state["placed"] = False
    save(conn, env.name, plan, state)
    hives.release_integration(conn, env.name, plan)
    return "red"


def _release(conn, env, plan, state, folder):
    """Hold the integration place until publication has completed."""
    from . import publish, projsettings
    if not projsettings.working_settings(env.settings)["releaseEnabled"]:
        return _done(conn, env, plan, state, "Release", "release disabled; skipped")
    try:
        result = publish.pipeline_release(conn, env, plan, state, folder,
                                          projsettings.working_settings(env.settings))
        if result is None:
            return _provider_wait(conn, env, plan, state, "Release", "waiting for release publication")
        return _done(conn, env, plan, state, "Release", result)
    except publish.ReleaseFailed as exc:
        return _red(conn, env, plan, state, "Release", str(exc))
    except Exception as exc:
        if authentication_failure(exc):
            return _access_wait(conn, env, plan, state, "Release", str(exc))
        return _provider_wait(conn, env, plan, state, "Release", "release publication: " + str(exc))


def _close(conn, env, plan, state, folder):
    path = _start_log(conn, env, plan, state, "Close")
    try:
        result = _close_run(conn, env, plan, state, folder)
    except Exception:
        import traceback
        action_logs.append(path, traceback.format_exc())
        action_logs.finish(path, 1)
        raise
    action_logs.finish(path, 0)
    return result


def _close_run(conn, env, plan, state, folder):
    from . import projsettings
    if projsettings.working_settings(env.settings)["releaseEnabled"] and state["steps"].get("Release") != "done":
        state["step"] = "Release"
        save(conn, env.name, plan, state)
        return False
    head = (state.get("provider") or {}).get("commit") or integration.head_commit(folder)


    row = conn.execute("SELECT * FROM queue WHERE project=? AND plan_path=? ORDER BY id DESC LIMIT 1", (env.name, plan)).fetchone()
    branch = row["branch"] if row else (__import__("haivequeen.bees", fromlist=["x"]).plan_worktree(env, plan) or ("", ""))[0]
    if row is None:
        db.add_ready(conn, env.name, branch, head, plan, "integrated by the Orchestrator")
    runtime.queue_upsert(conn, env.name, branch, tip=head, plan=plan, status="integrated", note="integrated by the Orchestrator")
    newest = conn.execute("SELECT id FROM queue WHERE project=? AND plan_path=? ORDER BY id DESC LIMIT 1", (env.name, plan)).fetchone()
    if newest:
        conn.execute("UPDATE hives SET ready_id=? WHERE project=? AND plan_path=?", (newest[0], env.name, plan))
    state["steps"]["Close"] = "done"
    hives.release_integration(conn, env.name, plan)
    # the worktree cleanup runs at the start of the next round (it takes the scheduling lock, which this round holds)
    bees = __import__("haivequeen.bees", fromlist=["x"])
    for package in bees.rejected_packages(conn, env.name, plan):   # the packages the owner rejected count as done again
        bees.set_package_state(conn, env.name, plan, package, "done", "Orchestrator")
    _save_last(conn, env.name, plan, commit=head, steps=list(state["ran"]),
               owner_check=state.get('owner_check', 0))
    if repair_history(conn, env.name, plan):
        _record_repair(conn, env.name, plan, 'verified', commit=head,
                       scope='integration pipeline', steps=list(state['ran']))
    for key in state.get('shared_causes', []):
        group = json.loads(runtime.kv_get(conn, env.name, key) or '{}')
        if group.get('leader') == plan:
            group['verified_commit'] = head
            runtime.kv_set(conn, env.name, key, json.dumps(group))
    action_logs.append(state.get("log_file"), "Queue integrated; integration reservation released; commit %s\n" % head)
    _log(env, "%s: integration done, commit %s" % (_short(plan), head[:7]))
    runtime.kv_set(conn, env.name, KEY + plan, "")
    runtime.kv_set(conn, env.name, "hive-blocker:" + plan, "")
    _hive_state(conn, env.name, plan, "waiting")
    return True


# ---------------------------------------------------------------- fix agent

def _coordinate_repair(conn, env, plan, state, step, error):
    """Coalesce detailed identical local diagnostics, never just a generic exit."""
    import hashlib
    command = commands(env).get(step)
    if step not in ('Check', 'Build') or not command:
        return
    diagnostic = str(error).strip()
    folder = _worktree(env, plan)
    if folder:
        diagnostic = diagnostic.replace(str(folder), '<worktree>').replace(folder.as_posix(), '<worktree>')
    # A wrapper such as 'checks failed, see file' cannot establish a common cause.
    if len(diagnostic) < 80 or not re.search(r'assertion|traceback|error:|fatal:|timed out|timeout', diagnostic, re.I):
        return
    fingerprint = hashlib.sha256(json.dumps([step, command, diagnostic]).encode()).hexdigest()
    index = 'orch-shared-index:' + fingerprint
    key = runtime.kv_get(conn, env.name, index)
    group = json.loads(runtime.kv_get(conn, env.name, key) or '{}') if key else {}
    if not group or group.get('verified_commit'):
        key = 'orch-shared-repair:' + uuid.uuid4().hex
        group = {'leader': plan, 'step': step, 'diagnostic': diagnostic, 'members': {}}
        runtime.kv_set(conn, env.name, index, key)
    leader, seen = group['leader'], set()
    while leader not in seen:
        if leader == plan:
            # The other repair already waits on this plan: consolidate both
            # causes here rather than forming a circular dependency.
            group['leader'] = plan
            break
        seen.add(leader)
        waiting = load(conn, env.name, leader) or {}
        if waiting.get('phase') != 'shared-repair':
            break
        dependency = json.loads(runtime.kv_get(conn, env.name, waiting['shared_repair']) or '{}')
        leader = dependency.get('leader')
        if not leader:
            break
    group['members'][plan] = state.get('log_file')
    runtime.kv_set(conn, env.name, key, json.dumps(group))
    if group['leader'] == plan:
        if key not in state.setdefault('shared_causes', []):
            state['shared_causes'].append(key)
    else:
        state.update(phase='shared-repair', shared_repair=key)
        _record_repair(conn, env.name, plan, 'shared-repair-wait', leader=group['leader'],
                       log_file=state.get('log_file'), group=key)
        _blocker(conn, env, plan, 'waiting for verified shared repair: ' + group['leader'])
        _set_status(conn, env, plan, 'WAITING')


def _advance_shared_repair(conn, env, plan, state):
    key = state['shared_repair']
    group = json.loads(runtime.kv_get(conn, env.name, key) or '{}')
    if group.get('verified_commit'):
        _record_repair(conn, env.name, plan, 'shared-repair-verified', leader=group['leader'],
                       commit=group['verified_commit'], group=key)
        # The normal Merge fetches the published fix; every follower still runs
        # its own full integration. No result is copied from the repair leader.
        state.update(phase='pipeline', step='Merge', placed=False, steps={}, ran=[],
                     fix=None, moved=0, rejoining=True)
        for field in ('source', 'merged', 'release', 'shared_repair'):
            state.pop(field, None)
        save(conn, env.name, plan, state)
        _blocker(conn, env, plan, '')
        return
    leader = group.get('leader')
    if not leader or _plan_status(conn, env.name, leader) in ('CANCELLED', 'DONE') or not load(conn, env.name, leader):
        # A cancelled leader is not success. Transfer diagnosis, retaining every
        # member's evidence, without touching the old process or attempt history.
        group['leader'] = plan
        runtime.kv_set(conn, env.name, key, json.dumps(group))
        state.setdefault('shared_causes', []).append(key)
        state['phase'] = 'fix'
        state.pop('shared_repair', None)
        save(conn, env.name, plan, state)
        return
    _blocker(conn, env, plan, 'waiting for verified shared repair: ' + leader)
    _set_status(conn, env, plan, 'WAITING')


def repair_history(conn, project, plan):
    raw = runtime.kv_get(conn, project, 'orch-repair-history:' + plan)
    return json.loads(raw) if raw else []


def _record_repair(conn, project, plan, event, **fields):
    history = repair_history(conn, project, plan)
    history.append({'event': event, 'time': db.now(), **fields})
    runtime.kv_set(conn, project, 'orch-repair-history:' + plan, json.dumps(history))

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
    if fix.get("log_file"):
        what += ("\nRead the complete action log of this failed run: %s\n"
                 "The JSONL output records contain the complete, uncut command output. "
                 "Fix all errors in that output in one run, not only the last or first error.\n" % fix["log_file"])
    if owner_text:
        what += "\nAdditional recovery instruction:\n%s\n" % owner_text
    history = repair_history(env.conn, env.name, plan)
    if history:
        what += '\nCOMPLETE REPAIR HISTORY (read every referenced log before choosing a new approach)\n' + json.dumps(history, indent=2)
    for key in state.get('shared_causes', []):
        group = json.loads(runtime.kv_get(env.conn, env.name, key) or '{}')
        what += '\nSHARED FAILURE: repair the common cause; these plans await full verification.\n' + json.dumps(group, indent=2)
        for member in group.get('members', {}):
            if member != plan:
                what += '\nRelated repair history:\n' + json.dumps(repair_history(env.conn, env.name, member), indent=2)
    what += ('\nAn ended agent is not a verified repair. Diagnose prior failed attempts, add a targeted regression '
             'for the original failure and report its command and result. Commit the repair; an unchanged tree '
             'does not justify repeating the integration. Never weaken checks.\n')
    text = ("You are the fix agent of the plan %s. Work only in the plan worktree %s on the plan branch.\n%s\n"
            "Fix exactly this. On a merge conflict keep the content of both sides (the merge of %s/%s is left open in the worktree: resolve it and commit it). "
            "Commit your fix and end. Do not push, do not merge into the integration branch, do not call bzz ready and do not change the queue: the Orchestrator of HaiveQueen "
            "starts the integration again at the first step after your fix. Write your final report as the answer of your last message.\n\nTHE PLAN\n\n%s"
            % (plan, folder, what, remote, branch, plan_text))
    return text


def _advance_fix(conn, env, plan, state, free_mb, alive):
    fix = state["fix"]
    if fix.get('verification'):
        return _verify_repair(conn, env, plan, state)
    if fix.get("agent"):
        row = next((a for a in runtime.list_agents(conn, env.name, "all") if a.get("name") == fix["agent"] and a.get("phase") == "fix"), None)
        if row is not None and agent_process.counts(row, alive):
            _set_status(conn, env, plan, "RUNNING")
            return None
        _log(env, "%s: fix agent %s ended" % (_short(plan), fix["agent"]))
        _save_last(conn, env.name, plan, fix_agent=fix["agent"])
        if fix.get("owner_stopped"):   # M-4: an agent the owner stopped is never restarted; the plan waits for his instruction
            return _block(conn, env, plan, state, "stopped")
        folder = _worktree(env, plan)
        head = integration.head_commit(folder) if folder else ''
        try:
            report = (env.run_dir(fix['agent']) / 'final.txt').read_text(encoding='utf-8', errors='replace')
        except OSError:
            report = ''
        status_code, status_output = _git(folder, 'status', '--porcelain') if folder else (1, '')
        changed = bool(folder and fix.get('head') and head and
                       _git(folder, 'diff', '--quiet', fix['head'], head)[0] == 1 and
                       status_code == 0 and not status_output.strip())
        conditions_changed = 'commands' in fix and fix['commands'] != commands(env)
        _record_repair(conn, env.name, plan, 'attempt-ended', agent=fix['agent'],
                       before=fix.get('head', ''), after=head, report=report,
                       changed=changed, conditions_changed=conditions_changed)
        if not changed and not conditions_changed:
            fix['agent'] = None
            fix['instruction'] = ('The previous attempt provided no committed repair or changed command. '
                                  'Continue diagnosis using the complete history; do not repeat the unchanged pipeline.')
            save(conn, env.name, plan, state)
            return None
        try:
            evidence_path = env.run_dir(fix['agent']) / 'repair-verification.json'
            if evidence_path.stat().st_size > 65536:
                raise ValueError('verification record exceeds 64 KiB')
            evidence = json.loads(evidence_path.read_text(encoding='utf-8'))
            if not isinstance(evidence, dict) or evidence.get('commit') != head:
                raise ValueError('verification must name the current repair commit')
            if evidence.get('failure_log') != fix.get('log_file', ''):
                raise ValueError('verification must reference the original failure log')
            if not isinstance(evidence.get('command'), str) or not evidence['command'].strip():
                raise ValueError('a targeted regression command is required')
            if not isinstance(evidence.get('reason'), str) or not evidence['reason'].strip():
                raise ValueError('explain how this regression covers the original failure')
        except (OSError, ValueError) as exc:
            return _repair_diagnosis(conn, env, plan, state, 'Missing or invalid repair verification: ' + str(exc))
        fix['verification'] = {**evidence, 'retry_reason':
                               'committed tree changed' if changed else 'configured command changed'}
        save(conn, env.name, plan, state)
        return _verify_repair(conn, env, plan, state)
    return _start_fix(conn, env, plan, state, free_mb, alive, str(fix.get("instruction") or ""))


def _repair_diagnosis(conn, env, plan, state, reason):
    fix = state['fix']
    _record_repair(conn, env.name, plan, 'diagnosis-required', agent=fix.get('agent'), reason=reason)
    fix.pop('verification', None)
    fix['agent'] = None
    fix['instruction'] = reason + '. Continue diagnosis from the complete repair history; do not repeat the unchanged pipeline.'
    save(conn, env.name, plan, state)


def _verify_repair(conn, env, plan, state):
    """Run the targeted regression through the bounded, durable check supervisor."""
    from copy import copy
    fix = state['fix']
    evidence = fix['verification']
    folder = _worktree(env, plan)
    if not state.get('job'):
        if not folder or integration.head_commit(folder) != evidence['commit']:
            return _repair_diagnosis(conn, env, plan, state, 'Repair commit changed before targeted verification')
        check_env = copy(env)
        check_env.settings = {**env.settings, 'compileCheck': evidence['command']}
        path = _start_log(conn, env, plan, state, 'RepairCheck', command=evidence['command'])
        evidence['log_file'] = path
        job = integration.start_compile_check(check_env, {'name': fix['agent']}, cwd=str(folder),
                    key='orch-repair-check:' + plan, poll=POLL, milestones=False, log_file=path)
        state['job'] = {**job, 'compile': True, 'step': 'RepairCheck', 'head': evidence['commit']}
        worker = Job(job['token'])
        worker.pid, worker.identity = job['pid'], job.get('identity')
        worker.compile_job, worker.log_file = job, path
        JOBS[(env.name, plan)] = worker
        save(conn, env.name, plan, state)
        if not _async():
            while integration.poll_compile_check(conn, env.name, job) is None:
                time.sleep(POLL)
    result = _harvest(conn, env, plan, state)
    if result is None:
        return None
    state.pop('job', None)
    code, output = result if result != 'lost' else (143, 'Targeted verification result was lost')
    _record_repair(conn, env.name, plan, 'targeted-verification', commit=evidence['commit'],
                   command=evidence['command'], reason=evidence['reason'], exit_code=code,
                   log_file=evidence['log_file'], failure_log=evidence['failure_log'])
    if code != 0:
        if authentication_failure(output):
            _access_wait(conn, env, plan, state, 'RepairCheck', output)
            state['fix'] = fix
            state['blocked']['kind'] = 'repair-access'
            save(conn, env.name, plan, state)
            return None
        return _repair_diagnosis(conn, env, plan, state,
            'Targeted regression failed (exit %s); read %s' % (code, evidence['log_file']))
    status, changes = _git(folder, 'status', '--porcelain', '--untracked-files=no')
    if integration.head_commit(folder) != evidence['commit'] or status or changes.strip():
        return _repair_diagnosis(conn, env, plan, state, 'The targeted regression changed the committed repair')
    _record_repair(conn, env.name, plan, 'pipeline-retry', commit=evidence['commit'],
                   reason=evidence['retry_reason'], verification_log=evidence['log_file'])
    state.update(phase='pipeline', step='Merge', placed=False, steps={}, ran=[], fix=None, moved=0, rejoining=True)
    for field in ('release', 'source', 'merged'):
        state.pop(field, None)
    save(conn, env.name, plan, state)


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
    base_name, suffix = name, 1
    while name in existing:
        suffix += 1
        name = '%s-%s' % (base_name, suffix)
    before_head = integration.head_commit(folder)
    action = {"name": name, "type": pick["name"], "phase": "fix", "plan": plan, "package": "", "cwd": str(folder), "prompt": _fix_prompt(env, plan, state, owner_text), "class": cls}
    action['prompt'] += ('\nREQUIRED TARGETED VERIFICATION\nBefore ending, write %s as JSON with fields '
                         'command (the targeted regression command), commit (full repaired HEAD), '
                         'reason (how it covers the original failure), and failure_log = %s. '
                         'HQ runs this command itself, then the unchanged full integration pipeline. '
                         'A passing report alone is insufficient. Do not weaken existing checks.\n' %
                         (env.run_dir(name) / 'repair-verification.json', json.dumps(fix.get('log_file', ''))))
    st = {"names": existing, "busy": [], "plan_status": {sch.norm_plan(plan): "RUNNING"}, "by_path": {sch.norm_plan(plan): {"path": plan}}}
    ctx = {"env": env, "state": st, "pool": loop.Pool(free, 0), "report": [], "dry": False, "settle": 0}
    if not loop._do_start(ctx, action, forced_pick=pick):
        return None
    started = st["names"][-1]
    fix["agent"] = started
    fix["head"] = before_head
    fix['commands'] = commands(env)
    _record_repair(conn, env.name, plan, 'attempt-started', agent=started, commit=before_head,
                   step=fix['step'], log_file=fix.get('log_file'))
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
    if state['blocked'].get('kind') == 'repair-access':
        _record_repair(conn, env.name, plan, 'access-corrected', question=state['blocked']['question'])
        state['phase'] = 'fix'
        state.pop('blocked', None)
        save(conn, env.name, plan, state)
        return None
    if state['blocked'].get('kind') == 'access':
        state['step'] = state['blocked']['step']
        state['phase'] = 'pipeline'
        state['steps'][state['step']] = 'todo'
        state.pop('blocked', None)
        save(conn, env.name, plan, state)
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
    if job and pid is not None:
        record.update(pid=pid, identity=job.identity)
    elif job:
        pid = record.get("pid")   # retain the last identity while its worker unwinds
    identity = record.get("identity")
    if pid and record.get("cancel_pid") != pid:
        if agent_process.identity_matches(pid, identity):
            stop(pid)
            record["cancel_pid"] = pid
        else:
            record["cancel_note"] = "Process identity could not be verified; no termination sent"
    save(conn, project, plan, state)
    if job and job.alive():
        return False
    if record.get("compile"):
        # A supervisor may publish before exiting, and recovery must also confirm
        # that an orphaned check has ended before the worktree can be reused.
        if agent_process.recorded_alive(record.get("pid"), identity):
            return False
        if integration.poll_compile_check(conn, project, record) is None:
            return False
    elif agent_process.recorded_alive(pid, identity):
        return False
    action_logs.finish(state.get("log_file"), 143, "cancelled", "pipeline command stopped")
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
    state["stopping"] = True
    save(conn, project, plan, state)
    if not _cancel_pipeline_job(conn, project, plan, state, stop):
        return
    fix = state.get("fix")
    if state.get("phase") == "fix" and fix and fix.get("agent"):
        for row in runtime.list_agents(conn, project, "active"):
            if row.get("name") == fix["agent"] and row.get("phase") == "fix" and row.get("pid"):
                verified = agent_process.identity_matches(row["pid"], agent_process._identity(row))
                if verified:
                    stop(row["pid"])
                elif agent_process.recorded_alive(row["pid"], agent_process._identity(row)):
                    fix["stop_note"] = "Process identity could not be verified; no termination sent"
                    save(conn, project, plan, state)
                    return
                if agent_process.recorded_alive(row["pid"], agent_process._identity(row)):
                    return
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
    state.pop("stopping", None)
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

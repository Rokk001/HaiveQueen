"""HaiveQueen command line: python haivequeen/cli.py <command>. Machine-readable output is JSON on stdout."""
import argparse
import contextlib
import io
import json
import os
import sys
from datetime import datetime
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from haivequeen import config, db, legacy, loop, runtime, runtime_cli, selfupdate, updates  # noqa: E402
from haivequeen import export as export_mod, init as init_mod, views  # noqa: E402

STUBS = ()


def out(obj) -> None:
    """Print an object as JSON on stdout."""
    print(json.dumps(obj, ensure_ascii=False))


def fail(message: str, code: int = 1) -> int:
    """Print a JSON error on stdout and return the exit code."""
    out({"ok": False, "error": message})
    return code


def cmd_done(args, conn) -> int:
    """Final report of an agent: store the result file and mark the agent done."""
    text = Path(args.result).read_text(encoding="utf-8")
    try:
        row = db.finish_agent(conn, args.agent, text)
    except KeyError as exc:
        return fail(str(exc))
    from haivequeen import bees, branchguard
    violation = bees.branch_violation(conn, row['project'], row['name']) if row['phase'] == 'bee' else ''
    out({"ok": True, "agent": args.agent, "project": row["project"], **branchguard.failure_fields(violation)})
    return 0


# Compare a Hive plan with a relative or absolute plan path.
def _plan_matches(conn, project, stored, supplied):
    """Compare a plan with a relative or absolute plan path."""
    if not stored or not supplied:
        return False
    if os.path.normcase(os.path.normpath(str(stored))) == os.path.normcase(os.path.normpath(str(supplied))):
        return True
    project_row = conn.execute("SELECT workspace FROM projects WHERE name=?", (project,)).fetchone()
    if not project_row or not project_row[0]:
        return False
    expected = os.path.normcase(os.path.normpath(os.path.join(project_row[0], stored)))
    return expected == os.path.normcase(os.path.normpath(str(supplied)))


# Register a branch as ready; a Queen may register only her own Hive plan.
def cmd_ready(args, conn) -> int:
    """Register a branch as ready; a Queen may register only her own plan."""
    agent = os.environ.get("BZZ_AGENT")
    if agent:
        from haivequeen import hives, readygate, routine, scheduler as sch
        row = runtime.get_agent(conn, args.project, agent)
        if row and row.get("phase") == "queen":
            plan = row.get("plan_path")
            if not _plan_matches(conn, args.project, plan, args.plan) or hives.get(conn, args.project, plan) is None:
                return fail("ready refused: branch does not belong to this Plan")
            args.plan = plan
            from haivequeen import bees
            rejected = bees.rejected_packages(conn, args.project, plan)
            if rejected:
                return fail("ready refused: package(s) %s rejected by the owner; rework them and set their state with bzz package" % ", ".join(rejected))
            env = next((e for e in sch.load_projects(conn, config.load_config()) if e.name == args.project), None)
            if env is None:
                return fail("ready refused: unknown project")
            bare = hives.nothing_to_integrate(conn, env, plan, row)[0]
            if bare:   # nothing left to integrate: no queue row, no compile check, no Build or Deploy step; only the requirement list and the fresh check
                accepted, message, waiting = readygate.gate(env, plan, row)
                if not accepted and not waiting:
                    hives.set_completion(conn, args.project, plan, "")
                    return fail("ready refused: " + message)
                hives.set_completion(conn, args.project, plan, "pending" if waiting else "passed", agent)
                if waiting:
                    out({"ok": False, "error": message, "waiting": True})
                    return 1
                from haivequeen import bees
                for (package,) in conn.execute("SELECT id FROM packages WHERE project=? AND plan_path=?", (args.project, plan)).fetchall():
                    bees.set_package_state(conn, args.project, plan, package, 'done', agent)
                hives.withdraw_integrated(conn, hives.nothing_to_integrate(conn, env, plan, row)[1])
                out({"ok": True, "nothing_to_integrate": True})
                return 0
            hives.set_completion(conn, args.project, plan, "")
            # no compile check result is needed: the Orchestrator runs the check, the build, the push and the deploy after an accepted ready
            accepted, message, waiting = readygate.gate(env, plan, row)   # requirement list first, then the fresh check agent
            if not accepted:
                if waiting:
                    out({"ok": False, "error": message, "waiting": True})
                    return 1
                return fail("ready refused: " + message)
        else:
            env = next((e for e in sch.load_projects(conn, config.load_config()) if e.name == args.project), None)
            why = routine.ready_refusal(env, agent) if env is not None else ""
            if why:
                return fail("ready refused: " + why)
    res = db.add_ready(conn, args.project, args.branch, args.tip, args.plan, args.note)
    if agent and row and row.get("phase") == "queen":
        conn.execute("UPDATE hives SET ready_id=? WHERE project=? AND plan_path=?", (res["id"], args.project, args.plan))
        from haivequeen import bees
        for (package,) in conn.execute("SELECT id FROM packages WHERE project=? AND plan_path=?", (args.project, args.plan)).fetchall():
            bees.set_package_state(conn, args.project, args.plan, package, 'done', agent)   # ready means the Queen answered Open requirements: 0, so every package is done
    out({"ok": True, **res})
    return 0


# Whether a path is the worktree of an Agent of the plan.
def _agent_worktree(conn, project, plan, path) -> bool:
    """True when `path` is the worktree of a process Agent row or a recorded Agent worktree of this plan."""
    def norm(value):
        return os.path.normcase(os.path.realpath(str(value))) if value else ""
    wanted = norm(path)
    if not wanted:
        return False
    known = [a.get("cwd") for a in runtime.list_agents(conn, project, scope="all") if a.get("phase") == "bee" and a.get("plan_path") == plan]
    from haivequeen import bees
    bees.ensure_attempts(conn)
    known += [r[0] for r in conn.execute("SELECT cwd FROM bee_worktrees WHERE project=? AND plan_path=?", (project, plan)).fetchall()]
    return any(norm(k) == wanted for k in known)


# Run the compile check of the project for a Queen or an Agent.
def cmd_compile_check(args, conn) -> int:
    """Wait for the stored result of a detached compile check in the worktree of the caller (a Queen or a process Agent) or, with --worktree, in the worktree of an Agent of the plan, and wait for its end.
    Only the result of the Queen in her own worktree is stored for `bzz ready`; the result of an Agent is stored for that Agent. Exit 0 only when it is green."""
    from haivequeen import hives, integration, scheduler as sch
    agent = os.environ.get("BZZ_AGENT")
    row = runtime.get_agent(conn, args.project, agent) if agent else None
    if not row or row.get("phase") not in ("queen", "bee") or not _plan_matches(conn, args.project, row.get("plan_path"), args.plan) or hives.get(conn, args.project, row.get("plan_path")) is None:
        return fail("compile-check refused: not this Plan's Queen or Agent")
    if row.get("phase") == "queen" and not getattr(args, "worktree", None):   # her subagent Agents call it with their worktree (F-9)
        return fail("compile-check refused: the Queen runs no compile check; the Orchestrator runs it after bzz ready")
    env = next((e for e in sch.load_projects(conn, config.load_config()) if e.name == args.project), None)
    if env is None:
        return fail("compile-check refused: unknown project")
    worktree = getattr(args, "worktree", None)
    if worktree:
        if not _agent_worktree(conn, args.project, row.get("plan_path"), worktree):
            return fail("compile-check refused: %s is not the worktree of an Agent of this Plan (foreign worktree)" % worktree)
        cwd, key = str(worktree), integration.COMPILE_AGENT_KEY + os.path.normcase(os.path.realpath(str(worktree)))
    else:
        cwd = row.get("cwd")
        key = None if row.get("phase") == "queen" else integration.COMPILE_AGENT_KEY + str(row.get("name"))
    if not str(env.settings.get("compileCheck") or "").strip():
        out({"ok": True, "skipped": "no compile check is configured for this Project; run your tests directly"})
        return 0
    if not integration.head_commit(cwd):
        return fail("compile-check refused: the commit of the worktree cannot be read")
    result = integration.run_compile_check(env, row, cwd=cwd, key=key, milestones=not worktree)
    out(result)
    return 0 if result["ok"] else 1


# File a question for the owner.
def cmd_ask(args, conn) -> int:
    """File a question for the owner."""
    from haivequeen import triage
    open_questions = runtime.list_questions(conn, args.project, status="open", plan=args.plan, item=args.item)
    if open_questions:
        qid = open_questions[-1]["id"]
        with db.transaction(conn):
            conn.execute("UPDATE questions SET question=question || ? WHERE id=? AND status='open'", ("\n\n" + args.question, qid))
            db.add_event(conn, args.project, "note", "Additional text for question %d: %s" % (qid, args.question), data={"question_id": qid})
        out({"ok": True, "id": qid, "status": "open"})
        return 0
    known = triage._known_answer(conn, args.project, args.plan, args.item)
    if known:
        qid = db.add_question(conn, args.project, args.plan, args.item, args.question, args.recommendation, status="triage")
        out(triage._close_with_reference(conn, qid, *known))
    else:
        qid = db.add_question(conn, args.project, args.plan, args.item, args.question, args.recommendation)
        out({"ok": True, "id": qid, "status": "open"})
    return 0


# Store a note and preserve the calling agent identity.
def cmd_note(args, conn) -> int:
    """Write a free-text note into the event history."""
    with db.transaction(conn):
        eid = db.add_event(conn, args.project, "note", args.text, agent=os.environ.get("BZZ_AGENT"))
    out({"ok": True, "id": eid})
    return 0


def cmd_status(args, conn) -> int:
    """Show progress per project (text table, or JSON with --json)."""
    data = db.project_status(conn, args.project)
    if args.json:
        out(data)
        return 0
    for p in data:
        print(f"{p['project']}: {p['integrated']} / {p['total']} integrated ({p['percent']}%)")
        w1 = max([len("Task")] + [len(t["task"]) for t in p["tasks"]])
        w2 = max([len("Agent")] + [len(t["agent"]) for t in p["tasks"]])
        print(f"{'Task':<{w1}} | {'Agent':<{w2}} | Status")
        for t in p["tasks"]:
            print(f"{t['task']:<{w1}} | {t['agent']:<{w2}} | {t['status']}")
        if p["stalled"]:
            print(f"WARNING: {len(p['stalled'])} agent(s) stalled (no output, still running): {', '.join(p['stalled'])}")
        print()
    return 0


def cmd_questions(args, conn) -> int:
    """List questions as JSON (filters: --open/--status, --project, --plan, --item)."""
    out(runtime.list_questions(conn, args.project, "open" if args.open else args.status, args.plan, args.item))
    return 0


def cmd_answer(args, conn) -> int:
    """Answer a question."""
    try:
        res = db.answer_question(conn, args.id, args.text, args.by)
    except (KeyError, ValueError) as exc:
        return fail(str(exc))
    out({"ok": True, **res})
    return 0


# List queue rows, optionally filtered to one branch, or update a branch row.
def cmd_queue(args, conn) -> int:
    """List the integration queue as JSON, or (action upsert) change or add the row of a branch."""
    if args.action == "upsert":
        if not (args.project and args.branch):
            return fail("queue upsert needs --project and --branch", 2)
        return runtime_cli.run(lambda a, c: runtime.queue_upsert(
            c, a.project, a.branch, a.tip, a.plan, a.status, a.note, a.run))(args, conn)
    rows = db.queue_rows(conn, args.project)
    if args.branch:
        rows = [row for row in rows if row["branch"] == args.branch]
    out(rows)
    return 0


def cmd_views(args, conn) -> int:
    """Write the generated Markdown views of a project."""
    out({"ok": True, "written": views.write_views(conn, args.project)})
    return 0


def cmd_export(args, conn) -> int:
    """Export the database now (--force) or only when the daily export is due."""
    if not args.force and not export_mod.export_due(datetime.now()):
        out({"ok": True, "skipped": "not due"})
        return 0
    out(export_mod.run_export())
    return 0


def cmd_init(args, conn) -> int:
    """Create the project files in a folder; existing files are kept."""
    messages = []
    files = init_mod.init_project(args.folder, messages)
    out({"ok": True, "files": files, "messages": messages})
    return 0


def cmd_register(args, conn) -> int:
    """Register the project in a folder in config.toml and the database."""
    out(init_mod.register_project(conn, args.folder))
    return 0


def make_stub(name: str):
    """Build a command handler that reports the command as not implemented yet."""
    def stub(args, conn) -> int:
        """Report that the command is not available in S1."""
        return fail(f"{name}: not implemented in S1", 2)
    return stub


# Acquire or release the shared integration place and clean worktrees after release.
def cmd_integration(args, conn):
    """Acquire or release the integration place, cleaning worktrees after release."""
    from haivequeen import hives
    agent = os.environ.get("BZZ_AGENT")
    row = runtime.get_agent(conn, args.project, agent) if agent else None
    if agent and (not row or row.get("phase") != "queen" or not _plan_matches(conn, args.project, row.get("plan_path"), args.plan)):
        return fail("integration refused: not this Plan's Queen")
    if row:
        args.plan = row['plan_path']
    from haivequeen import integration
    if args.action == "release":
        from haivequeen import scheduler, worktrees
        env = next((e for e in scheduler.load_projects(conn, config.load_config()) if e.name == args.project), None)
        if env:
            from haivequeen import publish
            try:
                publish.after_integration(conn, env, args.plan)   # HaiveQueen publishes the release itself; a failure is retried by the loop
            except Exception as exc:
                db.add_event(conn, args.project, "release", "release step failed: %s" % exc)
        hives.release_integration(conn, args.project, args.plan)
        if env:
            worktrees.cleanup(env)
        out({"ok": True})
        return 0
    owner_pid = (row.get("pid") if row else None) or getattr(args, "owner_pid", None)
    if not owner_pid or int(owner_pid) <= 0:
        return fail("external integration requires the window process ID via --owner-pid")
    from haivequeen import buildjobs, scheduler
    from haivequeen import platform as osplat
    env = next((e for e in scheduler.load_projects(conn, config.load_config()) if e.name == args.project), None)
    from haivequeen import projsettings
    reason = projsettings.remote_wait_reason(env.settings) if env else ""
    if reason:   # the Git remote of the project is not chosen: wait like for an occupied place, the dashboard shows this reason
        if row:
            conn.execute("UPDATE hives SET state='integration_wait' WHERE project=? AND plan_path=?", (args.project, args.plan))
        return fail("waiting for integration: " + reason)
    free_mb = float(osplat.free_ram_mb())
    builds = bool(env) and integration.builds(env.settings)   # without a Build and a Deploy command the integration builds nothing, so it needs no RAM and no job count
    low = buildjobs.waiting_for_ram(buildjobs.settings_from(env.settings.get("buildJobs")), free_mb) if builds else None
    if low:   # below the minimum free RAM before a build: wait like for an occupied place, without taking the place; the next round asks again
        if row:
            conn.execute("UPDATE hives SET state='integration_wait' WHERE project=? AND plan_path=?", (args.project, args.plan))
        return fail("waiting for integration: " + low)
    with buildjobs.reservation_guard():
        job = buildjobs.plan_build(env, free_mb=free_mb, exclude=agent or (args.project, args.plan)) if builds else None   # refresh an existing allocation without counting itself
        if job is not None and job["Jobs"] == 0:
            if row:
                conn.execute("UPDATE hives SET state='integration_wait' WHERE project=? AND plan_path=?", (args.project, args.plan))
            return fail("waiting for integration: waiting for build cores")
        if not hives.take_integration(conn, args.project, args.plan, owner_pid=owner_pid, build_jobs=job["Jobs"] if job else None):
            if row:
                conn.execute("UPDATE hives SET state='integration_wait' WHERE project=? AND plan_path=?", (args.project, args.plan))
            return fail("waiting for integration")
    result = {"ok": True, "buildJobs": job}
    hive = hives.get(conn, args.project, args.plan)
    if env and hive:
        result["integrationSteps"] = {"compileCheck": bool(str(env.settings.get("compileCheck") or "").strip()), "build": bool(str(env.settings.get("buildCommand") or "").strip()),
                                      "deploy": bool(str(env.settings.get("deployCommand") or "").strip())}
    out(result)
    return 0


# Resolve an owner-provided Hive name to exactly one project and plan.
def _resolve_hive(conn, name):
    """Resolve an owner-provided Plan name to exactly one project and plan."""
    rows = db.rows(conn, "SELECT project,plan_path FROM hives WHERE plan_path=? OR project || ':' || plan_path=?", (name, name))
    if len(rows) != 1:
        raise ValueError("Plan not found or ambiguous")
    return rows[0]


# End the Queen and separate Bees of the chosen Hive.
def cmd_dequeen(args, conn):
    """End the Queen and separate Agents of the chosen Plan."""
    from haivequeen import hives
    try:
        with loop.hive_schedule_lock():
            row = _resolve_hive(conn, args.hive)
            hives.dequeen(conn, row['project'], row['plan_path'])
    except ValueError as exc:
        return fail(str(exc))
    out({'ok': True})
    return 0


# Request a replacement Queen with an optional provider.
def cmd_requeen(args, conn):
    """Request a replacement Queen with an optional provider."""
    from haivequeen import hives, scheduler
    try:
        row = _resolve_hive(conn, args.hive)
        machine = config.load_config()
        env = next(e for e in scheduler.load_projects(conn, machine) if e.name == row['project'])
        with loop.hive_schedule_lock():
            hives.requeen(conn, row['project'], row['plan_path'], provider=args.provider, providers=env.providers,
                          queen_class=env.settings.get('queenClass', 'medium'), excluded=machine.get('excludedProviders', []))
            hives.schedule_round(conn, machine, [env], plan_files=[row['plan_path']])
    except (ValueError, StopIteration) as exc:
        return fail(str(exc) or 'Project not found')
    out({'ok': True})
    return 0


# Check the plan the calling Queen has cut into packages.
def cmd_plan(args, conn):
    """Check the header of the Queen's own plan (packages, unique ids, titles, dependencies without cycle, capabilities some provider offers, every requirement the Queen wrote in the state file belongs to a package). On success a SENT or PREPARING plan becomes QUEUED and the packages are read into the database; otherwise the problems are reported and nothing changes."""
    if args.action == "force-stop":
        if "BZZ_AGENT" in os.environ:
            return fail("this command is only available to the owner; agents cannot use it")
        from haivequeen import hives
        try:
            hives.stop_hive(conn, args.project, args.plan)
            out({"ok": True})
            return 0
        except (ValueError, KeyError, runtime.CmdError) as exc:
            return fail(str(exc))
    if args.action == "stop-gracefully":
        args.action = "pause"
    if args.action != "check":
        return cmd_owner(args, conn)
    from haivequeen import plans as plan_mod, scheduler
    agent = os.environ.get("BZZ_AGENT")
    queen = runtime.get_agent(conn, args.project, agent) if agent else None
    if not queen or queen.get("phase") != "queen" or not _plan_matches(conn, args.project, queen.get("plan_path"), args.plan):
        return fail("plan check requires the Queen of this Plan")
    env = next((e for e in scheduler.load_projects(conn, config.load_config()) if e.name == args.project), None)
    if not env:
        return fail("unknown Project")
    plan = queen["plan_path"]
    path = runtime._workspace(conn, args.project) / plan
    if not path.is_file():
        return fail("plan file not found: %s" % plan)
    parsed = plan_mod.load_plan(str(path))
    problems = plan_mod.check_plan(parsed, {c for p in env.providers if p.get("enabled", True) for c in p.get("capabilities", [])}, plan_mod.load_requirements(str(path)), main_checkout=env.code_repo)
    if problems:
        return fail("the plan is not cut properly: " + "; ".join(problems))
    status = parsed.status
    if status in plan_mod.CUT_STATUSES:
        runtime.save_cut_assignments(conn, args.project, plan, parsed.packages, path.read_text(encoding="utf-8-sig"))
        status = runtime.set_plan_status(conn, args.project, plan, "QUEUED")["status"]
    runtime.sync_plans(conn, args.project)
    from haivequeen import bees
    default = bees.cut_default(env, plan)
    handoff = parsed.branch != default[0] or not plan_mod.same_folder(parsed.worktree, default[1])   # the cut Queen started in the default worktree
    result = {"ok": True, "plan": plan, "status": status, "packages": [p.id for p in parsed.packages], "warnings": plan_mod.plan_warnings(parsed), "handoff": handoff}
    if handoff:
        result["next"] = "the header names branch %s and worktree %s, not the worktree you run in: write your handoff and end now; the next Queen starts in the worktree of the header" % (parsed.branch, parsed.worktree)
    out(result)
    return 0


# Record the progress state of one package of the calling Queen's Hive.
BUILD_WAIT_REFUSED = "the build is a step of the Orchestrator after bzz ready, the Queen never waits for it: close the package like a code package"


def cmd_package(args, conn):
    """Store the state (working, waiting, implemented, done, rejected) of a package of the Queen's own plan; shown as progress in the window."""
    if getattr(args, "action", None) == "unassign":
        return cmd_owner(args, conn)
    if not args.state:
        return fail("package requires --state or the unassign action")
    from haivequeen import bees
    agent = os.environ.get("BZZ_AGENT")
    queen = runtime.get_agent(conn, args.project, agent) if agent else None
    if not queen or queen.get("phase") != "queen" or not _plan_matches(conn, args.project, queen.get("plan_path"), args.plan):
        return fail("package requires the Queen of this Plan")
    plan = queen["plan_path"]
    if conn.execute("SELECT 1 FROM packages WHERE project=? AND plan_path=? AND id=?", (args.project, plan, args.package)).fetchone() is None:
        return fail("unknown package %s of this plan" % args.package)
    if args.state == "waiting" and not args.reason:
        return fail("waiting needs --reason (question:<id>, build, owner, package or provider)")
    if args.state == "waiting" and args.reason == "build":
        return fail(BUILD_WAIT_REFUSED)
    bees.set_package_state(conn, args.project, plan, args.package, args.state, agent, args.reason)
    out({"ok": True, "package": args.package, "state": args.state})
    return 0


# Run a Bee or record its package attempt for the calling Queen.
def cmd_agent_stop(args, conn):
    """Owner only: end the one running Agent named on the command line (not the Queen, not a subagent); the Queen is told by an owner message."""
    from haivequeen import owner
    if "BZZ_AGENT" in os.environ:
        return fail("this command is only available to the owner; agents cannot use it")
    try:
        out(owner.apply(conn, "stop_agent", args.project, None, name=args.target or args.name))
        return 0
    except (ValueError, KeyError, runtime.CmdError) as exc:
        return fail(str(exc))


def cmd_bee(args, conn):
    """Route and start an Agent, or record an attempt for escalation."""
    from haivequeen import bees, scheduler
    if args.action in ("force-stop", "stop"):
        return cmd_agent_stop(args, conn)
    missing = [f for f in ("plan", "package", "cls") if not getattr(args, f)]
    if missing:
        return fail("agent %s requires --plan, --package and --class" % args.action)
    agent = os.environ.get("BZZ_AGENT")
    queen = runtime.get_agent(conn, args.project, agent) if agent else None
    if not queen or queen.get("phase") != "queen" or queen.get("status") not in ("running", "starting") or not _plan_matches(conn, args.project, queen.get("plan_path"), args.plan):
        return fail("agent command requires the Queen of this Plan")
    env = next((e for e in scheduler.load_projects(conn, config.load_config()) if e.name == args.project), None)
    if not env:
        return fail("unknown Project")
    plan = queen["plan_path"]
    if args.action == "wait":
        provider = scheduler.get_provider(env.providers, args.provider)
        if not provider or provider['name'] in env.settings.get('excludedProviders', []):
            return fail("provider is unavailable or excluded")
        refused = bees.wait_refusal(env, plan, provider)
        if refused:
            return fail(refused)
        runnable = bees.runnable_packages(env, plan, provider)
        if runnable:   # one package without the provider never stops the whole Hive
            return fail("not yet: package %s can still start without %s; work it first and wait only when nothing else can start" % (", ".join(runnable), provider['name']))
        from haivequeen import usage
        windows = usage.limit_windows(conn, provider)
        resets = [w['reset_epoch'] for w in windows if w['used_pct'] >= 100 and w.get('reset_epoch')]
        reset_at = datetime.fromtimestamp(max(resets)).isoformat(timespec='seconds') if resets else None
        bees.wait_for_provider(conn, args.project, plan, provider['name'], provider.get('protocol') == 'codex', reset_at)
        out({"ok": True})
        return 0
    if args.action == "result":
        if not args.attempt or not args.outcome:
            return fail("result requires an attempt name and outcome")
        try:
            requirements = json.loads(args.open_requirements)
            if not isinstance(requirements, list) or not all(isinstance(item, str) for item in requirements):
                return fail("open requirements must be a JSON string list")
        except ValueError as exc:
            return fail(str(exc))
        blocked = ""
        if args.blocked_by:
            if args.outcome == "succeeded":
                return fail("--blocked-by cannot go with the outcome succeeded")
            blocked = args.blocked_by
            if blocked == "build":
                return fail(BUILD_WAIT_REFUSED)
            if blocked == "question":
                open_ids = [str(q["id"]) for q in runtime.list_questions(conn, args.project, status="open", plan=plan)]
                if args.blocked_ref not in open_ids:
                    return fail("--blocked-by question needs --blocked-ref <id of an open question of this plan>")
                blocked = "question:" + args.blocked_ref
        elif args.outcome == "failed" and not requirements and not args.error:
            return fail("a failed attempt needs evidence: --open-requirements or --error; if the Agent only waits for an answer, a build or the owner use --blocked-by")
        args.attempt, violation = bees.package_branch_violation(env, plan, args.package, args.attempt)
        if violation:
            from haivequeen import branchguard
            args.outcome = 'failed'
            args.error = branchguard.failure_text(violation)
            blocked = ''
        refused = bees.release_refusal(conn, args.project, plan, args.package, queen, getattr(args, "ended_evidence", "") or "", args.outcome)
        if refused:   # only trustworthy evidence ends a subagent Bee of an earlier Queen
            return fail(refused)
        bees.report_attempt(env, plan, args.package, args.cls, args.outcome, args.attempt, requirements, args.error, blocked, force_failure=bool(violation))
        next_cls = bees.next_attempt(conn, args.project, plan, args.package, args.cls, bees.escalation_providers(env, plan, args.package),
                                     env.settings.get("excludedProviders", []), int(env.settings.get("escalateAfter", 3)))
        out({"ok": True, "failures": bees.failure_count(conn, args.project, plan, args.package, args.cls), "nextClass": next_cls, **({**branchguard.failure_fields(violation), "outcome": "failed"} if violation else {})})
        return 0
    if not args.name or not args.task:
        return fail("start requires an Agent name and task")
    try:
        steps = json.loads(args.steps)
        paths = json.loads(args.paths)
        rules = json.loads(args.rules)
        if not all(isinstance(x, list) and all(isinstance(v, str) for v in x) for x in (steps, paths, rules)) or not steps:
            return fail("steps, paths and rules must be string lists and steps must not be empty")
        started = bees.start_process(env, plan, args.package, args.name, args.cls, args.task, steps, paths, rules, cwd=args.cwd, no_subagents=not args.subagents or args.no_subagents)
    except (ValueError, TypeError) as exc:
        return fail(str(exc))
    if isinstance(started, dict):
        bees.set_package_state(conn, args.project, plan, args.package, 'working', agent)
        out({"ok": True, "started": started.get("kind") == "process", **started})
        return 0
    if started:
        bees.set_package_state(conn, args.project, plan, args.package, 'working', agent)
    bees.ensure_attempts(conn)
    assignment = conn.execute("SELECT provider FROM bee_assignments WHERE project=? AND plan_path=? AND package=?", (args.project, plan, args.package)).fetchone() if not started else None
    out({"ok": bool(started), "started": bool(started), "kind": "process" if started else "waiting", "provider": assignment[0] if assignment else None, **({} if started else {"reason": bees.LAST_REFUSAL})})
    return 0 if started else 1


# Build the argument parser with all commands.
def cmd_owner(args, conn):
    """Owner-only entry point for explicit control actions."""
    from haivequeen import owner
    if "BZZ_AGENT" in os.environ:
        return fail("this command is only available to the owner; agents cannot use it")
    fields = vars(args).copy()
    action = fields.pop("action")
    project, plan = fields.pop("project", None), fields.pop("plan", None)
    if args.command == "provider":
        fields["provider_action"] = action
        action = "provider"
    try:
        out(owner.apply(conn, action, project, plan, **fields))
        return 0
    except (ValueError, KeyError, runtime.CmdError) as exc:
        return fail(str(exc))


def build_parser() -> argparse.ArgumentParser:
    """Build the argument parser with all commands."""
    ap = argparse.ArgumentParser(prog="bzz")
    sub = ap.add_subparsers(dest="command", required=True)

    p = sub.add_parser("agent", aliases=["bee"], help="start an agent or report its package result (bee is a compatibility alias)", description=(
        "Start creates or reuses the package branch <plan branch>-<package id in lower case> and folder "
        "<worktree prefix>-<plan tag in lower case>-<package id in lower case> in the project's worktree location. "
        "The plan tag is derived from the plan file name without -PLAN and kept for the life of the plan. "
        "Names are based on plan and package, never after an Agent. A new branch starts from the current tip of the plan branch; "
        "a replacement Agent reuses the same worktree and branch. The answer gives cwd and branch, also for subagents. "
        "Agents must not create or commit on another branch. "
        "Force stop: 'bzz agent force-stop <name>' is for the owner only (also the dashboard button and an inbox [[stop_agent]] entry); it ends the one running Agent, "
        "refuses a subagent of the Queen with the reason, and refuses the Queen, who can only be ended by bzz dequeen. "
        "The package stays stopped and waits for an explicit owner instruction. The Queen gets the stop as an owner message in owner_messages. "
        "'bzz agent stop' remains a compatibility alias for force-stop."))
    p.add_argument("action", choices=["start", "result", "wait", "force-stop", "stop"])
    p.add_argument("target", nargs="?", default="", help="force-stop: the name of the one running Agent to end (owner only; not the Queen, not a subagent)")
    p.add_argument("--project", required=True)
    for field in ("plan", "package", "class"):   # required except for force-stop and its stop alias
        p.add_argument("--" + field, default="", dest="cls" if field == "class" else field)
    p.add_argument("--name", default="")
    p.add_argument("--provider", default="")
    p.add_argument("--task", default="")
    p.add_argument("--steps", default="[]")
    p.add_argument("--paths", default="[]")
    p.add_argument("--rules", default="[]")
    p.add_argument("--cwd", default="", help="legacy option; package worktree is chosen by HaiveQueen")
    p.add_argument("--outcome", choices=["failed", "interrupted", "succeeded"])
    p.add_argument("--attempt", default="")
    p.add_argument("--open-requirements", default="[]")
    p.add_argument("--error", default="")
    p.add_argument("--blocked-by", choices=["question", "build", "owner", "package", "provider"], default="")
    p.add_argument("--blocked-ref", default="")
    p.add_argument("--ended-evidence", default="", help="existing Agent result file; release also requires its matching agent row to have ended or its known process to be absent")
    p.add_argument("--subagents", action="store_true", help="the Queen has a native subagent tool available")
    p.add_argument("--no-subagents", action="store_true", help="force separate processes")
    p.set_defaults(fn=cmd_bee)

    p = sub.add_parser("package", help="record package progress; owner: unassign its stored provider")
    p.add_argument("action", nargs="?", choices=["unassign"])
    for field in ("project", "plan", "package"):
        p.add_argument("--" + field, required=True)
    p.add_argument("--state", choices=["working", "waiting", "implemented", "done", "rejected"])
    p.add_argument("--reason", default="")
    p.set_defaults(fn=cmd_package)

    p = sub.add_parser("plan", help="check or control a plan", description=(
        "The TOML header requires branch (the plan branch) and worktree (the plan worktree folder). "
        "The Queen writes both at the cut; HaiveQueen reads them only from these header fields. "
        "Check refuses a missing branch or worktree, or a worktree that is the project's main checkout, and names the problem. "
        "The plan stays SENT until the check passes and sets it to QUEUED; agent start is refused until then. "
        "Owner controls: stop-gracefully prevents new starts while running work continues; force-stop ends the plan's running work; "
        "resume restores a stopped plan. pause remains an alias for stop-gracefully."))
    p.add_argument("action", choices=["check", "accept", "reject", "stop-gracefully", "force-stop", "pause", "resume", "release"])
    for field in ("project", "plan"):
        p.add_argument("--" + field, required=True)
    p.set_defaults(fn=cmd_plan)
    p.add_argument("--text", default="")
    p.add_argument("--package", dest="packages", action="append", default=[])
    p.add_argument("--commit", default="")

    p = sub.add_parser("provider", help="owner: enable, disable or test a provider connection")
    p.add_argument("action", choices=["enable", "disable", "test"])
    p.add_argument("--provider", required=True)
    p.set_defaults(fn=cmd_owner)

    p = sub.add_parser("integration")
    p.add_argument("action", choices=["acquire", "release"])
    p.add_argument("--project", required=True)
    p.add_argument("--plan", required=True)
    p.add_argument("--owner-pid", type=int)
    p.set_defaults(fn=cmd_integration)

    p = sub.add_parser("dequeen")
    p.add_argument("hive", metavar="plan")
    p.set_defaults(fn=cmd_dequeen)

    p = sub.add_parser("requeen")
    p.add_argument("hive", metavar="plan")
    p.add_argument("provider", nargs="?")
    p.set_defaults(fn=cmd_requeen)

    p = sub.add_parser("done")
    p.add_argument("--agent", type=int, required=True)
    p.add_argument("--result", required=True)
    p.set_defaults(fn=cmd_done)

    p = sub.add_parser("ready", description="Report the plan branch ready for integration; the Queen ends after it. The Orchestrator then merges, checks, builds, pushes and deploys the plan (Compile check command, Build command, Deploy command); a red step starts a fix agent. A plan with nothing left to integrate (no plan branch, or every commit already in the integration branch) writes no queue row, needs no compile check, build or deploy, and closes after the fresh check and the owner check.")
    for a in ("project", "branch", "tip", "plan"):
        p.add_argument(f"--{a}", required=True)
    p.add_argument("--note", default="")
    p.set_defaults(fn=cmd_ready)

    p = sub.add_parser("compile-check")
    p.add_argument("--project", required=True)
    p.add_argument("--plan", required=True)
    p.add_argument("--worktree", default=None)
    p.set_defaults(fn=cmd_compile_check)

    p = sub.add_parser("ask")
    for a in ("project", "plan", "item", "question"):
        p.add_argument(f"--{a}", required=True)
    p.add_argument("--recommendation", default="")
    p.set_defaults(fn=cmd_ask)

    p = sub.add_parser("note")
    p.add_argument("--project", required=True)
    p.add_argument("--text", required=True)
    p.set_defaults(fn=cmd_note)

    p = sub.add_parser("status")
    p.add_argument("--project")
    p.add_argument("--json", action="store_true")
    p.set_defaults(fn=cmd_status)

    p = sub.add_parser("questions")
    p.add_argument("--open", action="store_true")
    p.add_argument("--project")
    p.add_argument("--status")
    p.add_argument("--plan")
    p.add_argument("--item")
    p.set_defaults(fn=cmd_questions)

    p = sub.add_parser("answer")
    p.add_argument("id", type=int)
    p.add_argument("text")
    p.add_argument("--by", choices=["owner", "coordinator"], required=True)
    p.set_defaults(fn=cmd_answer)

    p = sub.add_parser("queue")
    p.add_argument("action", nargs="?", choices=["list", "upsert"], default="list")
    p.add_argument("--project")
    for a in ("branch", "tip", "plan", "status", "note", "run"):
        p.add_argument(f"--{a}")
    p.set_defaults(fn=cmd_queue)

    p = sub.add_parser("views")
    p.add_argument("--project", required=True)
    p.set_defaults(fn=cmd_views)

    p = sub.add_parser("export")
    p.add_argument("--force", action="store_true")
    p.set_defaults(fn=cmd_export)

    for name, fn in (("init", cmd_init), ("register", cmd_register)):
        p = sub.add_parser(name)
        p.add_argument("folder")
        p.set_defaults(fn=fn)

    runtime_cli.register(sub)
    legacy.register(sub)
    loop.register(sub)
    from haivequeen import web_cli
    web_cli.register(sub)
    selfupdate.register(sub)
    updates.register(sub)
    from haivequeen import worktrees
    worktrees.register(sub)

    for name in STUBS:
        p = sub.add_parser(name)
        p.set_defaults(fn=make_stub(name))
    return ap


# Run the CLI and optionally keep a shortcut's result visible on the desktop.
def main(argv=None) -> int:
    """Entry point: parse arguments, open the database, run the command."""
    for stream in (sys.stdout, sys.stdin):
        if hasattr(stream, "reconfigure"):
            stream.reconfigure(encoding="utf-8")
    args, extra = build_parser().parse_known_args(argv)
    if extra and args.command not in STUBS:
        return fail("unexpected arguments: " + " ".join(extra), 2)
    if args.command in STUBS:
        return args.fn(args, None)
    conn = None
    desktop = getattr(args, "desktop", False)
    try:
        conn = db.connect(config.db_path())
        agent = os.environ.get("BZZ_AGENT")
        row = conn.execute("SELECT * FROM agents WHERE name=? ORDER BY id DESC LIMIT 1", (agent,)).fetchone() if agent else None
        queen = row and dict(row).get("phase") == "queen" and not row["archived"] and row["status"] in ("running", "starting")
        if queen:
            output = io.StringIO()
            with contextlib.redirect_stdout(output):
                code = args.fn(args, conn)
            raw = output.getvalue()
            messages = runtime.take_owner_messages(conn, agent)
            if messages:
                try:
                    result = json.loads(raw)
                except ValueError:
                    result = raw
                result = {**result, "owner_messages": messages} if isinstance(result, dict) else {
                    "result": result, "owner_messages": messages}
                print(json.dumps(result, ensure_ascii=False))
            else:
                print(raw, end="")
        else:
            code = args.fn(args, conn)
    except Exception as exc:
        if not desktop:
            raise
        args.desktop_result = {"ok": False, "error": str(exc)}
        code = fail(str(exc))
    finally:
        if conn is not None:
            conn.close()
    if desktop:
        from haivequeen.desktop import show_result
        show_result(args.command, args.desktop_result)
    return code


if __name__ == "__main__":
    sys.exit(main())

"""HaiveQueen command line: python haivequeen/cli.py <command>. Machine-readable output is JSON on stdout."""
import argparse
import json
import os
import sys
from datetime import datetime
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from haivequeen import config, db, legacy, loop, runtime, runtime_cli, selfupdate  # noqa: E402
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
    out({"ok": True, "agent": args.agent, "project": row["project"]})
    return 0


# Compare a Hive plan with a relative or absolute plan path.
def _plan_matches(conn, project, stored, supplied):
    """Compare a Hive plan with a relative or absolute plan path."""
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
    """Register a branch as ready; a Queen may register only her own Hive plan."""
    agent = os.environ.get("BZZ_AGENT")
    if agent:
        from haivequeen import hives, routine, scheduler as sch
        row = runtime.get_agent(conn, args.project, agent)
        if row and row.get("phase") == "queen":
            plan = row.get("plan_path")
            if not _plan_matches(conn, args.project, plan, args.plan) or hives.get(conn, args.project, plan) is None:
                return fail("ready refused: branch does not belong to this Hive")
            args.plan = plan
        else:
            env = next((e for e in sch.load_projects(conn, config.load_config()) if e.name == args.project), None)
            why = routine.ready_refusal(env, agent) if env is not None else ""
            if why:
                return fail("ready refused: " + why)
    res = db.add_ready(conn, args.project, args.branch, args.tip, args.plan, args.note)
    if agent and row and row.get("phase") == "queen":
        conn.execute("UPDATE hives SET ready_id=? WHERE project=? AND plan_path=?", (res["id"], args.project, args.plan))
    out({"ok": True, **res})
    return 0


# File a question for the owner.
def cmd_ask(args, conn) -> int:
    """File a question for the owner."""
    from haivequeen import triage
    open_questions = runtime.list_questions(conn, args.project, status="open", plan=args.plan, item=args.item)
    if open_questions:
        out({"ok": True, "id": open_questions[-1]["id"], "status": "open"})
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
    out({"ok": True, "files": init_mod.init_project(args.folder)})
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


# Acquire or release the shared project integration place.
def cmd_integration(args, conn):
    """Acquire or release the shared project integration place."""
    from haivequeen import hives
    agent = os.environ.get("BZZ_AGENT")
    row = runtime.get_agent(conn, args.project, agent) if agent else None
    if agent and (not row or row.get("phase") != "queen" or not _plan_matches(conn, args.project, row.get("plan_path"), args.plan)):
        return fail("integration refused: not this Hive's Queen")
    if row:
        args.plan = row['plan_path']
    if args.action == "release":
        hives.release_integration(conn, args.project, args.plan)
        out({"ok": True})
        return 0
    owner_pid = (row.get("pid") if row else None) or getattr(args, "owner_pid", None)
    if not owner_pid or int(owner_pid) <= 0:
        return fail("external integration requires the window process ID via --owner-pid")
    from haivequeen import buildjobs, scheduler
    env = next((e for e in scheduler.load_projects(conn, config.load_config()) if e.name == args.project), None)
    job = buildjobs.plan_build(env) if env else None
    if not hives.take_integration(conn, args.project, args.plan, owner_pid=owner_pid, build_jobs=job["Jobs"] if job else None):
        if row:
            conn.execute("UPDATE hives SET state='integration_wait' WHERE project=? AND plan_path=?", (args.project, args.plan))
        return fail("waiting for integration")
    out({"ok": True, "buildJobs": job})
    return 0


# Resolve an owner-provided Hive name to exactly one project and plan.
def _resolve_hive(conn, name):
    """Resolve an owner-provided Hive name to exactly one project and plan."""
    rows = db.rows(conn, "SELECT project,plan_path FROM hives WHERE plan_path=? OR project || ':' || plan_path=?", (name, name))
    if len(rows) != 1:
        raise ValueError("Hive not found or ambiguous")
    return rows[0]


# End the Queen and separate Bees of the chosen Hive.
def cmd_dequeen(args, conn):
    """End the Queen and separate Bees of the chosen Hive."""
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
        return fail(str(exc) or 'Apiary not found')
    out({'ok': True})
    return 0


# Run a Bee or record its package attempt for the calling Queen.
def cmd_bee(args, conn):
    """Route and start a Bee, or record an attempt for escalation."""
    from haivequeen import bees, scheduler
    agent = os.environ.get("BZZ_AGENT")
    queen = runtime.get_agent(conn, args.project, agent) if agent else None
    if not queen or queen.get("phase") != "queen" or queen.get("status") not in ("running", "starting") or not _plan_matches(conn, args.project, queen.get("plan_path"), args.plan):
        return fail("bee command requires the Queen of this Hive")
    env = next((e for e in scheduler.load_projects(conn, config.load_config()) if e.name == args.project), None)
    if not env:
        return fail("unknown Apiary")
    plan = queen["plan_path"]
    if args.action == "wait":
        provider = scheduler.get_provider(env.providers, args.provider)
        if not provider or provider['name'] in env.settings.get('excludedProviders', []):
            return fail("provider is unavailable or excluded")
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
        bees.report_attempt(env, plan, args.package, args.cls, args.outcome, args.attempt)
        next_cls = bees.next_attempt(conn, args.project, plan, args.package, args.cls, bees.escalation_providers(env, plan, args.package),
                                     env.settings.get("excludedProviders", []), int(env.settings.get("escalateAfter", 3)))
        out({"ok": True, "failures": bees.failure_count(conn, args.project, plan, args.package, args.cls), "nextClass": next_cls})
        return 0
    if not args.name or not args.task or not args.cwd:
        return fail("start requires a Bee name, task and worktree")
    try:
        steps = json.loads(args.steps)
        paths = json.loads(args.paths)
        rules = json.loads(args.rules)
        if not all(isinstance(x, list) and all(isinstance(v, str) for v in x) for x in (steps, paths, rules)) or not steps:
            return fail("steps, paths and rules must be string lists and steps must not be empty")
        started = bees.start_process(env, plan, args.package, args.name, args.cls, args.task, steps, paths, rules, cwd=args.cwd, no_subagents=args.no_subagents, parallel=args.parallel)
    except (ValueError, TypeError) as exc:
        return fail(str(exc))
    if isinstance(started, dict):
        out({"ok": True, "started": False, **started})
        return 0
    bees.ensure_attempts(conn)
    assignment = conn.execute("SELECT provider FROM bee_assignments WHERE project=? AND plan_path=? AND package=?", (args.project, plan, args.package)).fetchone() if not started else None
    out({"ok": bool(started), "started": bool(started), "kind": "process" if started else "waiting", "provider": assignment[0] if assignment else None})
    return 0 if started else 1


# Build the argument parser with all commands.
def build_parser() -> argparse.ArgumentParser:
    """Build the argument parser with all commands."""
    ap = argparse.ArgumentParser(prog="bzz")
    sub = ap.add_subparsers(dest="command", required=True)

    p = sub.add_parser("bee")
    p.add_argument("action", choices=["start", "result", "wait"])
    for field in ("project", "plan", "package", "class"):
        p.add_argument("--" + field, required=True, dest="cls" if field == "class" else field)
    p.add_argument("--name", default="")
    p.add_argument("--provider", default="")
    p.add_argument("--task", default="")
    p.add_argument("--steps", default="[]")
    p.add_argument("--paths", default="[]")
    p.add_argument("--rules", default="[]")
    p.add_argument("--cwd", default="")
    p.add_argument("--outcome", choices=["failed", "interrupted", "succeeded"])
    p.add_argument("--attempt", default="")
    p.add_argument("--no-subagents", action="store_true")
    p.add_argument("--parallel", action="store_true")
    p.set_defaults(fn=cmd_bee)

    p = sub.add_parser("integration")
    p.add_argument("action", choices=["acquire", "release"])
    p.add_argument("--project", required=True)
    p.add_argument("--plan", required=True)
    p.add_argument("--owner-pid", type=int)
    p.set_defaults(fn=cmd_integration)

    p = sub.add_parser("dequeen")
    p.add_argument("hive")
    p.set_defaults(fn=cmd_dequeen)

    p = sub.add_parser("requeen")
    p.add_argument("hive")
    p.add_argument("provider", nargs="?")
    p.set_defaults(fn=cmd_requeen)

    p = sub.add_parser("done")
    p.add_argument("--agent", type=int, required=True)
    p.add_argument("--result", required=True)
    p.set_defaults(fn=cmd_done)

    p = sub.add_parser("ready")
    for a in ("project", "branch", "tip", "plan"):
        p.add_argument(f"--{a}", required=True)
    p.add_argument("--note", default="")
    p.set_defaults(fn=cmd_ready)

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
    selfupdate.register(sub)

    for name in STUBS:
        p = sub.add_parser(name)
        p.set_defaults(fn=make_stub(name))
    return ap


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
    conn = db.connect(config.db_path())
    try:
        return args.fn(args, conn)
    finally:
        conn.close()


if __name__ == "__main__":
    sys.exit(main())

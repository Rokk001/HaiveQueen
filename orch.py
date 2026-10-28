"""agent-orchestrator command line: python orch.py <command>. Machine-readable output is JSON on stdout."""
import argparse
import json
import sys
from datetime import datetime
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from orchlib import config, db, legacy, loop, runtime, runtime_cli  # noqa: E402
from orchlib import export as export_mod, init as init_mod, views  # noqa: E402

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


def cmd_ready(args, conn) -> int:
    """Register a branch as ready in the integration queue."""
    res = db.add_ready(conn, args.project, args.branch, args.tip, args.plan, args.note)
    out({"ok": True, **res})
    return 0


def cmd_ask(args, conn) -> int:
    """File a question for the owner."""
    qid = db.add_question(conn, args.project, args.plan, args.item, args.question, args.recommendation)
    out({"ok": True, "id": qid})
    return 0


def cmd_note(args, conn) -> int:
    """Write a free-text note into the event history."""
    with db.transaction(conn):
        eid = db.add_event(conn, args.project, "note", args.text)
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


def cmd_queue(args, conn) -> int:
    """List the integration queue as JSON, or (action upsert) change or add the row of a branch."""
    if args.action == "upsert":
        if not (args.project and args.branch):
            return fail("queue upsert needs --project and --branch", 2)
        return runtime_cli.run(lambda a, c: runtime.queue_upsert(
            c, a.project, a.branch, a.tip, a.plan, a.status, a.note, a.run))(args, conn)
    out(db.queue_rows(conn, args.project))
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


def build_parser() -> argparse.ArgumentParser:
    """Build the argument parser with all commands."""
    ap = argparse.ArgumentParser(prog="orch")
    sub = ap.add_subparsers(dest="command", required=True)

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

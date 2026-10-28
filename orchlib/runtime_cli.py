"""Command line parsers and handlers of the runtime commands (see docs/RUNTIME-API.md). Handlers return a JSON-able object."""
import json
import sqlite3
import sys

from . import db, runtime
from .runtime import CmdError

COMMANDS = ("agents", "event", "events", "kv", "handled", "nofollow", "build", "usage", "plans", "packages",
            "decisions")


def text_arg(value):
    """Return the argument text; a single '-' reads the text from stdin (for long or awkward values)."""
    return sys.stdin.read() if value == "-" else value


def json_arg(value):
    """Parse a JSON argument (a single '-' reads stdin); raises CmdError on invalid JSON."""
    try:
        return json.loads(text_arg(value))
    except ValueError as exc:
        raise CmdError(f"invalid JSON: {exc}")


def run(handler):
    """Wrap a handler: print its result as JSON, turn CmdError and database errors into a JSON error with exit 1."""
    def wrapped(args, conn) -> int:
        """Run the handler and print the result."""
        try:
            result = handler(args, conn)
        except (CmdError, KeyError, ValueError, sqlite3.Error) as exc:
            print(json.dumps({"ok": False, "error": str(exc.args[0] if isinstance(exc, KeyError) else exc)},
                             ensure_ascii=False))
            return 1
        print(json.dumps(result, ensure_ascii=False))
        return 0
    return wrapped


def actions(sub, name, table):
    """Add a command with sub-actions; table maps action -> (handler, [(flag, kwargs), ...])."""
    p = sub.add_parser(name)
    asub = p.add_subparsers(dest="action", required=True)
    for action, (handler, flags) in table.items():
        ap = asub.add_parser(action)
        for flag, kw in flags:
            ap.add_argument(flag, **kw)
        ap.set_defaults(fn=run(handler))
    return p


P = ("--project", {"required": True})
PO = ("--project", {})


def h_agents_list(a, c):
    """agents list: rows of one or all projects."""
    return runtime.list_agents(c, a.project, a.scope, a.status)


def h_agents_get(a, c):
    """agents get: one agent by name (null if unknown)."""
    return runtime.get_agent(c, a.project, a.name)


def h_agents_upsert(a, c):
    """agents upsert: create or update an agent row from a JSON object."""
    return runtime.upsert_agent(c, a.project, a.name, json_arg(a.fields))


def h_agents_update(a, c):
    """agents update-fields: like upsert, but the agent must exist."""
    return runtime.upsert_agent(c, a.project, a.name, json_arg(a.fields), create=False)


def h_agents_archive(a, c):
    """agents archive: move old rows (or rows matching a regex) to the archive."""
    return {"ok": True, "moved": runtime.archive_agents(c, a.project, a.hours, a.match)}


def h_event(a, c):
    """event append: history row plus text mirror line."""
    data = json_arg(a.data) if a.data else None
    res = runtime.append_event(c, a.project, text_arg(a.text), a.kind, a.agent, data, a.mirror, a.error, a.log_dir)
    return {"ok": True, **res}


def h_events_list(a, c):
    """events list: events after an id."""
    return runtime.list_events(c, a.project, a.since_id, a.kind, a.limit)


def h_events_tail(a, c):
    """events tail: the newest events."""
    return runtime.list_events(c, a.project, a.since_id, a.kind, a.n, tail=True)


def h_events_paths(a, c):
    """events paths: the text mirror files of a project."""
    folder = runtime.mirror_dir(a.project, a.log_dir)
    return {name: str(folder / fname) for name, fname in runtime.MIRRORS.items()}


def h_kv_get(a, c):
    """kv get: value or found=false."""
    v = runtime.kv_get(c, a.project, a.key)
    return {"found": v is not None, "value": v}


def h_kv_set(a, c):
    """kv set: store a text value."""
    runtime.kv_set(c, a.project, a.key, text_arg(a.value))
    return {"ok": True}


def h_kv_delete(a, c):
    """kv delete: remove a key."""
    return {"ok": True, "deleted": runtime.kv_delete(c, a.project, a.key)}


def h_kv_list(a, c):
    """kv list: all pairs with a key prefix."""
    return runtime.kv_list(c, a.project, a.prefix)


def h_handled_check(a, c):
    """handled check: is the key handled (and its value)."""
    r = runtime.handled_get(c, a.project, a.key)
    return {"handled": r is not None, "value": r and r["value"], "ts": r and r["ts"]}


def h_handled_add(a, c):
    """handled add: remember a key (optional value, e.g. a retry counter)."""
    runtime.handled_add(c, a.project, a.key, a.value)
    return {"ok": True}


def h_handled_remove(a, c):
    """handled remove: forget a key."""
    return {"ok": True, "deleted": runtime.handled_remove(c, a.project, a.key)}


def h_handled_list(a, c):
    """handled list: all handled keys of a project."""
    return db.rows(c, "SELECT key, ts, value FROM handled WHERE project=? ORDER BY key", (a.project,))


def h_nofollow_list(a, c):
    """nofollow list: frozen keys."""
    return [r[0] for r in c.execute("SELECT key FROM nofollow WHERE project=? ORDER BY key", (a.project,))]


def h_nofollow_add(a, c):
    """nofollow add: freeze a key."""
    runtime.nofollow_add(c, a.project, a.key)
    return {"ok": True}


def h_nofollow_remove(a, c):
    """nofollow remove: unfreeze a key."""
    return {"ok": True, "deleted": runtime.nofollow_remove(c, a.project, a.key)}


def h_build_add(a, c):
    """build add: store a build measurement."""
    runtime.build_add(c, a.project, a.host or runtime.host_name(), a.jobs, a.free_start_mb, a.free_min_mb,
                      a.per_job_mb, a.duration_s)
    return {"ok": True}


def h_build_max(a, c):
    """build max-per-job: highest measured MB per job."""
    return {"per_job_mb": runtime.build_max_per_job(c, a.project, a.host or runtime.host_name())}


def h_usage_add(a, c):
    """usage add: store a usage row."""
    f = {k: v for k, v in vars(a).items() if k not in ("command", "action", "fn")}
    runtime.usage_add(c, **f)
    return {"ok": True}


def h_usage_list(a, c):
    """usage list: usage rows."""
    return runtime.usage_list(c, a.kind, a.project, a.since, a.limit)


def h_plans_sync(a, c):
    """plans sync: read the plan files into the tables and return the startable packages."""
    done = [x for x in (a.done or "").split(",") if x]
    return runtime.sync_plans(c, a.project, a.workspace, done)


def h_plans_list(a, c):
    """plans list: plan rows."""
    return runtime.list_plans(c, a.project)


def h_plans_status(a, c):
    """plans set-status: change a plan status in the table and the plan file."""
    return {"ok": True, **runtime.set_plan_status(c, a.project, a.plan, a.status, a.workspace)}


def h_packages_list(a, c):
    """packages list: package rows."""
    return runtime.list_packages(c, a.project, a.plan)


def h_packages_set(a, c):
    """packages set: change phase, lock flag or lock reason."""
    return runtime.set_package(c, a.project, a.plan, a.id, a.phase, a.locked, a.reason)


def h_queue_list(a, c):
    """queue list: queue rows."""
    return db.queue_rows(c, a.project)


def h_queue_upsert(a, c):
    """queue upsert: change or add the queue row of a branch."""
    return runtime.queue_upsert(c, a.project, a.branch, a.tip, a.plan, a.status, a.note, a.run)


def h_questions(a, c):
    """questions: filtered question list."""
    return runtime.list_questions(c, a.project, "open" if a.open else a.status, a.plan, a.item)


def h_decisions_list(a, c):
    """decisions list: decisions."""
    return runtime.list_decisions(c, a.project, a.plan, a.item)


def h_decisions_add(a, c):
    """decisions add: record a decision."""
    return {"ok": True, "id": runtime.add_decision(c, a.project, a.source, text_arg(a.text), a.plan, a.item)}


def intflag(v):
    """Parse 0/1/true/false."""
    return str(v).lower() in ("1", "true", "yes")


def register(sub) -> None:
    """Add the runtime commands to the sub-parser collection of orch.py."""
    actions(sub, "agents", {
        "list": (h_agents_list, [PO, ("--scope", {"choices": ["active", "archive", "all"], "default": "active"}),
                                 ("--status", {})]),
        "get": (h_agents_get, [P, ("--name", {"required": True})]),
        "upsert": (h_agents_upsert, [P, ("--name", {"required": True}), ("--fields", {"required": True})]),
        "update-fields": (h_agents_update, [P, ("--name", {"required": True}), ("--fields", {"required": True})]),
        "archive": (h_agents_archive, [P, ("--hours", {"type": float, "default": 12.0}), ("--match", {})]),
    })
    actions(sub, "event", {
        "append": (h_event, [P, ("--text", {"required": True}), ("--kind", {}), ("--agent", {}), ("--data", {}),
                             ("--mirror", {"choices": ["rounds", "events", "both", "none"], "default": "rounds"}),
                             ("--error", {"action": "store_true"}), ("--log-dir", {})]),
    })
    ev = [PO, ("--since-id", {"type": int, "default": 0}), ("--kind", {})]
    actions(sub, "events", {
        "list": (h_events_list, ev + [("--limit", {"type": int})]),
        "tail": (h_events_tail, ev + [("-n", {"type": int, "default": 20, "dest": "n"})]),
        "paths": (h_events_paths, [P, ("--log-dir", {})]),
    })
    actions(sub, "kv", {
        "get": (h_kv_get, [P, ("--key", {"required": True})]),
        "set": (h_kv_set, [P, ("--key", {"required": True}), ("--value", {"required": True})]),
        "delete": (h_kv_delete, [P, ("--key", {"required": True})]),
        "list": (h_kv_list, [P, ("--prefix", {"default": ""})]),
    })
    K = ("--key", {"required": True})
    actions(sub, "handled", {
        "check": (h_handled_check, [P, K]),
        "add": (h_handled_add, [P, K, ("--value", {})]),
        "remove": (h_handled_remove, [P, K]),
        "list": (h_handled_list, [P]),
    })
    actions(sub, "nofollow", {
        "list": (h_nofollow_list, [P]), "add": (h_nofollow_add, [P, K]), "remove": (h_nofollow_remove, [P, K]),
    })
    HOST = ("--host", {})
    actions(sub, "build", {
        "add": (h_build_add, [P, HOST, ("--jobs", {"type": int, "required": True}),
                              ("--free-start-mb", {"type": float}), ("--free-min-mb", {"type": float}),
                              ("--per-job-mb", {"type": float}), ("--duration-s", {"type": float})]),
        "max-per-job": (h_build_max, [P, HOST]),
    })
    actions(sub, "usage", {
        "add": (h_usage_add, [("--provider", {}), ("--model", {}), ("--tokens-in", {"type": int}),
                              ("--tokens-out", {"type": int}), ("--kind", {}), PO, ("--trigger", {}),
                              ("--context", {"type": int}), ("--duration-s", {"type": float}), ("--actions", {}),
                              ("--error", {"type": int}), ("--ts", {})]),
        "list": (h_usage_list, [("--kind", {}), PO, ("--since", {}), ("--limit", {"type": int})]),
    })
    WS = ("--workspace", {})
    PL = ("--plan", {"required": True})
    actions(sub, "plans", {
        "sync": (h_plans_sync, [P, WS, ("--done", {})]),
        "list": (h_plans_list, [PO]),
        "set-status": (h_plans_status, [P, PL, ("--status", {"required": True}), WS]),
    })
    actions(sub, "packages", {
        "list": (h_packages_list, [PO, ("--plan", {})]),
        "set": (h_packages_set, [P, PL, ("--id", {"required": True}), ("--phase", {}),
                                 ("--locked", {"type": intflag}), ("--reason", {})]),
    })
    actions(sub, "decisions", {
        "list": (h_decisions_list, [PO, ("--plan", {}), ("--item", {})]),
        "add": (h_decisions_add, [P, ("--source", {"required": True}), ("--text", {"required": True}),
                                  ("--plan", {}), ("--item", {})]),
    })
    register_runstate(sub)


def register_runstate(sub) -> None:
    """Add pause, resume, drain, run-state, starts-allowed and reattach (run state and restart, D8)."""
    from . import reattach, runstate

    def allowed(a, c):
        """starts-allowed: may the round start an agent now."""
        return {"allowed": runstate.starts_allowed(c), "state": runstate.get_state(c)["state"]}

    def re_attach(a, c):
        """reattach: fail running agents whose PID is not in the given list."""
        alive = {int(x) for x in (a.alive_pids or "").split(",") if x.strip()}
        return reattach.reattach(c, a.project, lambda pid: pid in alive)

    for name, fn, flags in (
            ("pause", lambda a, c: runstate.pause(c), []),
            ("resume", lambda a, c: runstate.resume(c), []),
            ("drain", lambda a, c: runstate.drain(c), []),
            ("run-state", lambda a, c: runstate.get_state(c), []),
            ("starts-allowed", allowed, []),
            ("reattach", re_attach, [P, ("--alive-pids", {"default": ""})])):
        p = sub.add_parser(name)
        for flag, kw in flags:
            p.add_argument(flag, **kw)
        p.set_defaults(fn=run(fn))

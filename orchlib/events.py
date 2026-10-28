"""Event hook: collects the events of one round and calls the project's onEvent command once with them.

Events are plan status changes, finished work, passed verifies, released packages, ready and integrated queue rows, questions asked or
answered. No event, no call. A failed call is logged in the events log and repeated in the next round with all events collected so far;
it never stops the loop and costs no tokens. The hook state (seen keys and pending events) lives in the kv key `event-sync`.
"""
import json
import subprocess
from datetime import datetime
from pathlib import Path

from . import runtime

STATE_KEY = "event-sync"


def run_command(argv: list, work_dir, timeout: int) -> dict:
    """Run a program with UTF-8 output and a timeout; returns {ExitCode, StdOut, StdErr, TimedOut} (a missing program gives exit 127)."""
    try:
        r = subprocess.run(argv, cwd=str(work_dir), stdin=subprocess.DEVNULL, capture_output=True, timeout=timeout)
        return {"ExitCode": r.returncode, "StdOut": r.stdout.decode("utf-8", "replace"),
                "StdErr": r.stderr.decode("utf-8", "replace"), "TimedOut": False}
    except subprocess.TimeoutExpired as exc:
        return {"ExitCode": -1, "StdOut": "", "StdErr": (exc.stderr or b"").decode("utf-8", "replace"), "TimedOut": True}
    except OSError as exc:
        return {"ExitCode": 127, "StdOut": "", "StdErr": str(exc), "TimedOut": False}


def current_event_keys(env, plans: list, queue_rows: list, verify_zero, questions: list) -> dict:
    """Current event candidates as an ordered dict key -> text (the state, not the change: new keys since the last round are the events).

    `plans` are dicts with path and status, `queue_rows` dicts with branch, tip and status words (ready, integriert),
    `verify_zero(agent name)` tells whether a verify ended with 'Offene Anforderungen: 0', `questions` are dicts with id and status OPEN/ANSWERED.
    """
    cur = {}
    for pr in plans:
        path, st = str(pr["path"]), str(pr["status"])
        cur["plan|%s|%s" % (path, st)] = "plan %s %s" % (Path(path).stem, st)
    for a in runtime.list_agents(env.conn, env.name, "all"):
        n = a.get("name")
        if a.get("status") != "done" or not n:
            continue
        if a.get("phase") == "work":
            cur["work|%s" % n] = "%s finished" % n
        elif a.get("phase") == "verify" and verify_zero(n):
            cur["verified|%s" % n] = "%s passed" % n
        elif a.get("phase") == "release":
            cur["release|%s" % n] = "%s finished" % n
    for r in queue_rows:
        word = str(r["status"]).strip().split(" ")[0]
        if word == "ready":
            cur["queue|%s|%s|ready" % (r["branch"], r["tip"])] = "ready %s" % r["branch"]
        elif word == "integriert":
            cur["queue|%s|%s|integrated" % (r["branch"], r["tip"])] = "integrated %s" % r["branch"]
    for q in questions:
        if q["status"] == "OPEN":
            cur["q|%s|OPEN" % q["id"]] = "question %s asked" % q["id"]
        elif q["status"] == "ANSWERED":
            cur["q|%s|ANSWERED" % q["id"]] = "question %s answered" % q["id"]
    return cur


def invoke_hook(env, plans: list, queue_rows: list, verify_zero, questions: list, tools_dir=None) -> None:
    """Call the onEvent command of the project settings with the pending events (see the module text). The first call records the baseline only.

    The command is a list; {projectDir} (the .orchestrator folder of the workspace) and {tools} are replaced in it. Never raises.
    """
    hook = env.settings.get("onEvent") or {}
    if not hook.get("command"):
        return
    try:
        stored = runtime.kv_get(env.conn, env.name, STATE_KEY)
        baseline = stored is None
        seen, pending = {}, []
        if not baseline:
            j = json.loads(stored)
            seen = j.get("seen") or {}
            pending = [str(x) for x in (j.get("pending") or [])]
        cur = current_event_keys(env, plans, queue_rows, verify_zero, questions)
        for key, text in cur.items():
            if key not in seen:
                seen[key] = datetime.now().isoformat(timespec="seconds")
                if not baseline:
                    pending.append(str(text))
        if not pending:
            runtime.kv_set(env.conn, env.name, STATE_KEY, json.dumps({"seen": seen, "pending": []}))
            return
        runtime.kv_set(env.conn, env.name, STATE_KEY, json.dumps({"seen": seen, "pending": pending}))   # kept before the command runs
        project_dir = str(env.workspace / ".orchestrator")
        tools = str(tools_dir or Path(__file__).resolve().parent.parent)
        cmd = [str(c).replace("{projectDir}", project_dir).replace("{tools}", tools) for c in hook["command"]]
        work = str(hook.get("workDir") or "{projectDir}").replace("{projectDir}", project_dir)
        timeout = int(hook.get("timeoutSeconds") or 180)
        res = run_command(cmd + pending, work, timeout)
        if res["ExitCode"] == 0:
            env.log("event hook ok: %d event(s)" % len(pending))
            pending = []
        else:
            err = " ".join((res["StdErr"] + " " + res["StdOut"]).split())
            if res["TimedOut"]:
                err = "timeout after %d s" % timeout
            env.log("event hook failed (exit %s): %s" % (res["ExitCode"], err))
            env.event("Event command failed (again next round): " + err[:120], True)
        runtime.kv_set(env.conn, env.name, STATE_KEY, json.dumps({"seen": seen, "pending": pending}))
    except Exception as exc:   # the hook must never stop the loop
        try:
            env.log("event hook error: %s" % exc)
            env.event("Event command error: %s" % exc, True)
        except Exception:
            pass

"""Explicit owner actions shared by the CLI, Questions page and inbox."""
from . import filetxn
import re

from . import platform as osplat
from . import agent_process, bees, config, db, hives, integration, runtime, servers


def is_check(item):
    """Whether an item is a pending owner check rather than its history."""
    return item == "owner-check" or (str(item or "").startswith("owner-check-") and item != "owner-check-answered")


def incidental_question(item):
    """Technical end and stall notifications do not block owner acceptance."""
    return item in ("queen-ended", "stall") or str(item or "").upper().startswith("STALL-")


def pending_questions(conn, project, plan):
    """Include questions still being triaged or waiting for a fault decision."""
    return db.rows(conn, "SELECT * FROM questions WHERE project=? AND plan_path=? AND status IN ('open','triage','fault_check') ORDER BY id", (project, plan))


def validate(conn, action, project=None, plan=None, **fields):
    """Validate an action before changing any state."""
    if fields.get("by", "owner") not in ("owner", "coordinator"):
        raise ValueError("by must be owner or coordinator")
    if action == "provider":
        if fields.get("provider_action") not in ("enable", "disable", "test"):
            raise ValueError("provider action must be enable, disable or test")
        provider_settings(fields.get("provider"))
        return
    if action == "stop_agent":
        stoppable_agent(conn, project, fields.get("name"))
        return
    status = hives._plan_status(conn, project, plan)
    if action in ("accept", "reject"):
        questions = pending_questions(conn, project, plan)
        if status not in ("REVIEW", "PAUSED") or not any(is_check(q["item"]) for q in questions):
            raise ValueError("plan is not waiting for its owner check (status %s)" % status)
        hive = conn.execute("SELECT state, ready_id FROM hives WHERE project=? AND plan_path=?", (project, plan)).fetchone()
        latest = conn.execute("SELECT id, status FROM queue WHERE project=? AND plan_path=? ORDER BY id DESC LIMIT 1", (project, plan)).fetchone()
        report = hives.completion_report(conn, project, plan)
        if hive and hive["state"] == "owner_check" and report and report["state"] == "passed":
            pass   # a plan with nothing left to integrate has no integrated queue row; its checked completion report stands in
        elif not hive or hive["state"] != "owner_check" or not latest or latest["status"] != "integrated" or (hive["ready_id"] is not None and hive["ready_id"] != latest["id"]) or runtime.kv_get(conn, project, "hive-rejected-ready:" + plan) == str(latest["id"]):
            raise ValueError("plan is not waiting for its integrated owner check")
        if action == "reject":
            if not str(fields.get("text") or "").strip():
                raise ValueError("reject requires non-empty text")
            packages = fields.get("packages", [])
            if not isinstance(packages, list) or any(not isinstance(p, str) for p in packages):
                raise ValueError("packages must be a list of package ids")
            known = {p["id"] for p in runtime.list_packages(conn, project, plan)}
            if set(packages) - known:
                raise ValueError("unknown packages: " + ", ".join(sorted(set(packages) - known)))
        else:
            blockers = [str(q["item"] or q["id"]) for q in questions
                        if not is_check(q["item"]) and not incidental_question(q["item"])]
            if blockers:
                raise ValueError("still open: " + ", ".join(blockers))
    elif action == "pause":
        if status not in ("SENT", "PREPARING", "QUEUED", "RUNNING", "WAITING", "REVIEW"):
            raise ValueError("cannot pause plan in status " + status)
    elif action == "resume":
        if status != "PAUSED":
            raise ValueError("cannot resume plan in status " + status)
    elif action == "release":
        if status in ("DONE", "CANCELLED"):
            raise ValueError("cannot release plan in status " + status)
        if fields.get("commit") and not re.fullmatch(r"[a-fA-F0-9]{7,64}", fields["commit"]):
            raise ValueError("commit must be a commit id of at least 7 hexadecimal characters")
    elif action == "unassign":
        if not any(p["id"] == fields.get("package") for p in runtime.list_packages(conn, project, plan)):
            raise ValueError("unknown package " + str(fields.get("package")))
    else:
        raise ValueError("unknown owner action " + action)


EMBEDDED_REASON = "Agent %s runs as a subagent inside its Queen and has no process of its own, so it cannot be stopped alone"
STOPPED_LINE = runtime.STOPPED_LINE


def stoppable_agent(conn, project, name):
    """The agents row of the Agent `name` that the owner may stop alone; raise ValueError with the reason when it cannot be stopped."""
    name = str(name or "").strip()
    if not name:
        raise ValueError("stop requires the Agent name")
    if name in bees.open_embedded_names(conn):
        raise ValueError(EMBEDDED_REASON % name)
    row = runtime.get_agent(conn, project, name)
    if row is None or row.get("archived"):
        raise ValueError("unknown Agent " + name)
    if row.get("phase") == "queen":
        raise ValueError("%s is a Queen; a Queen is ended only with bzz dequeen" % name)
    if row.get("phase") not in ("bee", "verify", "fix"):
        raise ValueError("%s is no package, fix or ready-check Agent" % name)
    if row.get("status") not in runtime.ACTIVE_STATUSES or not agent_process.counts(row):
        raise ValueError("Agent %s is not running" % name)
    return row


def stop_agent(conn, project, name, stop=None):
    """End exactly one running package or ready-check Agent (the one action behind the dashboard button, bzz agent stop and the inbox entry).

    Its process tree is ended, its row closes as done without a result (no failure, retry or escalation is counted, no question is asked), the package of a
    package Agent stays open and stopped until the owner resumes it, a ready check counts as aborted, and the Queen of the plan gets the owner message
    'Agent <name> stopped by the owner' like any new owner decision. The Queen and the other Agents keep running."""
    row = stoppable_agent(conn, project, name)
    stop = stop or osplat.kill_process_tree
    if row.get("pid") and stop(row["pid"]) is False:
        raise ValueError("could not end Agent " + row["name"])
    if row.get("phase") == "fix":   # a fix agent of the Orchestrator: never restarted automatically, its plan waits for the owner (M-4)
        from . import orchestrator
        orchestrator.owner_stop(conn, project, row["name"])
    runtime.upsert_agent(conn, project, row["name"], {"status": "done", "pid": None, "ended_at": db.now(), "last_line": STOPPED_LINE})
    plan = row.get("plan_path") or None
    if row.get("phase") == "bee" and row.get("package") and plan:
        bees.stop_package(conn, project, plan, row["package"])
    runtime.add_decision(conn, project, "owner", "Agent %s stopped by the owner" % row["name"], plan)
    return {"ok": True, "stopped": row["name"], "package": row.get("package") or "", "plan": plan or ""}


def provider_settings(name):
    """Load the same migrated settings edited by the provider page."""
    from . import settings
    path = config.config_dir() / "config.toml"
    data = settings.load_migrated(path)
    provider = next((p for p in data.get("providers", []) if p.get("name") == name), None)
    if provider is None:
        raise ValueError("unknown provider " + str(name))
    return path, data, provider


@filetxn.coordinated
def apply(conn, action, project=None, plan=None, **fields):
    """Perform one checked owner action and return its public result."""
    validate(conn, action, project, plan, **fields)
    by = fields.get("by", "owner")
    if action == "provider":
        from . import settings
        path, data, provider = provider_settings(fields["provider"])
        operation = fields["provider_action"]
        if operation == "test":
            if servers.access_of(provider) != "llm":
                return {"ok": True, "message": "Nothing to test for this access type"}
            server = servers.get_server(data, provider["name"])
            result = servers.run_test(server)
            return {"ok": True, "result": result, "message": servers.summary(server)}
        provider["enabled"] = operation == "enable"
        settings.save_file(path, data)
        return {"ok": True, "provider": provider["name"], "enabled": provider["enabled"]}
    if action == "stop_agent":
        return stop_agent(conn, project, fields["name"])
    if action == "pause":
        hives.pause_plan(conn, project, plan)
    elif action == "resume":
        hives.start_again(conn, project, plan)
    elif action == "release":
        runtime.add_decision(conn, project, by, fields.get("commit") or "any commit", plan, "owner-release")
    elif action == "unassign":
        bees.ensure_attempts(conn)
        changed = conn.execute("UPDATE bee_assignments SET provider='' WHERE project=? AND plan_path=? AND package=? AND provider!=''",
                               (project, plan, fields["package"])).rowcount
        return {"ok": True, "message": "Provider unassigned; takes effect at next start" if changed else "No stored provider assignment (none)"}
    elif action in ("accept", "reject"):
        paused = hives._plan_status(conn, project, plan) == "PAUSED"
        for q in pending_questions(conn, project, plan):
            if is_check(q["item"]):
                text = fields.get("text") or q.get("answer") or ("Accepted" if action == "accept" else "Rejected")
                conn.execute("UPDATE questions SET status='answered',answer=?,answered_by=?,answered_at=?,owner_check_result=? WHERE id=?",
                             (text, by, db.now(), "accepted" if action == "accept" else "rejected", q["id"]))
                runtime.add_decision(conn, project, by, text, plan, "owner-check")
            elif action == "accept" and incidental_question(q["item"]):
                conn.execute("UPDATE questions SET status='answered',answer=?,answered_by=?,answered_at=? WHERE id=?",
                             ("Closed by owner acceptance", by, db.now(), q["id"]))
        if action == "accept":
            runtime.set_plan_status(conn, project, plan, "DONE")
            conn.execute("UPDATE questions SET item='owner-check-answered' WHERE project=? AND plan_path=? AND owner_check_result='accepted'", (project, plan))
            hives.finish_hive(conn, project, plan)
        else:
            conn.execute("UPDATE decisions SET status='superseded' WHERE project=? AND plan_path=? AND item='owner-release'", (project, plan))
            ready = conn.execute("SELECT id FROM queue WHERE project=? AND plan_path=? AND status='integrated' ORDER BY id DESC LIMIT 1", (project, plan)).fetchone()
            if ready:
                runtime.kv_set(conn, project, "hive-rejected-ready:" + plan, str(ready[0]))
            hives.reopen_after_owner_check(conn, project, plan)
            from . import orchestrator
            orchestrator.begin_owner_fix(conn, project, plan, fields.get("text") or "")   # a fix agent gets his answer; no Queen starts
            for package in fields.get("packages", []):
                bees.set_package_state(conn, project, plan, package, "rejected", by)
            if paused:
                runtime.kv_set(conn, project, "plan-prepause:" + plan, "RUNNING")
            else:
                runtime.set_plan_status(conn, project, plan, "RUNNING")
    db.add_event(conn, project, "note", "Owner action: " + action, data={"plan": plan, "by": by})
    return {"ok": True, "plan": plan, "action": action}

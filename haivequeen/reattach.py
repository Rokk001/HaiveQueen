"""Re-attach after an orchestrator restart (D8): running agents with a living process stay, others fail."""
from . import db, agent_process

REASON = "process gone after orchestrator restart"


def reattach(conn, project, pid_alive) -> dict:
    """Check running agent rows of a project; pid_alive(pid) -> bool. Returns {kept, failed} (agent names)."""
    kept, failed = [], []
    with db.transaction(conn):
        for r in conn.execute("SELECT * FROM agents WHERE project=? AND archived=0 AND (status='running' OR (status='starting' AND phase IN ('queen','bee'))) "
                              "ORDER BY id", (project,)).fetchall():
            label = r["name"] or str(r["id"])
            if agent_process.is_live(r, pid_alive):
                kept.append(label)
                continue
            if r["pid"] and agent_process.pid_reused(r):
                conn.execute("UPDATE agents SET status='failed', ended_at=?, last_line=?, pid=NULL, extra_json=json_set(COALESCE(extra_json, '{}'), '$.interrupted', 'vanished', '$.previousPid', ?) WHERE id=?",
                             (db.now(), REASON, r["pid"], r["id"]))
            else:
                conn.execute("UPDATE agents SET status='failed', ended_at=?, last_line=?, extra_json=json_set(COALESCE(extra_json, '{}'), '$.interrupted', 'vanished') WHERE id=?",
                             (db.now(), REASON, r["id"]))
            if r['phase'] == 'bee':
                from . import bees
                foreign = bees.enforce_agent_end(conn, project, dict(r))
                if foreign:
                    conn.execute('UPDATE agents SET last_line=? WHERE id=?', ('Foreign branch commit: ' + foreign, r['id']))
            db.add_event(conn, project, "failed", REASON, agent=label, data={"reason": "reattach"})
            failed.append(label)
    return {"ok": True, "kept": kept, "failed": failed}

"""Re-attach after an orchestrator restart (D8): running agents with a living process stay, others fail."""
from . import db

REASON = "process gone after orchestrator restart"


def reattach(conn, project, pid_alive) -> dict:
    """Check running agent rows of a project; pid_alive(pid) -> bool. Returns {kept, failed} (agent names)."""
    kept, failed = [], []
    with db.transaction(conn):
        for r in conn.execute("SELECT id, name, pid FROM agents WHERE project=? AND archived=0 AND (status='running' OR (status='starting' AND phase IN ('queen','bee'))) "
                              "ORDER BY id", (project,)).fetchall():
            label = r["name"] or str(r["id"])
            if r["pid"] and pid_alive(r["pid"]):
                kept.append(label)
                continue
            conn.execute("UPDATE agents SET status='failed', ended_at=?, last_line=?, extra_json=json_set(COALESCE(extra_json, '{}'), '$.interrupted', 'vanished') WHERE id=?",
                         (db.now(), REASON, r["id"]))
            db.add_event(conn, project, "failed", REASON, agent=label, data={"reason": "reattach"})
            failed.append(label)
    return {"ok": True, "kept": kept, "failed": failed}

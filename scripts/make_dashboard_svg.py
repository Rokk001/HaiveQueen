"""Draws assets/dashboard.svg: the dashboard of the tool itself, rendered from a throw-away runtime with invented data (no screen capture).

Usage: python scripts/make_dashboard_svg.py
"""
import sys
import tempfile
from datetime import datetime, timedelta
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
from orchlib import ansi_svg, dashboard, db, runtime  # noqa: E402

NOW = datetime(2026, 10, 28, 12, 0, 0)


def iso(minutes_ago):
    """Time string a number of minutes before the fixed demo time."""
    return (NOW - timedelta(minutes=minutes_ago)).isoformat(timespec="seconds")


def main():
    """Build the demo database, render the dashboard with colours and write the SVG."""
    with tempfile.TemporaryDirectory() as tmp:
        conn = db.connect(Path(tmp) / "demo.db")
        db.upsert_project(conn, "webshop", None, "cyan", 1)
        db.upsert_project(conn, "game-engine", None, "magenta", 0)
        agents = [("webshop", "webshop-P1-work", "docs/plans/CHECKOUT-PLAN.md", "P1", "work", "claude", "claude-sonnet-5-5", "running", 12, "Ann", "4FA8FF"),
                  ("webshop", "webshop-P2-work", "docs/plans/CHECKOUT-PLAN.md", "P2", "work", "codex", "gpt-6.1-sol", "running", 7, "Ben", "C77DFF"),
                  ("game-engine", "engine-R3-verify", "docs/plans/RENDER-PLAN.md", "R3", "verify", "codex", "gpt-6.1-sol", "running", 3, "Clara", "FF6FB5"),
                  ("game-engine", "engine-R2-work", "docs/plans/RENDER-PLAN.md", "R2", "work", "claude", "claude-sonnet-5-5", "done", 40, "Leo", "3DDCDC")]
        for proj, name, plan, pkg, phase, prov, model, status, started, person, color in agents:
            row = {"plan": plan, "package": pkg, "phase": phase, "provider": prov, "model": model, "effort": "medium", "status": status,
                   "startedAt": iso(started), "tokensIn": 90000 + started * 1000, "tokensOut": 2500, "person": person, "color": color, "buildJobs": 2}
            if status == "done":
                row["endedAt"] = iso(5)
            runtime.upsert_agent(conn, proj, name, row)
        with db.transaction(conn):
            conn.execute("INSERT INTO plans (project, path, title, status) VALUES ('webshop','docs/plans/CHECKOUT-PLAN.md','Checkout','IN_PROGRESS')")
            conn.execute("INSERT INTO plans (project, path, title, status) VALUES ('game-engine','docs/plans/RENDER-PLAN.md','Renderer','IN_PROGRESS')")
            conn.execute("INSERT INTO plans (project, path, title, status) VALUES ('game-engine','docs/plans/AUDIO-PLAN.md','Audio','READY')")
        db.add_question(conn, "game-engine", "docs/plans/RENDER-PLAN.md", "R4", "Should shadows be on by default?", "Yes, with a quality switch")
        runtime.kv_set(conn, "*", "usage-claude", '{"fiveHour":{"utilization":0.42,"resetsAt":%d},"sevenDay":{"utilization":0.61,"resetsAt":%d}}'
                       % (NOW.timestamp() + 3600, NOW.timestamp() + 86400 * 3))
        text = dashboard.render(conn, width=104, height=40, now=NOW, cfg={"buildJobs": 1},
                                sysinfo={"cores": 12, "free_mb": 9216, "total_mb": 16384, "host": "dev-pc", "compiler_count": None}, color=True)
        conn.close()
    out = ROOT / "assets" / "dashboard.svg"
    out.write_text(ansi_svg.to_svg(text), encoding="utf-8")
    print("wrote", out)


if __name__ == "__main__":
    main()

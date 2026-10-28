"""Draws assets/dashboard.svg: the dashboard of the tool itself, shown by the headless terminal backend from a throw-away runtime with invented data (no screen capture).

The dashboard runs as a pane of the headless backend; its ANSI output (runs/<pane>/output.log) is converted to SVG.

Usage: python scripts/make_dashboard_svg.py
"""
import json
import os
import sys
import tempfile
import time
from datetime import datetime, timedelta
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
from haivequeen import ansi_svg, config, db, runtime  # noqa: E402
from haivequeen.terminal import get_terminal  # noqa: E402

SYSINFO = {"cores": 12, "free_mb": 9216, "total_mb": 16384, "host": "dev-pc", "compiler_count": None}
PANE_NAME = "dashboard"


def iso(now, minutes_ago):
    """Time string a number of minutes before `now`."""
    return (now - timedelta(minutes=minutes_ago)).isoformat(timespec="seconds")


def build_demo_database(now):
    """Fill the database of the throw-away runtime (found through BZZ_DATA_DIR) with invented projects, agents, plans and a question."""
    conn = db.connect(config.db_path())
    db.upsert_project(conn, "webshop", None, "cyan", 1)
    db.upsert_project(conn, "game-engine", None, "magenta", 0)
    agents = [("webshop", "webshop-P1-work", "docs/plans/CHECKOUT-PLAN.md", "P1", "work", "claude", "claude-sonnet-5-5", "running", 12, "Ada", "4FA8FF"),
              ("webshop", "webshop-P2-work", "docs/plans/CHECKOUT-PLAN.md", "P2", "work", "codex", "gpt-6.1-sol", "running", 7, "Bea", "C77DFF"),
              ("game-engine", "engine-R3-verify", "docs/plans/RENDER-PLAN.md", "R3", "verify", "codex", "gpt-6.1-sol", "running", 3, "Cleo", "FF6FB5"),
              ("game-engine", "engine-R2-work", "docs/plans/RENDER-PLAN.md", "R2", "work", "claude", "claude-sonnet-5-5", "done", 40, "Dina", "3DDCDC")]
    for proj, name, plan, pkg, phase, prov, model, status, started, person, color in agents:
        row = {"plan": plan, "package": pkg, "phase": phase, "provider": prov, "model": model, "effort": "medium", "status": status,
               "startedAt": iso(now, started), "tokensIn": 90000 + started * 1000, "tokensOut": 2500, "person": person, "color": color, "buildJobs": 2}
        if status == "done":
            row["endedAt"] = iso(now, 5)
        runtime.upsert_agent(conn, proj, name, row)
    with db.transaction(conn):
        conn.execute("INSERT INTO plans (project, path, title, status) VALUES ('webshop','docs/plans/CHECKOUT-PLAN.md','Checkout','IN_PROGRESS')")
        conn.execute("INSERT INTO plans (project, path, title, status) VALUES ('game-engine','docs/plans/RENDER-PLAN.md','Renderer','IN_PROGRESS')")
        conn.execute("INSERT INTO plans (project, path, title, status) VALUES ('game-engine','docs/plans/AUDIO-PLAN.md','Audio','READY')")
    db.add_question(conn, "game-engine", "docs/plans/RENDER-PLAN.md", "R4", "Should shadows be on by default?", "Yes, with a quality switch")
    runtime.kv_set(conn, "*", "usage-claude", '{"fiveHour":{"utilization":0.42,"resetsAt":%d},"sevenDay":{"utilization":0.61,"resetsAt":%d}}'
                   % (now.timestamp() + 3600, now.timestamp() + 86400 * 3))
    conn.close()


def main():
    """Build the demo runtime, show the dashboard once in a headless pane, convert its ANSI output and write the SVG."""
    with tempfile.TemporaryDirectory() as tmp:
        os.environ["BZZ_DATA_DIR"] = str(Path(tmp) / "data")
        os.environ["BZZ_CONFIG_DIR"] = str(Path(tmp) / "config")
        os.environ["BZZ_DASH_WIDTH"] = "104"
        os.environ["BZZ_DASH_HEIGHT"] = "40"
        os.environ["BZZ_DASH_SYSINFO"] = json.dumps(SYSINFO)
        build_demo_database(datetime.now())
        terminal = get_terminal("headless", runs_dir=str(Path(tmp) / "runs"))
        pane = terminal.open_window("demo", "Dashboard", [sys.executable, "-I", str(ROOT / "haivequeen" / "dashboard.py"), "--once"], name=PANE_NAME)
        deadline = time.monotonic() + 60
        while any(p.alive for p in terminal.list_panes("demo")) and time.monotonic() < deadline:
            time.sleep(0.2)
        text = (terminal.registry.folder(PANE_NAME) / "output.log").read_text(encoding="utf-8")
    if "dashboard error" in text or not text.strip():
        raise SystemExit("dashboard pane failed (%s): %s" % (pane, text[:300]))
    out = ROOT / "assets" / "dashboard.svg"
    out.write_text(ansi_svg.to_svg(text), encoding="utf-8")
    print("wrote", out)


if __name__ == "__main__":
    main()

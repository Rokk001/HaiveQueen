"""Project setup: `bzz init` creates the project files, `bzz register` records a project."""
import json
from pathlib import Path

from . import config, db

EXAMPLES = Path(__file__).resolve().parent.parent / "examples"
CLAUDE_LINE = "Read AGENTS.md in this folder first; it holds the rules for all agents.\n"


def _write_new(path: Path, text: str, report: list) -> None:
    """Create a file with text unless it exists (an existing file is never touched)."""
    if path.exists():
        report.append({"path": str(path), "result": "exists, kept"})
        return
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text, encoding="utf-8", newline="\n")
    report.append({"path": str(path), "result": "created"})


def init_project(folder) -> list:
    """Create .haivequeen/project.toml, AGENTS.md, CLAUDE.md (only if missing) and docs/plans/; returns a report."""
    ws = Path(folder)
    ws.mkdir(parents=True, exist_ok=True)
    report = []
    toml = (EXAMPLES / "project.example.toml").read_text(encoding="utf-8").replace(
        'name = "example"', "name = " + json.dumps(ws.resolve().name), 1)
    _write_new(ws / ".haivequeen" / "project.toml", toml, report)
    _write_new(ws / "AGENTS.md", (EXAMPLES / "AGENTS.example.md").read_text(encoding="utf-8"), report)
    _write_new(ws / "CLAUDE.md", CLAUDE_LINE, report)
    plans = ws / "docs" / "plans"
    if plans.is_dir():
        report.append({"path": str(plans), "result": "exists, kept"})
    else:
        plans.mkdir(parents=True)
        report.append({"path": str(plans), "result": "created"})
    return report


def register_project(conn, folder) -> dict:
    """Add the project to config.toml (registered projects) and to the projects table; safe to repeat."""
    ws = Path(folder).resolve()
    proj = config.load_project(ws)
    name = proj["name"]
    cfg_file = config.config_dir() / "config.toml"
    listed = any(p.get("name") == name for p in config.load_config()["projects"])
    if not listed:
        cfg_file.parent.mkdir(parents=True, exist_ok=True)
        old = cfg_file.read_text(encoding="utf-8") if cfg_file.is_file() else ""
        sep = "" if not old or old.endswith("\n") else "\n"
        block = f"\n[[projects]]\nname = {json.dumps(name)}\nworkspace = {json.dumps(str(ws))}\n"
        cfg_file.write_text(old + sep + block, encoding="utf-8", newline="\n")
    db.upsert_project(conn, name, str(ws), proj["colour"], proj["priority"])
    return {"ok": True, "project": name, "workspace": str(ws), "config": "kept" if listed else "added"}

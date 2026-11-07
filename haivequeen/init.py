"""Project setup: `bzz init` creates the project files, `bzz register` records a project."""
import json
import os
import subprocess
from pathlib import Path

from . import config, db

EXAMPLES = Path(__file__).resolve().parent.parent / "examples"
CLAUDE_LINE = "Read AGENTS.md in this folder first; it holds the rules for all agents.\n"
EXAMPLE_PLAN = '''+++
status = "DRAFT"

[[packages]]
id = "P1"
title = "Implement the example"
class = "medium"
depends_on = []
requirements = ["E-1", "E-2"]
+++
# Example plan

Feature branch: `example`

## Requirements
- E-1: Add a greeting.
- E-2: Test the greeting.
'''


def _write_new(path: Path, text: str, report: list) -> None:
    """Create a file with text unless it exists (an existing file is never touched)."""
    if path.exists():
        report.append({"path": str(path), "result": "exists, kept"})
        return
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text, encoding="utf-8", newline="\n")
    report.append({"path": str(path), "result": "created"})


def _ignore_local_folder(ws: Path) -> list:
    """Keep machine-local project files out of Git and report tracked files."""
    ignore = ws / ".gitignore"
    old = ignore.read_bytes() if ignore.exists() else b""
    messages = []
    if b".haivequeen/" not in old.splitlines():
        separator = b"" if not old or old.endswith((b"\n", b"\r")) else b"\n"
        ignore.write_bytes(old + separator + b".haivequeen/\n")
        messages.append("Added .haivequeen/ to " + str(ignore))
    try:
        tracked = subprocess.run(["git", "ls-files", "--", ".haivequeen/"], cwd=ws,
                                 capture_output=True, check=False)
        if tracked.returncode == 0 and tracked.stdout:
            messages.append("The folder " + str(ws / ".haivequeen") + " is tracked; remove it from git.")
    except FileNotFoundError:
        pass
    return messages


def init_project(folder, messages=None) -> list:
    """Create .haivequeen/project.toml, AGENTS.md, CLAUDE.md (only if missing) and docs/plans/; returns a report."""
    ws = Path(folder)
    ws.mkdir(parents=True, exist_ok=True)
    report = []
    ignore_messages = _ignore_local_folder(ws)
    if messages is not None:
        messages.extend(ignore_messages)
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
    _write_new(plans / "EXAMPLE-PLAN.md", EXAMPLE_PLAN, report)
    return report


def register_listed(conn, entries) -> list:
    """Register the projects of the settings page that have a name and an existing workspace and are not registered yet; returns their names. A registered row is never changed."""
    added = []
    known = {r["name"]: r["workspace"] for r in db.rows(conn, "SELECT name, workspace FROM projects")}
    places = {os.path.normcase(str(Path(w).resolve())) for w in known.values() if w}
    for entry in entries or []:
        name, workspace = str(entry.get("name") or "").strip(), str(entry.get("workspace") or "").strip()
        if not name or not workspace or name in known or not Path(workspace).is_dir():
            continue
        ws = Path(workspace).resolve()
        if os.path.normcase(str(ws)) in places:
            continue
        proj = config.load_project(ws)
        db.upsert_project(conn, name, str(ws), proj["colour"], proj["priority"])
        added.append(name)
    return added


def register_project(conn, folder) -> dict:
    """Add the project to config.toml (registered projects) and to the projects table; safe to repeat."""
    ws = Path(folder).resolve()
    proj = config.load_project(ws)
    messages = _ignore_local_folder(ws)
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
    return {"ok": True, "project": name, "workspace": str(ws), "config": "kept" if listed else "added", "messages": messages}

"""Daily database export with the SQLite backup API; the newest 14 exports are kept."""
import os
import re
import sqlite3
from datetime import datetime, time
from pathlib import Path

from . import config

KEEP = 14
EXPORT_TIME = time(3, 30)
NAME_RE = re.compile(r"^orchestrator-\d{4}-\d{2}-\d{2}\.db$")


def export_dir() -> Path:
    """Return the export folder from config.toml (environment variables expanded), default <data>/exports."""
    value = config.load_config().get("exportDir") or ""
    return Path(os.path.expandvars(value)) if value else config.data_dir() / "exports"


def export_path(day, folder=None) -> Path:
    """Return the export file path of a day (a date or datetime)."""
    return (Path(folder) if folder else export_dir()) / f"orchestrator-{day:%Y-%m-%d}.db"


def export_due(now: datetime, folder=None) -> bool:
    """True when 03:30 of today has passed and there is no export for today yet."""
    if now.time() < EXPORT_TIME:
        return False
    return not export_path(now, folder).exists()


def prune(folder, keep: int = KEEP) -> list:
    """Delete all but the newest `keep` exports (only files named orchestrator-YYYY-MM-DD.db); returns removed names."""
    files = sorted(p for p in Path(folder).iterdir() if p.is_file() and NAME_RE.match(p.name))
    removed = files[:-keep] if keep else files
    for p in removed:
        p.unlink()
    return [p.name for p in removed]


def run_export(now: datetime = None, folder=None, source=None) -> dict:
    """Copy the database into today's export file (atomically) and prune old exports."""
    now = now or datetime.now()
    folder = Path(folder) if folder else export_dir()
    folder.mkdir(parents=True, exist_ok=True)
    target = export_path(now, folder)
    tmp = target.with_name(target.name + ".tmp")
    if tmp.exists():
        tmp.unlink()
    src = sqlite3.connect(str(source or config.db_path()), timeout=10)
    try:
        dst = sqlite3.connect(str(tmp))
        try:
            src.backup(dst)
        finally:
            dst.close()
    finally:
        src.close()
    os.replace(tmp, target)
    return {"ok": True, "file": str(target), "removed": prune(folder)}

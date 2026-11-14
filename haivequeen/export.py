"""Daily database export with the SQLite backup API; the newest 14 exports are kept."""
import os
import re
import sqlite3
import tempfile
import threading
from contextlib import closing, contextmanager
from datetime import datetime, time
from pathlib import Path

from . import config

KEEP = 14
EXPORT_TIME = time(3, 30)
NAME_RE = re.compile(r"^(?:haivequeen|orchestrator)-\d{4}-\d{2}-\d{2}\.db$")


_publish_mutex = threading.Lock()


@contextmanager
def publication_lock(folder):
    """Serialize replace and retention across threads and export processes."""
    with _publish_mutex:
        with open(Path(folder) / '.export.lock', 'a+b') as handle:
            if os.name == 'nt':
                import msvcrt
                handle.seek(0)
                msvcrt.locking(handle.fileno(), msvcrt.LK_LOCK, 1)
            else:
                import fcntl
                fcntl.flock(handle, fcntl.LOCK_EX)
            try:
                yield
            finally:
                if os.name == 'nt':
                    handle.seek(0)
                    msvcrt.locking(handle.fileno(), msvcrt.LK_UNLCK, 1)
                else:
                    fcntl.flock(handle, fcntl.LOCK_UN)


def export_dir() -> Path:
    """Return the export folder from config.toml (environment variables expanded), default <data>/exports."""
    value = config.load_config().get("exportDir") or ""
    return Path(os.path.expandvars(value)) if value else config.data_dir() / "exports"


def export_path(day, folder=None) -> Path:
    """Return the export file path of a day (a date or datetime)."""
    return (Path(folder) if folder else export_dir()) / f"haivequeen-{day:%Y-%m-%d}.db"


def export_due(now: datetime, folder=None) -> bool:
    """True when 03:30 of today has passed and there is no export for today yet."""
    if now.time() < EXPORT_TIME:
        return False
    return not export_path(now, folder).exists()


def prune(folder, keep: int = KEEP) -> list:
    """Delete all but the newest `keep` exports (files named haivequeen-YYYY-MM-DD.db or the old orchestrator-YYYY-MM-DD.db); returns removed names."""
    files = sorted((p for p in Path(folder).iterdir() if p.is_file() and NAME_RE.match(p.name)), key=lambda p: p.name.split("-", 1)[1])   # oldest day first, both name patterns
    removed = files[:-keep] if keep else files
    for p in removed:
        p.unlink(missing_ok=True)
    return [p.name for p in removed]


def run_export(now: datetime = None, folder=None, source=None) -> dict:
    """Copy the database into today's export file (atomically) and prune old exports."""
    now = now or datetime.now()
    folder = Path(folder) if folder else export_dir()
    folder.mkdir(parents=True, exist_ok=True)
    target = export_path(now, folder)
    fd, name = tempfile.mkstemp(prefix=target.name + ".", suffix=".tmp", dir=folder)
    os.close(fd)
    tmp = Path(name)
    try:
        with closing(sqlite3.connect(str(source or config.db_path()), timeout=10)) as src:
            with closing(sqlite3.connect(str(tmp))) as dst:
                src.backup(dst)
        with publication_lock(folder):
            os.replace(tmp, target)
            removed = prune(folder)
    finally:
        tmp.unlink(missing_ok=True)
    return {"ok": True, "file": str(target), "removed": removed}

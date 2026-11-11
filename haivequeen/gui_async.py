"""Background loading for the windows: data is read in a thread of its own and shown by the window's event loop when it is ready,
so a slow read never delays a click, a key press, a move or a resize (every window lives in the same event loop)."""
import gc
import threading

from . import db

# The garbage collector may run in any thread. A Tk image or variable that is only freed by it (never released by its window) would then call into Tcl from
# a worker thread, which waits for the event loop of the main thread and holds the thread, and the lock files it holds, for good. So no collection runs
# while a worker is reading; the main thread collects afterwards.
_gc_lock = threading.Lock()
_workers = 0
_gc_was_on = False


def _workers_start():
    """A worker starts: stop the collector if it is the first one."""
    global _workers, _gc_was_on
    with _gc_lock:
        if _workers == 0:
            _gc_was_on = gc.isenabled()
            gc.disable()
        _workers += 1


def _workers_stop():
    """A worker has finished: start the collector again when it was the last one (and was on before)."""
    global _workers
    with _gc_lock:
        _workers -= 1
        if _workers == 0 and _gc_was_on:
            gc.enable()


# The file of a connection.
def _path(conn) -> str:
    """The database file of `conn`, "" when it has none (an in-memory database) or cannot be asked; only in the thread that made `conn`."""
    try:
        return next((r[2] for r in conn.execute("PRAGMA database_list") if r[1] == "main"), "")
    except Exception:
        return ""


# Open a second connection to the database of a connection.
def reopen(conn):
    """A new connection to the file of `conn` for use in another thread; None when `conn` has no file (an in-memory database)."""
    path = _path(conn)
    return db.connect(path) if path else None


# Run a read in a thread and hand its result to the window.
def run_async(widget, work, done, failed=None, poll_ms=25):
    """Call `work()` in a thread and then `done(result)` (or `failed(exception)`) in the event loop of `widget`; nothing is called after the widget is gone.
    The thread never touches a widget: `work` must only read data."""
    box = {}

    def target():
        """Run the read and keep its result or error."""
        try:
            box["value"] = work()
        except BaseException as exc:   # a failed read is shown by the window, never lost
            box["error"] = exc
        finally:
            _workers_stop()
        box["done"] = True

    def poll():
        """Hand the result over once the thread has finished, else look again shortly."""
        try:
            if not widget.winfo_exists():
                return
            if not box.get("done"):
                widget.after(poll_ms, poll)
                return
        except Exception:   # the interpreter or the window may be gone
            return
        if "error" in box:
            if failed:
                failed(box["error"])
        else:
            done(box["value"])

    _workers_start()
    try:
        thread = threading.Thread(target=target, daemon=True)
        thread.start()
    except BaseException:
        _workers_stop()
        raise
    widget.after(poll_ms, poll)
    return thread


# Read with a connection of the thread's own.
def with_connection(conn, read):
    """A function for `run_async` that calls `read(connection)` with a new connection to the same database (closed afterwards), or with `conn` itself when it has
    no file. Call `with_connection` in the window's thread: the file of `conn` is looked up here, because a connection may only be used in the thread that made it."""
    path = _path(conn)

    def work():
        """Read in the thread with a connection of its own."""
        other = db.connect(path) if path else None
        if other is None:
            return read(conn)
        try:
            return read(other)
        finally:
            other.close()
    return work

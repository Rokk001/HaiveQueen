"""Background loading for the windows: data is read in a thread of its own and shown by the window's event loop when it is ready,
so a slow read never delays a click, a key press, a move or a resize (every window lives in the same event loop)."""
import threading

from . import db


# Open a second connection to the database of a connection.
def reopen(conn):
    """A new connection to the file of `conn` for use in another thread; None when `conn` has no file (an in-memory database)."""
    try:
        path = next((r[2] for r in conn.execute("PRAGMA database_list") if r[1] == "main"), "")
    except Exception:
        return None
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

    threading.Thread(target=target, daemon=True).start()
    widget.after(poll_ms, poll)


# Read with a connection of the thread's own.
def with_connection(conn, read):
    """`read(connection)` with a new connection to the same database (closed afterwards); with the given `conn` itself when it has no file."""
    other = reopen(conn)
    if other is None:
        return read(conn)
    try:
        return read(other)
    finally:
        other.close()

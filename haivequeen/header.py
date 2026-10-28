"""Header of one agent pane (port of head-ticker.ps1), started by haivequeen.agent_runner.

It shares the console of the pane and redraws the header every second from the agent's row in the database:
  line 1  "<PROJECT> | <PERSON> | <agent name> | <type> <model short> | <status> <runtime>"
          (project in the project colour, the rest in the agent's own colour, status word in its status colour)
  line 2  the task in plain words (one line)
  line 3  the tokens ("Tokens: no data yet" before the first token data)
When the pane width or height changes, the screen is cleared first and the milestones are replayed,
so no old header line stays behind.

Usage: python -m haivequeen.header --project P --name N [--parent-pid PID] [--runs-dir DIR]
"""
import argparse
import os
import shutil
import sys
import time
from datetime import datetime
from pathlib import Path

ESC = "\x1b"
AUTOWRAP_OFF = ESC + "[?7l"   # text beyond the last column is cut instead of wrapped into the next row
AUTOWRAP_ON = ESC + "[?7h"
DASH = "–"

# ANSI codes of the colour names used in the pane (status colours and the colour names of project.toml).
NAMED = {
    "yellow": "33", "green": "32", "red": "31", "gray": "90", "grey": "90", "white": "97", "cyan": "36",
    "darkgray": "90", "magenta": "95", "blue": "94", "black": "30",
}


def enable_vt():
    """Switch the Windows console to ANSI escape processing (a no-op on other systems or without a console)."""
    if sys.platform != "win32":
        return
    try:
        import ctypes
        kernel = ctypes.windll.kernel32
        handle = kernel.GetStdHandle(-11)
        mode = ctypes.c_uint32()
        if kernel.GetConsoleMode(handle, ctypes.byref(mode)):
            kernel.SetConsoleMode(handle, mode.value | 0x0004)
    except Exception:
        pass


def sgr(colour):
    """Return the ANSI sequence of a colour: 6 hex digits (optionally with #) = true colour, else a colour name."""
    c = str(colour or "").strip().lstrip("#")
    if len(c) == 6 and all(ch in "0123456789abcdefABCDEF" for ch in c):
        return "%s[1;38;2;%d;%d;%dm" % (ESC, int(c[0:2], 16), int(c[2:4], 16), int(c[4:6], 16))
    return "%s[%sm" % (ESC, NAMED.get(c.lower(), "0"))


def wrap_text(text, width):
    """Word-wrap a text to `width` columns (a word longer than the width is split); returns the lines."""
    out = []
    for para in str(text).replace("\r\n", "\n").split("\n"):
        cur = ""
        for word in para.split(" "):
            while len(word) > width:
                if cur:
                    out.append(cur)
                    cur = ""
                out.append(word[:width])
                word = word[width:]
            if len(cur) == 0:
                cur = word
            elif len(cur) + 1 + len(word) <= width:
                cur += " " + word
            else:
                out.append(cur)
                cur = word
        out.append(cur)
    return out


def fmt_tok(n):
    """Return a token count in short form (75k, 2k, 1.2M); a dash when unknown."""
    if n is None:
        return DASH
    n = int(n)
    if n >= 1000000:
        return "%.1fM" % (n / 1e6)
    if n >= 1000:
        return "%dk" % int(n / 1e3 + 0.5)
    return str(n)


def fmt_run(seconds):
    """Return a runtime as mm:ss, or h:mm:ss from one hour on."""
    seconds = max(0, int(seconds))
    if seconds >= 3600:
        return "%d:%02d:%02d" % (seconds // 3600, seconds % 3600 // 60, seconds % 60)
    return "%02d:%02d" % (seconds // 60, seconds % 60)


def format_token_line(ctx, tin, tout):
    """Return the third display line: "Context 44k | Total input 44k / Output 95", or "Tokens: no data yet" without token data."""
    has = any(v is not None and int(v) > 0 for v in (tin, tout, ctx))
    if not has:
        return "Tokens: no data yet"
    return "Context %s | Total input %s / Output %s" % (fmt_tok(ctx), fmt_tok(tin), fmt_tok(tout))


def _parse_time(value, default):
    """Parse an ISO time string; `default` when empty or invalid."""
    from . import db
    return db.parse_time(value, default)


def build_head(row, project, project_colour, width, now=None, max_lines=None):
    """Return the header lines for `width` columns: a list of lines, each a list of (text, colour) parts.

    `max_lines` is the number of rows the pane can spare for the header; when the full header is longer, the identity
    lines (project, name, model, status) stay, then the token line, then the task line, and the rest is dropped.
    """
    now = now or datetime.now()
    res = []
    person = str(row.get("person") or "-").upper()
    col = row.get("color") or "FFFFFF"
    typ = "Codex" if row.get("type") == "codex" else "Claude"
    status = str(row.get("status") or "")
    start = _parse_time(row.get("started_at"), now)
    end = _parse_time(row.get("ended_at"), now)
    run = fmt_run((end - start).total_seconds())
    st_col = {"done": "Green", "failed": "Red"}.get(status, "Yellow")
    prefix = "%s | " % project
    left = "%s | %s | %s %s | " % (person, row.get("name") or "", typ, row.get("modelShort") or "")
    right = "%s %s" % (status, run)
    if len(prefix) + len(left) + len(right) <= width:
        res.append([(prefix, project_colour), (left, col), (right, st_col)])
    else:
        res.append([(str(project), project_colour)])
        if len(left) + len(right) <= width:
            res.append([(left, col), (right, st_col)])
        else:
            for line in wrap_text(left.rstrip(" |"), width):
                res.append([(line, col)])
            res.append([(right, st_col)])
    task = " ".join(str(row.get("task") or "").split())
    if len(task) > width:
        task = task[:max(0, width - 3)] + "..."
    task_lines = [[(task, "Gray")]]
    token_lines = [[(line, "White")] for line in
                   wrap_text(format_token_line(row.get("ctxTokens"), row.get("tokens_in"), row.get("tokens_out")), width)]
    if max_lines is None or len(res) + len(task_lines) + len(token_lines) <= max_lines:
        return res + task_lines + token_lines
    room = max(1, max_lines)
    if len(res) >= room:
        return res[:room]
    room -= len(res)
    if len(token_lines) <= room:
        return res + (task_lines if len(token_lines) < room else []) + token_lines
    return res + token_lines[:room]


def line_str(parts):
    """Return one header line as an escape string."""
    return "".join("%s%s%s[0m" % (sgr(c), t, ESC) for t, c in parts)


def read_milestones(path, tail=300):
    """Return the last `tail` milestone lines of the file as (colour, text) pairs."""
    try:
        with open(str(path), encoding="utf-8-sig") as handle:
            lines = handle.read().splitlines()[-tail:]
    except OSError:
        return []
    out = []
    for line in lines:
        if "\t" in line:
            colour, text = line.split("\t", 1)
            out.append((colour, text))
    return out


def render(lines, width, height, milestones, full):
    """Return the escape string that redraws the header.

    full=True clears the screen, draws the header, sets the scroll region below it and replays the milestones that fit;
    full=False only rewrites the header lines at the top (cursor position is saved and restored).
    """
    n = len(lines)
    sb = []
    if full:
        sb.append("%s[r%s[2J%s[3J%s[H" % (ESC, ESC, ESC, ESC))   # 3J: the old screen must not move into the scrollback (it would stack up headers)
        sb.append(AUTOWRAP_OFF)   # a header line longer than a narrow pane is cut, never wrapped into the next row
        for i, parts in enumerate(lines):
            sb.append("%s[%d;1H%s" % (ESC, i + 1, line_str(parts)))
        sb.append(AUTOWRAP_ON)
        sb.append("%s[%d;%dr" % (ESC, n + 1, height))
        room = height - n
        rep = []
        for colour, text in milestones:
            first = True
            for wl in wrap_text(text, width - 6):
                rep.append((("" if first else "      ") + wl, colour))
                first = False
        skip = max(0, len(rep) - (room - 1))
        sb.append("%s[%d;1H" % (ESC, n + 1))
        for text, colour in rep[skip:]:
            sb.append("%s%s%s[0m\r\n" % (sgr(colour), text, ESC))
    else:
        sb.append(ESC + "7" + AUTOWRAP_OFF)
        for i, parts in enumerate(lines):
            sb.append("%s[%d;1H%s[2K%s" % (ESC, i + 1, ESC, line_str(parts)))
        sb.append(AUTOWRAP_ON + ESC + "8")
    return "".join(sb)


def project_colour_of(conn, project):
    """Return the colour of a project from the projects table (white when none is stored)."""
    row = conn.execute("SELECT colour FROM projects WHERE name=?", (project,)).fetchone()
    return (row[0] if row and row[0] else "white")


def run(project, name, parent_pid=0, runs_dir=None, out=None, interval=1.0, max_ticks=None):
    """Redraw the header every `interval` seconds until the agent ended and its parent process is gone.

    `max_ticks` stops after that many redraws (tests). Returns the number of redraws.
    """
    from . import config, db, runtime
    from . import platform as osplat
    out = out or sys.stdout
    base = Path(runs_dir) if runs_dir else osplat.runs_dir()
    ms_file = base / name / "milestones.txt"
    conn = db.connect(config.db_path())
    last = (-1, -1, -1)
    end_seen = 0
    ticks = 0
    while True:
        ticks += 1
        try:
            row = runtime.get_agent(conn, project, name)
            if row:
                size = shutil.get_terminal_size((120, 40))
                width, height = max(10, size.columns), max(2, size.lines)   # the real width: 30 columns drawn into a narrower pane wrapped into the next rows
                lines = build_head(row, project, project_colour_of(conn, project), width, max_lines=height - 1)
                key = (width, height, len(lines))
                full = key != last
                last = key
                out.write(render(lines, width, height, read_milestones(ms_file) if full else [], full))
                out.flush()
                if row.get("ended_at") and parent_pid > 0:
                    if not osplat.process_alive(parent_pid):
                        end_seen += 1
                        if end_seen >= 2:
                            break
        except Exception:
            pass
        if max_ticks is not None and ticks >= max_ticks:
            break
        time.sleep(interval)
    conn.close()
    return ticks


def main(argv=None):
    """Command line entry: python -m haivequeen.header --project P --name N [--parent-pid PID] [--runs-dir DIR]."""
    p = argparse.ArgumentParser(prog="haivequeen.header")
    p.add_argument("--project", required=True)
    p.add_argument("--name", required=True)
    p.add_argument("--parent-pid", type=int, default=0)
    p.add_argument("--runs-dir", default=None)
    a = p.parse_args(argv)
    try:
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    except Exception:
        pass
    enable_vt()
    run(a.project, a.name, a.parent_pid, a.runs_dir)
    return 0


if __name__ == "__main__":
    sys.exit(main())

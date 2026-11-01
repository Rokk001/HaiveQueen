"""Sub-terminal of one Hive (runs inside the pane the loop opens for the Hive, for as long as the Hive lives).

  - A frame in the colour of the Apiary (project) stays at the top: "Hive: <plan>", "Queen: <name> | <model> | <runtime>" and one line per Bee
    with a small progress bar. Without a running Queen only the name and the state of the Hive in German are drawn (small form). It is redrawn
    every second.
  - Below the frame the output of the Queen runner appears. The Queens of the Hive run one after the other in this pane: the loop writes
    launch.json (the runner command) into the run folder of a new Queen, this process starts it and waits for it to end.
  - Keys: X dequeen, R requeen, C requeen with a provider (then the number of the provider); each needs a second press of the same key.
  - Nothing here raises or activates the terminal window.

Usage: python -m haivequeen.hivepane --project P --plan PLAN-REL-PATH [--env NAME=VALUE ...]
       python -m haivequeen.hivepane --project P --head   (the slim head pane of the Apiary)
"""
import argparse
import json
import os
import subprocess
import sys
import textwrap
import time
from datetime import datetime
from pathlib import Path

from . import config, db, header, hives, runstate, runtime
from . import platform as osplat

CONFIRM_SECONDS = 3.0   # a command runs only when its key is pressed twice within this time
TOP, BOTTOM, SIDE = "┌", "└", "│"
RULE = "─"


def bar(step, cells=5):
    """Small progress bar from a step like '2/5': "[##---]"; '' when the step is unknown."""
    try:
        n, m = (int(x) for x in str(step).split("/"))
    except ValueError:
        return ""
    filled = max(0, min(cells, int(round(cells * n / m)))) if m > 0 else 0
    return "[" + "#" * filled + "-" * (cells - filled) + "]"


def head_lines(conn, project, width):
    """The slim head pane of an Apiary (project): one line "Apiary <Name>" in the colour of the Apiary."""
    text = "Apiary %s" % project
    if len(text) > width:
        text = text[:max(0, width - 1)] + "…" if width > 0 else ""
    return [[(text, header.project_colour_of(conn, project))]]


# Draw the visible Hive and its current agents, including during final shutdown.
def frame_lines(conn, project, plan, width, now=None, message=""):
    """Lines of the Hive frame for `width` columns: a list of lines, each a list of (text, colour) parts.

    Big form (a Queen runs or starts): a box with "Hive: <plan>", the Queen line (name, model, runtime), one "Bee: <name> <task> <n/m> [bar]" line per Bee,
    the `message` of a pending confirmation and the key hint. Small form (no Queen): the same box with "Hive: <plan>", the German state
    and running Bees (plus the message while a confirmation waits). Everything is drawn in the colour of the Apiary.
    """
    now = now or datetime.now()
    width = max(1, width)
    inner = width - 4
    col = header.project_colour_of(conn, project)
    data = next(iter(hives.hive_data(conn, project, runtime.list_agents(conn, project, "active"), plan, include_done=True)), None) or {"queen": None, "bees": [], "state": ""}
    hive = hives.get(conn, project, plan) or {}
    queen = data["queen"]
    running = bool(queen and (queen.get("status") in ("running", "starting") or (queen.get("pid") and osplat.process_alive(queen["pid"]))))
    name_line = "Hive: %s" % Path(plan).stem
    if running:
        name = str(queen.get("person") or queen.get("name") or "-")
        if queen.get("status") == "starting":
            text = "Queen: %s | startet" % name
        else:
            typ = "Codex" if queen.get("type") == "codex" else "Claude"
            started = db.parse_time(queen.get("started_at"), now)
            text = "Queen: %s | %s %s | %s" % (name, typ, queen.get("modelShort") or "", header.fmt_run((now - started).total_seconds()))
    else:
        state = hives.state_label(hive)
        queue = conn.execute("SELECT status,note FROM queue WHERE project=? AND plan_path=? ORDER BY id DESC LIMIT 1", (project, plan)).fetchone()
        if queue and queue['status'] == 'blocked' and queue['note']:
            state = "blockiert: " + ' '.join(queue['note'].split())
        elif state in ("", "weisellos") and runstate.get_state(conn)["state"] == "PAUSED":
            state = "pausiert"
        text = "Queen: " + (state or "weisellos")
    text = text.replace("  ", " ")
    body = [name_line, text]
    body += ["Bee: " + " ".join(x for x in (str(bee["name"]), str(bee["task"]), str(bee["step"]), bar(bee["step"])) if x) for bee in data["bees"]]
    if width < 5:
        return [[(t if len(t) <= width else t[:width - 1] + "…", col)] for t in body]

    def row(content, colour):
        """One framed line: side bars in the colour of the Apiary; the text is already wrapped to the pane width."""
        return [(SIDE + " ", col), (content[:inner].ljust(inner), colour), (" " + SIDE, col)]

    def wrapped(content):
        """The text cut into rows of the pane's width (a long name is broken, nothing is lost)."""
        return textwrap.wrap(content, inner, break_long_words=True, subsequent_indent="  ") or [""]

    lines = [[(TOP + RULE * (width - 2) + "┐", col)]]
    lines += [row(t if len(t) <= inner else t[:inner - 1] + "…", col) for t in body[:2]]
    lines += [row(t if len(t) <= inner else t[:inner - 1] + "…", col) for t in body[2:]]
    if message:
        lines += [row(r, "yellow") for r in wrapped(message)]
    if running:
        lines.append(row("[X] dequeen  [R] requeen  [C] requeen mit Anbieter", "darkgray"))
    lines.append([(BOTTOM + RULE * (width - 2) + "┘", col)])
    return lines


def new_state():
    """Key state of the pane: the key (or provider choice) that waits for its second press and the message shown in the frame."""
    return {"pending": "", "at": 0.0, "choose": None, "provider": None, "message": ""}


def handle_key(state, key, now, providers):
    """Apply one key to `state`; returns the action ("dequeen" or "requeen", provider or None) after a confirmed second press, else None.

    X and R ask for their second press within CONFIRM_SECONDS. C lists the `providers` (numbered), a number picks one, C again confirms
    the requeen with that provider. Any other key (or a late press) cancels what waits.
    """
    live = state["pending"] and now - state["at"] <= CONFIRM_SECONDS
    if key in ("x", "r") and live and state["pending"] == key:
        state.update(pending="", choose=None, provider=None, message="")
        return ("dequeen" if key == "x" else "requeen", None)
    if key == "c" and live and state["pending"] == "c" and state["provider"]:
        provider = state["provider"]
        state.update(pending="", choose=None, provider=None, message="")
        return ("requeen", provider)
    if key == "c":
        state.update(pending="", provider=None)
        if not providers:
            state.update(choose=None, message="Kein Anbieter eingerichtet")
            return None
        state.update(choose=list(providers), at=now, message="Anbieter: " + "  ".join("%d %s" % (i + 1, p) for i, p in enumerate(providers)))
        return None
    if state["choose"] and key.isdigit() and 1 <= int(key) <= len(state["choose"]) and now - state["at"] <= CONFIRM_SECONDS * 3:
        provider = state["choose"][int(key) - 1]
        state.update(pending="c", at=now, choose=None, provider=provider, message="Requeen mit %s: C nochmal drücken zum Bestätigen" % provider)
        return None
    if key in ("x", "r"):
        state.update(pending=key, at=now, choose=None, provider=None,
                     message=("Dequeen" if key == "x" else "Requeen") + ": %s nochmal drücken zum Bestätigen" % key.upper())
        return None
    state.update(pending="", choose=None, provider=None, message="")
    return None


def execute(conn, project, plan, action, machine=None):
    """Run a confirmed action like `bzz dequeen` / `bzz requeen` and return the message for the frame.

    A requeen also runs the schedule round for this plan, so the new Queen starts at once; errors of the Hive logic (for instance a
    provider that does not offer the Queen class) become the message.
    """
    from . import loop, scheduler
    command, provider = action
    try:
        machine = machine if machine is not None else config.load_config()
        env = next((e for e in scheduler.load_projects(conn, machine) if e.name == project), None)
        with loop.hive_schedule_lock():
            if command == "dequeen":
                hives.dequeen(conn, project, plan)
            else:
                hives.requeen(conn, project, plan, provider=provider, providers=env.providers if env else machine.get("providers", []),
                              queen_class=env.settings.get("queenClass", "medium") if env else "medium", excluded=machine.get("excludedProviders", []))
                if env:
                    hives.schedule_round(conn, machine, [env], plan_files=[plan])
    except (ValueError, OSError) as exc:
        return str(exc)
    return ("Dequeen" if command == "dequeen" else "Requeen") + ": fertig"


# Keep a completed Hive pane while an agent or native Bee still runs.
def finished(conn, project, plan):
    """Return whether the Hive is done and no agent process still uses its pane."""
    hive = hives.get(conn, project, plan)
    plan_row = conn.execute("SELECT status FROM plans WHERE project=? AND path=?", (project, plan)).fetchone()
    if hive is not None and ((plan_row and plan_row['status'] != 'DONE') or (not plan_row and hive.get("state") != "done")):
        return False
    from . import native_bee
    if any(item.get('plan') == plan and item.get('running') for item in native_bee.records(conn, project)):
        return False
    return not any(a.get('plan_path') == plan and (a.get('phase') == 'queen' or a.get('pane_id')) and
                   (a.get('status') in ('running', 'starting') or (a.get('pid') and osplat.process_alive(a['pid'])))
                   for a in runtime.list_agents(conn, project, 'active'))


def launch_due(conn, project, plan, current):
    """Start the runner of the next Queen of this Hive and return its process, else None.

    Only one Queen runs at a time (`current` is the runner process of this pane, if it still runs). A Queen is due when its row of this plan is
    'starting' without a pid and its run folder has launch.json, written by the loop, and no `launched` marker. The runner inherits the console
    of the pane; it draws no header of its own because the frame of the Hive takes that place.
    """
    if current is not None and current.poll() is None:
        return None
    for row in runtime.list_agents(conn, project, "active"):
        if row.get("plan_path") != plan or row.get("phase") != "queen" or row.get("status") != "starting" or row.get("pid"):
            continue
        folder = osplat.runs_dir() / project / row["name"]
        spec_file, marker = folder / "launch.json", folder / "launched"
        if not spec_file.is_file() or marker.exists():
            continue
        try:
            argv = json.loads(spec_file.read_text(encoding="utf-8"))["argv"]
        except (OSError, ValueError, KeyError):
            continue
        marker.write_text(db.now(), encoding="utf-8")
        env = dict(os.environ, BZZ_NO_HEADER="1")
        return subprocess.Popen(argv, env=env)
    return None


def providers_of(machine):
    """Names of the configured non-local providers that are not excluded (the choice of the C key)."""
    return [p["name"] for p in machine.get("providers", []) if not p.get("endpoint") and p["name"] not in (machine.get("excludedProviders") or [])]


# Draw a Hive pane and handle its commands and Queen launches.
def run(project, plan, out=None, interval=1.0, max_ticks=None, keys=None):
    """Draw the frame, read keys and start the Queens of the Hive until the Hive is done and no runner is left.

    The frame is redrawn every `interval` seconds; its rows stay fixed (scroll region below them), so the runner output scrolls under it.
    A change of the pane size or of the number of frame rows redraws everything and replays the milestones of the newest Queen.
    `max_ticks` stops after that many redraws and `keys` is a key reader (both for tests). Returns the number of redraws.
    """
    import shutil
    out = out or sys.stdout
    conn = db.connect(config.db_path())
    state, proc, last, last_text, ticks = new_state(), None, None, None, 0
    last_request = None
    if keys is None:
        from .dashboard import KeyReader
        keys = KeyReader()
    with keys:
        while True:
            ticks += 1
            try:
                size = shutil.get_terminal_size((120, 40))
                width, height = max(1, size.columns), max(1, size.lines)
                suffix = Path(plan).stem
                request = runtime.kv_get(conn, project, 'hive-measure-request-' + suffix)
                if request and request != last_request:
                    runtime.kv_set(conn, project, 'hive-rows-' + suffix, str(height))
                    runtime.kv_set(conn, project, 'hive-measure-ack-' + suffix, request)
                    last_request = request
                lines = frame_lines(conn, project, plan, width, message=state["message"])
                message_text = (textwrap.wrap(state["message"], width - 4, break_long_words=True, subsequent_indent="  ") or [""])[0] if state["message"] and width >= 5 else ""
                if state["message"] and 3 <= height <= 4:
                    message = next((line for line in lines if len(line) > 1 and line[1][0].rstrip() == message_text),
                                   [(state["message"][:width], "yellow")])
                    heads = lines[:2] if width < 5 else lines[1:3]
                    lines = (lines[:1] if width >= 5 and height == 4 else []) + heads + [message]
                elif state["message"] and height >= 5 and width >= 5 and len(lines) > height:
                    message_index = next((i for i, line in enumerate(lines) if len(line) > 1 and line[1][0].rstrip() == message_text), None)
                    if message_index is not None:
                        lines = lines[:3] + lines[3:message_index][:height - 5] + [lines[message_index], lines[-1]]
                elif len(lines) > height:
                    if width < 5:
                        lines = lines[:height]
                    elif height < 4:
                        lines = lines[1:height + 1]
                    else:
                        lines = lines[:height - 1] + lines[-1:]
                key = (width, height, len(lines))
                if key != last:
                    last, last_text = key, None
                    rows = runtime.list_agents(conn, project, "all")
                    newest = [a for a in rows if a.get("plan_path") == plan and a.get("phase") == "queen"]
                    current = next((a for a in reversed(newest) if a.get('status') in ('running', 'starting')),
                                   next((a for a in reversed(newest) if a.get('pid') and osplat.process_alive(a['pid'])), None))
                    ms = (osplat.runs_dir() / project / current["name"] / "milestones.txt") if current else None   # an ended Queen's old text is not replayed
                    out.write(header.render(lines, width, height, header.read_milestones(ms) if ms else [], True))
                else:
                    text = header.render(lines, width, height, [], False)
                    if text != last_text:
                        out.write(text)
                        last_text = text
                out.flush()
                proc = launch_due(conn, project, plan, proc) or proc
                if finished(conn, project, plan) and (proc is None or proc.poll() is not None):
                    break
            except Exception:
                pass
            if max_ticks is not None and ticks >= max_ticks:
                break
            end = time.monotonic() + interval
            while time.monotonic() < end:
                key = keys.get(0.2)
                if key:
                    try:
                        action = handle_key(state, key, time.monotonic(), providers_of(config.load_config()) if key == "c" else [])
                        if action:
                            state["message"] = execute(conn, project, plan, action)
                    except Exception as exc:   # a failed command shows its text, the pane stays
                        state["message"] = str(exc)
                    break
    conn.close()
    return ticks


# Draw the Apiary head and publish its measured size.
def run_head(project, out=None, interval=2.0, max_ticks=None):
    """Draw the head of a registered Apiary and return the number of redraws."""
    import shutil
    out = out or sys.stdout
    conn = db.connect(config.db_path())
    ticks, last_width, last_rows, last_request = 0, None, None, None
    while True:
        ticks += 1
        try:
            registered = conn.execute("SELECT 1 FROM projects WHERE name=?", (project,)).fetchone()
            occupied = any(a.get('pane_id') and (a.get('status') in ('running', 'starting') or
                           (a.get('pid') and osplat.process_alive(a['pid']))) for a in runtime.list_agents(conn, project, 'active'))
            if not registered and not occupied:
                break
            size = shutil.get_terminal_size((120, 40))
            width, rows = max(1, size.columns), max(1, size.lines)
            if width != last_width:
                runtime.kv_set(conn, project, 'apiary-columns', str(width))
                last_width = width
            if rows != last_rows:
                runtime.kv_set(conn, project, 'apiary-rows', str(rows))
                last_rows = rows
            request = runtime.kv_get(conn, project, 'apiary-measure-request')
            if request and request != last_request:
                runtime.kv_set(conn, project, 'apiary-columns', str(width))
                runtime.kv_set(conn, project, 'apiary-rows', str(rows))
                runtime.kv_set(conn, project, 'apiary-measure-ack', request)
                last_request = request
            out.write(header.ESC + "[?7l" + header.ESC + "[H" + "".join(header.line_str(p) for p in head_lines(conn, project, width)[0:1]) + header.ESC + "[K" + header.ESC + "[?7h")   # overwrite, no screen clear: copies would pile up in the scrollback
            out.flush()
        except Exception:
            pass
        if max_ticks is not None and ticks >= max_ticks:
            break
        time.sleep(interval)
    conn.close()
    return ticks


def main(argv=None):
    """Command line entry: python -m haivequeen.hivepane --project P --plan PLAN [--env NAME=VALUE]."""
    p = argparse.ArgumentParser(prog="haivequeen.hivepane")
    p.add_argument("--project", required=True, metavar="APIARY", help="Apiary name")
    p.add_argument("--plan", default="")
    p.add_argument("--head", action="store_true", help="draw the head pane of the Apiary instead of one Hive")
    p.add_argument("--env", action="append", default=[], metavar="NAME=VALUE", help="environment value to set before the run (test and folder overrides)")
    a = p.parse_args(argv)
    for pair in a.env:
        key, _, value = pair.partition("=")
        if key:
            os.environ[key] = value
    try:
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    except Exception:
        pass
    header.enable_vt()
    if a.head:
        run_head(a.project)
    else:
        run(a.project, a.plan)
    return 0


if __name__ == "__main__":
    sys.exit(main())

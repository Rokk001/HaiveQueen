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
import time
from datetime import datetime
from pathlib import Path

from . import config, db, header, hives, runtime
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
    return [[("Apiary %s" % project, header.project_colour_of(conn, project))]]


def frame_lines(conn, project, plan, width, now=None, message=""):
    """Lines of the Hive frame for `width` columns: a list of lines, each a list of (text, colour) parts.

    Big form (a Queen runs or starts): a box with "Hive: <plan>", the Queen line (name, model, runtime), one "Bee: <name> <task> <n/m> [bar]" line per Bee,
    the `message` of a pending confirmation and the key hint. Small form (no Queen): only "Hive: <plan>" and the German state, nothing else
    (plus the message while a confirmation waits). Everything is drawn in the colour of the Apiary.
    """
    now = now or datetime.now()
    width = max(10, width)
    inner = width - 4
    col = header.project_colour_of(conn, project)
    data = next(iter(hives.hive_data(conn, project, runtime.list_agents(conn, project, "active"), plan)), None) or {"queen": None, "bees": [], "state": ""}
    hive = hives.get(conn, project, plan) or {}
    queen = data["queen"]
    running = bool(queen and queen.get("status") in ("running", "starting"))
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
        text = "Queen: " + (hives.state_label(hive) or "-")
    text = text.replace("  ", " ")
    if not running:
        lines = [[(name_line[:width], col)], [(text[:width], col)]]
        if message:
            lines.append([(message[:width], "yellow")])
        return lines
    body = [name_line, text]
    body += ["Bee: " + " ".join(x for x in (str(bee["name"]), str(bee["task"]), str(bee["step"]), bar(bee["step"])) if x) for bee in data["bees"]]

    def row(content, colour):
        """One framed line: side bars in the colour of the Apiary, the text cut at the pane edge."""
        return [(SIDE + " ", col), (content[:inner].ljust(inner), colour), (" " + SIDE, col)]

    lines = [[(TOP + RULE * (width - 2) + "┐", col)]]
    lines += [row(t, col) for t in body]
    if message:
        lines.append(row(message, "yellow"))
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


def finished(conn, project, plan):
    """True when the Hive is done or its row is gone: the pane process ends then."""
    hive = hives.get(conn, project, plan)
    return hive is None or hive.get("state") == "done"


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
    if keys is None:
        from .dashboard import KeyReader
        keys = KeyReader()
    with keys:
        while True:
            ticks += 1
            try:
                size = shutil.get_terminal_size((120, 40))
                width, height = max(10, size.columns), max(4, size.lines)
                lines = frame_lines(conn, project, plan, width, message=state["message"])
                key = (width, height, len(lines))
                if key != last:
                    last, last_text = key, None
                    rows = runtime.list_agents(conn, project, "all")
                    newest = [a for a in rows if a.get("plan_path") == plan and a.get("phase") == "queen"]
                    ms = (osplat.runs_dir() / project / newest[-1]["name"] / "milestones.txt") if newest else None
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


def run_head(project, out=None, interval=2.0, max_ticks=None):
    """Draw the head pane of an Apiary ("Apiary <Name>") until the Apiary has no Hive that is not done any more; returns the number of redraws."""
    import shutil
    out = out or sys.stdout
    conn = db.connect(config.db_path())
    ticks = 0
    while True:
        ticks += 1
        try:
            if not conn.execute("SELECT 1 FROM hives WHERE project=? AND state!='done'", (project,)).fetchone():
                break
            width = max(10, shutil.get_terminal_size((120, 40)).columns)
            out.write(header.ESC + "[2J" + header.ESC + "[H" + "".join(header.line_str(p) for p in head_lines(conn, project, width)[0:1]))
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
    p.add_argument("--project", required=True)
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

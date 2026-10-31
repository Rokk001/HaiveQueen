"""Wanted layout of the Hive sub-terminals: one block per Apiary (project) with a living Hive, stacked; inside a block its Hives side by side.

A Hive whose Queen runs (or starts) is big, any other living Hive is small (name and state only). The functions compute the layout from the
database and hand it to the terminal backend as resize requests; a backend that cannot resize is skipped, nothing here ever raises.
"""
from . import db

HEAD_HEIGHT, BIG_HEIGHT, SMALL_HEIGHT = 1, 6, 1   # relative heights: the slim Apiary head pane, a Hive with a running Queen, any other Hive
BIG_WIDTH, SMALL_WIDTH = 3, 1                       # relative widths of Hives side by side


def width_of(conn, project, plan):
    """Relative width (BIG_WIDTH or SMALL_WIDTH) a Hive gets: big while a Queen of it runs or starts, small otherwise."""
    row = conn.execute("SELECT 1 FROM agents WHERE archived=0 AND phase='queen' AND status IN ('running','starting') AND project=? AND plan_path=? LIMIT 1", (project, plan)).fetchone()
    return BIG_WIDTH if row else SMALL_WIDTH


def compute(conn):
    """Layout blocks, one per Apiary that has a Hive which is not done (sorted by project name).

    Each block is {project, colour, weight, hives: [{plan, big, width}]}; `weight` is the relative height of the block (head plus its tallest Hive).
    """
    working = {(a["project"], a["plan_path"]) for a in db.rows(conn, "SELECT project,plan_path FROM agents WHERE archived=0 AND phase='queen' AND status IN ('running','starting')")}
    blocks = {}
    for row in db.rows(conn, "SELECT h.project,h.plan_path,p.colour FROM hives h LEFT JOIN projects p ON p.name=h.project WHERE h.state!='done' ORDER BY h.project,h.plan_path"):
        block = blocks.setdefault(row["project"], {"project": row["project"], "colour": row["colour"] or "white", "weight": 0, "hives": []})
        big = (row["project"], row["plan_path"]) in working
        block["hives"].append({"plan": row["plan_path"], "big": big, "width": BIG_WIDTH if big else SMALL_WIDTH})
    for block in blocks.values():
        block["weight"] = HEAD_HEIGHT + max(BIG_HEIGHT if h["big"] else SMALL_HEIGHT for h in block["hives"])
    return list(blocks.values())


def height_shares(blocks):
    """Share (0..1) of the window height per Apiary block; they add up to 1."""
    total = sum(b["weight"] for b in blocks) or 1
    return {b["project"]: b["weight"] / total for b in blocks}


def width_shares(block):
    """Share (0..1) of the block width per Hive plan of a block; they add up to 1."""
    total = sum(h["width"] for h in block["hives"]) or 1
    return {h["plan"]: h["width"] / total for h in block["hives"]}


def apply(term, window, conn, test=False):
    """Ask the backend to resize the live Hive panes of `window` to the computed shares (x = width in the block, y = height of the block).

    Best effort: a backend that cannot resize raises TerminalError, which ends the attempt silently; any other error is ignored as well, because
    a layout problem must never stop a round. Returns the number of resize requests sent.
    """
    from . import loop
    from .terminal import TerminalError
    sent = 0
    try:
        ids = {p.title: p.id for p in term.list_panes(window) if p.alive}
        blocks = compute(conn)
        heights = height_shares(blocks)
        for block in blocks:
            widths = width_shares(block)
            for hive in block["hives"]:
                pane = ids.get(loop.hive_pane_title(block["project"], hive["plan"], test))
                if pane:
                    term.resize_pane(pane, "x", widths[hive["plan"]])
                    term.resize_pane(pane, "y", heights[block["project"]])
                    sent += 2
    except TerminalError:
        pass
    except Exception:
        pass
    return sent

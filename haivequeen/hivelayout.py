"""Wanted layout of the Hive sub-terminals: one block per Apiary (project) with a living Hive, stacked; inside a block its Hives side by side.

A Hive whose Queen runs (or starts) is big, any other living Hive is small (name and state only). The functions compute the layout from the
database and hand it to the terminal backend as resize requests; a backend that cannot resize is skipped, nothing here ever raises.
"""
from . import config, db

HEAD_HEIGHT, BIG_HEIGHT, SMALL_HEIGHT = 1, 6, 1   # relative heights: the slim Apiary head pane, a Hive with a running Queen, any other Hive
BIG_WIDTH, SMALL_WIDTH = 3, 1                       # relative widths of Hives side by side


# Calculate the heights of Hive rows from their Queen states.
def hive_row_heights(conn, project, plans, rows):
    """Give idle Hive heads four rows and divide the rest among working Hives."""
    if not plans or rows <= 0:
        return {}
    working = {p for p in plans if width_of(conn, project, p) == BIG_WIDTH}
    if not working:
        return {p: rows / len(plans) for p in plans}
    share = max(4, (rows - 4 * (len(plans) - len(working))) / len(working))
    return {p: share if p in working else 4 for p in plans}


def width_of(conn, project, plan):
    """Relative width (BIG_WIDTH or SMALL_WIDTH) a Hive gets: big while a Queen of it runs or starts, small otherwise."""
    row = conn.execute("SELECT 1 FROM agents WHERE archived=0 AND phase='queen' AND status IN ('running','starting') AND project=? AND plan_path=? LIMIT 1", (project, plan)).fetchone()
    return BIG_WIDTH if row else SMALL_WIDTH


# Calculate target widths for the Hives in one Apiary.
def hive_widths(conn, project, plans, columns):
    """Calculate Hive widths from readable heads and the Apiary's measured width."""
    from . import hivepane, loop, runtime
    if not plans or columns <= 0:
        return {}
    agents = runtime.list_agents(conn, project, 'active')
    working = {a.get('plan_path') for a in agents if a.get('phase') == 'queen' and a.get('status') in ('running', 'starting')}
    fixed = {}
    for agent in agents:
        plan = agent.get('plan_path')
        if plan in plans and agent.get('pane_id') and loop.agent_has_live_process(agent) and int(agent.get('paneCols') or 0) > 0:
            fixed[plan] = int(agent['paneCols'])
    narrow = {}
    for plan in plans:
        rows = hivepane.frame_lines(conn, project, plan, 1024)
        heads = [''.join(text for text, _ in row)[2:-2].rstrip() for row in rows[1:3]]
        narrow[plan] = max(map(len, heads)) + 4
    if not working.intersection(plans):
        remaining = [plan for plan in plans if plan not in fixed]
        share = (columns - sum(fixed.values())) / len(remaining) if remaining else 0
        if fixed and remaining:
            share = max(share, max(narrow[plan] for plan in remaining))
        widths = {plan: fixed[plan] if plan in fixed else share for plan in plans}
        excess = max(0, sum(widths.values()) - columns)
        available = sum(fixed.values())
        if excess and available:
            for plan in fixed:
                widths[plan] = max(1, widths[plan] - min(excess, available) * fixed[plan] / available)
        return widths
    idle = [p for p in plans if p not in working and p not in fixed]
    flexible = [p for p in plans if p in working and p not in fixed]
    remaining = columns - sum(fixed.values()) - sum(narrow[p] for p in idle)
    share = remaining / len(flexible) if flexible else 0
    widths = {p: fixed[p] if p in fixed else narrow[p] if p in idle else max(narrow[p], share) for p in plans}
    excess = max(0, sum(widths.values()) - columns)
    available = sum(fixed.values())
    if excess and available:
        for plan in fixed:
            widths[plan] = max(1, widths[plan] - min(excess, available) * fixed[plan] / available)
    return widths


def configured_order(names):
    """Order Apiary names by their configured order, retaining unconfigured names last."""
    configured = [p.get('name') for p in config.load_config().get('projects', []) if isinstance(p, dict)]
    return sorted(names, key=lambda name: (configured.index(name) if name in configured else len(configured), name))


# Build layout blocks for registered Apiaries and their unfinished Hives.
def compute(conn):
    """Layout blocks for registered Apiaries in configuration order.

    Each block is {project, colour, weight, hives: [{plan, big, width}]}; `weight` is the relative height of the block (head plus its tallest Hive).
    """
    working = {(a["project"], a["plan_path"]) for a in db.rows(conn, "SELECT project,plan_path FROM agents WHERE archived=0 AND phase='queen' AND status IN ('running','starting')")}
    blocks = {row['name']: {'project': row['name'], 'colour': row['colour'] or 'white', 'weight': 0, 'hives': []}
              for row in db.rows(conn, 'SELECT name,colour FROM projects')}
    for row in db.rows(conn, "SELECT h.project,h.plan_path,p.colour FROM hives h JOIN projects p ON p.name=h.project WHERE (EXISTS (SELECT 1 FROM plans x WHERE x.project=h.project AND x.path=h.plan_path AND x.status IS NOT 'DONE') OR (h.state!='done' AND NOT EXISTS (SELECT 1 FROM plans x WHERE x.project=h.project AND x.path=h.plan_path))) ORDER BY h.rowid"):
        block = blocks.setdefault(row["project"], {"project": row["project"], "colour": row["colour"] or "white", "weight": 0, "hives": []})
        big = (row["project"], row["plan_path"]) in working
        block["hives"].append({"plan": row["plan_path"], "big": big, "width": BIG_WIDTH if big else SMALL_WIDTH})
    for block in blocks.values():
        block["weight"] = HEAD_HEIGHT + max((BIG_HEIGHT if h["big"] else SMALL_HEIGHT for h in block["hives"]), default=0)
    return [blocks[name] for name in configured_order(blocks)]


# Calculate the height share of each Apiary block.
def height_shares(blocks, rows=None):
    """Share (0..1) of the window height per Apiary block; they add up to 1."""
    if rows and blocks:
        working = [b for b in blocks if any(h["big"] for h in b["hives"])]
        minimum = {b['project']: 1 + 4 * len(b['hives']) for b in blocks}
        flexible = working or blocks
        heights = {b['project']: minimum[b['project']] for b in blocks if b not in flexible}
        remaining = rows - sum(heights.values())
        pending = list(flexible)
        while pending:
            share = remaining / len(pending)
            small = [b for b in pending if minimum[b['project']] > share]
            if not small:
                heights.update({b['project']: share for b in pending})
                break
            for b in small:
                heights[b['project']] = minimum[b['project']]
                remaining -= minimum[b['project']]
                pending.remove(b)
        total = sum(heights.values())
        return {name: height / total for name, height in heights.items()}
    total = sum(b["weight"] for b in blocks) or 1
    return {b["project"]: b["weight"] / total for b in blocks}


def width_shares(block):
    """Share (0..1) of the block width per Hive plan of a block; they add up to 1."""
    total = sum(h["width"] for h in block["hives"]) or 1
    return {h["plan"]: h["width"] / total for h in block["hives"]}


# Apply the calculated layout where the terminal supports resizing.
def apply(term, window, conn, test=False):
    """Ask the backend to resize the live Hive panes of `window` to the computed shares (x = width in the block, y = height of the block).

    Best effort: a backend that cannot resize raises TerminalError, which ends the attempt silently; any other error is ignored as well, because
    a layout problem must never stop a round. Returns the number of resize requests sent.
    """
    from . import loop, runtime
    from .terminal import TerminalError
    sent = 0
    try:
        ids = {p.title: p.id for p in term.list_panes(window) if p.alive}
        occupied = {a.get('pane_id') for a in runtime.list_agents(conn, None, 'active') if loop.agent_has_live_process(a)}
        if occupied.intersection(ids.values()):
            return 0
        blocks = compute(conn)
        measured = runtime.kv_get(conn, '*', 'dashboard-rows')
        rows = int(measured) if measured and str(measured).isdigit() else None
        heights = height_shares(blocks, rows)
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

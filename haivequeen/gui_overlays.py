"""The data of the overlays that open over the six tiles of the dashboard (U-6, U-7), and the lines they show.

Every overlay shows the state now: the units its tile counts, each with the numbers of today. `build()` returns {tile key: {"rows": [...]}}; a tile
without units has no rows and the window writes "none". The tokens of a Bee that runs as a subagent of its Queen are shown at the Bee only
(gui_data.hive_row takes them from the Queen's row), so every sum counts them once."""
from . import runtime, scheduler as sch, startctx
from . import overview

KEYS = ("apiaries", "hives", "queens", "bees", "io", "start")
TITLES = {"apiaries": "APIARIES", "hives": "HIVES", "queens": "QUEENS", "bees": "BUSY BEES", "io": "TOTAL I/O", "start": "START CONTEXT"}


def _tokens(agent) -> tuple:
    """(in, out) of an agent row."""
    return int(agent.get("tokens_in") or 0), int(agent.get("tokens_out") or 0)


def _label(model) -> str:
    """The label usage_models gives a model (same key rules)."""
    key = runtime.model_key(model)
    return sch.model_label(key) if str(key).startswith("claude-") else key


def io_rows(usage, agents_today) -> list:
    """Today's tokens per model, in the order of the usage table; their sums are the tile. `cached` = cache reads, `fresh` = the rest of the input (cache writes
    count as fresh); both are None for a model whose CLI reports no cache figures."""
    cache = {}
    for a in agents_today:
        if a.get("model") and a.get("cacheRead") is not None:
            cache[_label(a["model"])] = cache.get(_label(a["model"]), 0) + int(a["cacheRead"] or 0)
    rows = []
    for m in usage:
        if not m.get("Today") or not any(m["Today"]):
            continue
        tin, tout = m["Today"]
        cached = min(tin, cache[m["Label"]]) if m["Label"] in cache else None
        rows.append({"model": m["Label"], "tin": tin, "tout": tout, "cached": cached, "fresh": tin - cached if cached is not None else None})
    return rows


def start_rows(conn, providers, apiaries, hive_names, since) -> tuple:
    """(tile rows, overlay rows) of the Start Context tile: per provider as named in the settings the average start context of today's starts ('avg' None = none),
    per Apiary and Hive, the split per file for Queens and Bees, and the benchmark comparison per Apiary."""
    starts = startctx.today_starts(conn, since)
    names = list(dict.fromkeys(list(providers) + [s["provider"] for s in starts]))
    bench = {}
    for b in startctx.benchmarks(conn):
        bench.setdefault(b["provider"], set()).add(b["project"])

    def avg(rows):
        """Rounded average of the tokens of start rows; None for no rows."""
        return round(sum(r["tokens"] for r in rows) / len(rows)) if rows else None

    def split(rows):
        """Average start context of a group of starts, the average approximate parts of HaiveQueen and the rest (the CLI's own part)."""
        if not rows:
            return {"n": 0, "avg": None, "parts": {}, "rest": None, "approximate": False}
        withparts = [r for r in rows if r["parts"]]
        parts = {}
        for r in withparts:
            if r["parts"].get("role") is not None:
                parts["role"] = parts.get("role", 0) + r["parts"]["role"]
            for label, n in (r["parts"].get("rules") or {}).items():
                parts["rules " + label] = parts.get("rules " + label, 0) + n
            if r["parts"].get("task") is not None:
                parts["task"] = parts.get("task", 0) + r["parts"]["task"]
        parts = {k: round(v / len(withparts)) for k, v in parts.items()}
        mean = avg(rows)
        return {"n": len(rows), "avg": mean, "parts": parts, "rest": max(0, mean - sum(parts.values())) if withparts else None, "approximate": bool(withparts)}
    tile, overlay = [], []
    for name in names:
        mine = [s for s in starts if s["provider"] == name]
        tile.append({"provider": name, "avg": avg(mine), "n": len(mine)})
        if not mine and name not in bench:
            continue
        projects = sorted({s["project"] for s in mine} | bench.get(name, set()))
        hives = sorted({(s["project"], s["plan_path"]) for s in mine if s["plan_path"]})
        overlay.append({"provider": name, "avg": avg(mine), "n": len(mine),
                        "apiaries": [{"name": p, "avg": avg([s for s in mine if s["project"] == p]), "benchmark": startctx.benchmark_text(conn, name, p)} for p in projects],
                        "hives": [{"apiary": p, "name": hive_names.get((p, pl), pl), "avg": avg([s for s in mine if (s["project"], s["plan_path"]) == (p, pl)])} for p, pl in hives],
                        "split": {"queen": split([s for s in mine if s["kind"] == "queen"]), "bee": split([s for s in mine if s["kind"] in ("bee", "subbee")])}})
    return tile, overlay


def build(conn, usage, agents_all, hive_rows, bees, apiaries, day, providers) -> tuple:
    """(overlays, start tile rows). `day` = the start of today, `hive_rows` = the Hives the tile counts, `bees` = the rows of the Queens-and-Bees table."""
    today = [a for a in agents_all if str(a.get("started_at") or "") >= day.isoformat(timespec="seconds")]
    per_apiary = {}
    for a in today:
        i, o = _tokens(a)
        t = per_apiary.setdefault(a.get("project"), [0, 0])
        t[0] += i
        t[1] += o
    hives = []
    for h in hive_rows:
        i = sum(_tokens(a)[0] for a in today if (a.get("project"), a.get("plan_path")) == (h["apiary"], h["plan_path"]))
        o = sum(_tokens(a)[1] for a in today if (a.get("project"), a.get("plan_path")) == (h["apiary"], h["plan_path"]))
        per = (round(h["hive_tin"] / h["done"]), round(h["hive_tout"] / h["done"])) if h["done"] else None
        hives.append({"name": h["name"], "apiary": h["apiary"], "tin": i, "tout": o, "done": h["done"], "per_package": per})
    row = lambda r: {"name": r["name"], "hive": r["hive"], "apiary": r["apiary"], "tin": r["tin"], "tout": r["tout"]}   # noqa: E731
    hive_names = {(h["apiary"], h["plan_path"]): h["name"] for h in hive_rows}
    tile, start = start_rows(conn, providers, apiaries, hive_names, day.isoformat(timespec="seconds"))
    overlays = {"apiaries": {"rows": [{"name": a, "tin": per_apiary.get(a, [0, 0])[0], "tout": per_apiary.get(a, [0, 0])[1]} for a in apiaries]},
                "hives": {"rows": hives},
                "queens": {"rows": [row(r) for r in bees if r["kind"] == "queen"]},
                "bees": {"rows": [row(r) for r in bees if r["kind"] == "bee" and r["status"] == "running"]},
                "io": {"rows": io_rows(usage, today)},
                "start": {"rows": start}}
    return overlays, tile


# ---- the lines the window draws

def _tok(n) -> str:
    """Compact token count; an en dash for none."""
    return "–" if n is None else overview.fmt_tok0(n)


def lines(snap, key) -> list:
    """The lines of the overlay of tile `key` as (text, colour name) pairs, the first being the heading; 'none' for a tile without units."""
    rows = (snap.get("overlays") or {}).get(key, {}).get("rows", [])
    out = [(TITLES[key] + " (now)", "gold")]
    if not rows:
        return out + [("none", "dim")]
    if key == "io":
        out.append(("model | in / out | fresh in | cached in", "dim"))
        out += [("%s | %s / %s | %s | %s" % (r["model"], _tok(r["tin"]), _tok(r["tout"]), _tok(r["fresh"]), _tok(r["cached"])), "text") for r in rows]
    elif key == "apiaries":
        out.append(("Apiary | in / out today", "dim"))
        out += [("%s | %s / %s" % (r["name"], _tok(r["tin"]), _tok(r["tout"])), "text") for r in rows]
    elif key == "hives":
        out.append(("Hive | in / out today | per finished package", "dim"))
        out += [("%s (%s) | %s / %s | %s" % (r["name"], r["apiary"], _tok(r["tin"]), _tok(r["tout"]),
                                              "%s / %s" % (_tok(r["per_package"][0]), _tok(r["per_package"][1])) if r["per_package"] else "–"), "text") for r in rows]
    elif key in ("queens", "bees"):
        out.append(("%s | Hive | run in / out" % ("Queen" if key == "queens" else "Bee"), "dim"))
        out += [("%s | %s (%s) | %s / %s" % (r["name"], r["hive"], r["apiary"], _tok(r["tin"]), _tok(r["tout"])), "text") for r in rows]
    else:
        for r in rows:
            out.append(("%s | average %s over %d start%s today" % (r["provider"], _tok(r["avg"]), r["n"], "" if r["n"] == 1 else "s"), "cyan"))
            for a in r["apiaries"]:
                out.append(("  Apiary %s: %s | %s" % (a["name"], _tok(a["avg"]), a["benchmark"]), "text"))
            for h in r["hives"]:
                out.append(("  Hive %s (%s): %s" % (h["name"], h["apiary"], _tok(h["avg"])), "text"))
            for who in ("queen", "bee"):
                s = r["split"][who]
                if not s["n"]:
                    continue
                out.append(("  %ss: average %s%s" % (who.capitalize(), _tok(s["avg"]), " (parts of HaiveQueen approximate)" if s["approximate"] else ""), "dim"))
                out += [("    %s %s" % (k, _tok(v)), "text") for k, v in s["parts"].items()]
                if s["rest"] is not None:
                    out.append(("    CLI's own part %s" % _tok(s["rest"]), "text"))
    return out

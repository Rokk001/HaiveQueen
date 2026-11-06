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


MISSING = "no benchmark yet - run it in the settings"
HEADLINE = "Start context per agent start"
COLUMNS = ("WITHOUT HaiveQueen", "WITH HaiveQueen", "Saving")


def _mean(values) -> float:
    """Mean of a non-empty list."""
    return sum(values) / len(values)


def _row(block, name, pairs, group=None, date=None) -> dict:
    """One comparison row: the mean of the `pairs` (measurements) as WITHOUT (own), WITH (hq) and the mean saving in percent; no pairs = a provider without benchmark."""
    if not pairs:
        return {"block": block, "group": group, "name": name, "missing": True, "own": None, "hq": None, "saving": None, "date": None}
    return {"block": block, "group": group, "name": name, "missing": False, "own": round(_mean([p["own"] for p in pairs])), "hq": round(_mean([p["hq"] for p in pairs])),
            "saving": round(_mean([p["saving"] for p in pairs])), "date": date}


def start_rows(conn, providers) -> tuple:
    """(tile value, overlay rows) of the Start Context tile. The tile is {"saving", "n"}: the mean saving in percent over all stored measurements of all providers and
    Apiaries (a minus number; None = no benchmark). The overlay rows are comparison rows only, in three blocks: 1 per provider (mean over its Apiaries, a provider without
    benchmark is marked missing, last row 'All providers'), 2 per Apiary (mean over the providers measured there), 3 per provider within each Apiary (single measurements
    with their date, `group` = the Apiary)."""
    pairs = startctx.pairs(conn)
    tile = {"saving": round(_mean([p["saving"] for p in pairs])) if pairs else None, "n": len(pairs)}
    names = list(dict.fromkeys(list(providers) + [p["provider"] for p in pairs]))
    rows = [_row(1, n, [p for p in pairs if p["provider"] == n]) for n in names]
    if pairs:
        rows.append(_row(1, "All providers", pairs))
    apiaries = sorted({p["project"] for p in pairs})
    rows += [_row(2, a, [p for p in pairs if p["project"] == a]) for a in apiaries]
    for a in apiaries:
        rows += [_row(3, p["provider"], [p], group=a, date=str(p["measured_at"])[:10]) for p in sorted((p for p in pairs if p["project"] == a), key=lambda p: p["provider"])]
    return tile, rows


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
    tile, start = start_rows(conn, providers)
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
        return [(HEADLINE, "gold"), ("   |   ".join(("",) + COLUMNS), "dim")] + [(_start_text(r), "dim" if r["missing"] else "text") for r in rows]
    return out


def _start_text(r) -> str:
    """One comparison row as text: name, WITHOUT, WITH, Saving (and the date of a single measurement)."""
    if r["missing"]:
        return "%s   %s" % (r["name"], MISSING)
    text = "%s   %s   %s   %+d %%" % (r["name"], _tok(r["own"]), _tok(r["hq"]), r["saving"])
    return text + ("   (%s)" % r["date"] if r["date"] else "")

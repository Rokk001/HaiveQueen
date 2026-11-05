"""The data of the overlays that open over the six tiles of the dashboard (U-6, U-7), and the lines they show.

Every overlay shows the state now: the units its tile counts, each with the numbers of today. `build()` returns {tile key: {"rows": [...]}}; a tile
without units has no rows and the window writes "none". The tokens of a Bee that runs as a subagent of its Queen are shown at the Bee only
(gui_data.hive_row takes them from the Queen's row), so every sum counts them once."""
from . import runtime, scheduler as sch, startctx
from . import overview
from .i18n import N_, tr

KEYS = ("apiaries", "hives", "queens", "bees", "io", "start")
TITLES = {"apiaries": N_("APIARIES"), "hives": N_("HIVES"), "queens": N_("QUEENS"), "bees": N_("BUSY BEES"), "io": N_("TOTAL I/O"), "start": N_("START CONTEXT")}


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
    """(tile value, overlay rows) of the Start Context tile: the tile is {"avg", "n"} = the average start context of all of today's starts of all providers (avg None = none);
    the overlay has per provider as named in the settings the average, per Apiary and Hive, the split per file for Queens and Bees, and the benchmark comparison per Apiary."""
    starts = startctx.today_starts(conn, since)
    names = list(dict.fromkeys(list(providers) + [s["provider"] for s in starts]))
    bench = {}
    for b in startctx.benchmarks(conn):
        bench.setdefault(b["provider"], set()).add(b["project"])

    def avg(rows):
        """Rounded average of the tokens of start rows; None for no rows."""
        return round(sum(r["tokens"] for r in rows) / len(rows)) if rows else None

    def split(rows):
        """Average start context of a group of starts, the average approximate parts of HaiveQueen and the rest (the CLI's own part). The parts of one start are
        scaled down together when they sum to more than its start context, so no part and no sum exceeds the average shown."""
        if not rows:
            return {"n": 0, "avg": None, "parts": {}, "rest": None, "approximate": False}
        withparts = [r for r in rows if r["parts"]]
        parts = {}
        for r in withparts:
            for label, n in startctx.fit_parts(r["parts"], r["tokens"]).items():
                parts[label] = parts.get(label, 0) + n
        parts = {k: round(v / len(withparts)) for k, v in parts.items()}
        mean = avg(rows)
        rest = max(0, round(sum(r["tokens"] for r in withparts) / len(withparts)) - sum(parts.values())) if withparts else None
        return {"n": len(rows), "avg": mean, "parts": parts, "rest": rest, "approximate": bool(withparts)}
    tile, overlay = {"avg": avg(starts), "n": len(starts)}, []   # the tile: one value over all providers
    for name in names:
        mine = [s for s in starts if s["provider"] == name]
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


def _part_label(label) -> str:
    """The name of one part of a start context: role, task, or rules with the file name."""
    if label.startswith("rules "):
        return tr("rules %s", label[len("rules "):])
    return tr(label)


def lines(snap, key) -> list:
    """The lines of the overlay of tile `key` as (text, colour name) pairs, the first being the heading; 'none' for a tile without units."""
    rows = (snap.get("overlays") or {}).get(key, {}).get("rows", [])
    out = [(tr("%s (now)", tr(TITLES[key])), "gold")]
    if not rows:
        return out + [(tr("none"), "dim")]
    if key == "io":
        out.append((tr("model | in / out | fresh in | cached in"), "dim"))
        out += [("%s | %s / %s | %s | %s" % (r["model"], _tok(r["tin"]), _tok(r["tout"]), _tok(r["fresh"]), _tok(r["cached"])), "text") for r in rows]
    elif key == "apiaries":
        out.append((tr("Apiary | in / out today"), "dim"))
        out += [("%s | %s / %s" % (r["name"], _tok(r["tin"]), _tok(r["tout"])), "text") for r in rows]
    elif key == "hives":
        out.append((tr("Hive | in / out today | per finished package"), "dim"))
        out += [("%s (%s) | %s / %s | %s" % (r["name"], r["apiary"], _tok(r["tin"]), _tok(r["tout"]),
                                              "%s / %s" % (_tok(r["per_package"][0]), _tok(r["per_package"][1])) if r["per_package"] else "–"), "text") for r in rows]
    elif key in ("queens", "bees"):
        out.append((tr("Queen | Hive | run in / out") if key == "queens" else tr("Bee | Hive | run in / out"), "dim"))
        out += [("%s | %s (%s) | %s / %s" % (r["name"], r["hive"], r["apiary"], _tok(r["tin"]), _tok(r["tout"])), "text") for r in rows]
    else:
        for r in rows:
            out.append(((tr("%s | average %s over %d start today", r["provider"], _tok(r["avg"]), r["n"]) if r["n"] == 1 else tr("%s | average %s over %d starts today", r["provider"], _tok(r["avg"]), r["n"])), "cyan"))
            for a in r["apiaries"]:
                out.append((tr("  Apiary %s: %s | %s", a["name"], _tok(a["avg"]), a["benchmark"]), "text"))
            for h in r["hives"]:
                out.append((tr("  Hive %s (%s): %s", h["name"], h["apiary"], _tok(h["avg"])), "text"))
            for who in ("queen", "bee"):
                s = r["split"][who]
                if not s["n"]:
                    continue
                note = tr(" (parts of HaiveQueen approximate)") if s["approximate"] else ""
                out.append(((tr("  Queens: average %s%s", _tok(s["avg"]), note) if who == "queen" else tr("  Bees: average %s%s", _tok(s["avg"]), note)), "dim"))
                out += [("    %s %s" % (_part_label(k), _tok(v)), "text") for k, v in s["parts"].items()]
                if s["rest"] is not None:
                    out.append((tr("    CLI's own part %s", _tok(s["rest"])), "text"))
    return out

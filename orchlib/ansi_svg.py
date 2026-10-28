"""Converts text with ANSI colour codes to an SVG picture (standard library only); used to draw the dashboard image of the README."""
import re
from xml.sax.saxutils import escape

SGR = re.compile(r"\x1b\[([0-9;]*)m")
BASIC = {30: "#000000", 31: "#cd3131", 32: "#0dbc79", 33: "#e5e510", 34: "#2472c8", 35: "#bc3fbc", 36: "#11a8cd", 37: "#e5e5e5",
         90: "#666666", 91: "#f14c4c", 92: "#23d18b", 93: "#f5f543", 94: "#3b8eea", 95: "#d670d6", 96: "#29b8db", 97: "#ffffff"}
DEFAULT_FG = "#d4d4d4"
BACKGROUND = "#1e1e1e"


def _apply(codes, state):
    """Update the (colour, bold) state with the numbers of one SGR sequence."""
    i = 0
    while i < len(codes):
        c = codes[i]
        if c == 0:
            state["fg"], state["bold"] = DEFAULT_FG, False
        elif c == 1:
            state["bold"] = True
        elif c == 39:
            state["fg"] = DEFAULT_FG
        elif c in BASIC:
            state["fg"] = BASIC[c]
        elif c == 38 and i + 4 < len(codes) + 0 and codes[i + 1] == 2:
            state["fg"] = "#%02x%02x%02x" % (codes[i + 2], codes[i + 3], codes[i + 4])
            i += 4
        i += 1


def parse_line(line, state):
    """Split one line into (text, colour, bold) pieces; `state` carries the colour over from the previous line."""
    pieces, pos = [], 0
    for m in SGR.finditer(line):
        if m.start() > pos:
            pieces.append((line[pos:m.start()], state["fg"], state["bold"]))
        _apply([int(x) for x in m.group(1).split(";") if x] or [0], state)
        pos = m.end()
    if pos < len(line):
        pieces.append((line[pos:], state["fg"], state["bold"]))
    return pieces


def to_svg(text, font_size=14, pad=14):
    """Return an SVG document that shows `text` (with ANSI colours) in a monospace font on a dark background."""
    cw, lh = font_size * 0.602, font_size * 1.35
    state = {"fg": DEFAULT_FG, "bold": False}
    lines = text.splitlines()
    plain = [SGR.sub("", ln) for ln in lines]
    width = int(max([len(p) for p in plain] + [1]) * cw + 2 * pad)
    height = int(len(lines) * lh + 2 * pad)
    out = ['<svg xmlns="http://www.w3.org/2000/svg" width="%d" height="%d" viewBox="0 0 %d %d" role="img" aria-label="Dashboard of the orchestrator">' % (width, height, width, height),
           '<rect width="100%%" height="100%%" rx="8" fill="%s"/>' % BACKGROUND,
           '<g font-family="Consolas, Menlo, \'DejaVu Sans Mono\', monospace" font-size="%s" xml:space="preserve">' % font_size]
    for n, ln in enumerate(lines):
        spans = "".join('<tspan fill="%s"%s>%s</tspan>' % (fg, ' font-weight="bold"' if bold else "", escape(t)) for t, fg, bold in parse_line(ln, state))
        out.append('<text x="%s" y="%.1f">%s</text>' % (pad, pad + (n + 1) * lh - lh * 0.25, spans))
    out.append("</g></svg>")
    return "\n".join(out) + "\n"

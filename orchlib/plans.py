"""Plan files: TOML front matter, plan discovery, rule 11 (which packages may start) and the v1 legacy reader."""
import glob
import os
import re
import tomllib
from dataclasses import dataclass, field

STATUSES = ("DRAFT", "READY", "IN_PROGRESS", "DONE", "PAUSED")
CLASSES = ("light", "medium", "heavy")
DEFAULT_GLOBS = ("docs/plans/*.md",)
STARTABLE_STATUSES = ("READY", "IN_PROGRESS")


@dataclass
class Package:
    """One work package of a plan."""
    id: str
    title: str = ""
    cls: str = "medium"
    agent: str = ""
    capability: str = ""
    depends_on: list = field(default_factory=list)


@dataclass
class Plan:
    """A plan file: path, title (first heading), status and packages."""
    path: str
    title: str = ""
    status: str = "DRAFT"
    packages: list = field(default_factory=list)


def normalize_status(value):
    """Return the upper-case status if known, else DRAFT."""
    s = str(value or "").strip().upper()
    return s if s in STATUSES else "DRAFT"


def normalize_class(value):
    """Return light/medium/heavy; missing, empty or unknown becomes medium."""
    s = str(value or "").strip().lower()
    return s if s in CLASSES else "medium"


def split_front_matter(text):
    """Split text into (header_dict, body). Header is {} when no valid +++ TOML block starts the file."""
    text = text.lstrip("﻿")
    lines = text.splitlines()
    if not lines or lines[0].strip() != "+++":
        return {}, text
    for i in range(1, len(lines)):
        if lines[i].strip() == "+++":
            try:
                header = tomllib.loads("\n".join(lines[1:i]))
            except tomllib.TOMLDecodeError:
                return {}, "\n".join(lines[i + 1:])
            return header, "\n".join(lines[i + 1:])
    return {}, text


def first_heading(body):
    """Return the text of the first Markdown heading outside code fences, or ''."""
    fenced = False
    for line in body.splitlines():
        if line.lstrip().startswith("```"):
            fenced = not fenced
            continue
        if not fenced:
            m = re.match(r"^\s{0,3}#{1,6}\s+(.*?)\s*#*\s*$", line)
            if m:
                return m.group(1)
    return ""


def _depends(value):
    """Normalise a depends_on value (list, single string or missing) to a list of strings."""
    if isinstance(value, str):
        value = [value] if value.strip() else []
    if not isinstance(value, list):
        return []
    return [str(v).strip() for v in value if str(v).strip()]


def parse_plan_text(text, path=""):
    """Parse plan text with TOML front matter into a Plan; no header means DRAFT without packages."""
    header, body = split_front_matter(text)
    packages = []
    raw = header.get("packages", [])
    if isinstance(raw, list):
        for row in raw:
            if not isinstance(row, dict) or not str(row.get("id", "")).strip():
                continue
            packages.append(Package(
                id=str(row["id"]).strip(),
                title=str(row.get("title", "")),
                cls=normalize_class(row.get("class")),
                agent=str(row.get("agent", "")),
                capability=str(row.get("capability", "")),
                depends_on=_depends(row.get("depends_on")),
            ))
    return Plan(path=path, title=first_heading(body), status=normalize_status(header.get("status")), packages=packages)


def load_plan(path):
    """Read one plan file (UTF-8) and parse it."""
    with open(path, encoding="utf-8") as f:
        return parse_plan_text(f.read(), path)


def discover_plans(root, globs=None, excludes=None):
    """Load all plans below root matching the glob list (default docs/plans/*.md), sorted by path.

    excludes is an optional list of globs (same syntax, ** allowed) whose matches are removed again;
    a pattern such as docs/x/** excludes every file below that folder."""
    found = set()
    for pattern in (globs or DEFAULT_GLOBS):
        for p in glob.glob(os.path.join(root, pattern), recursive=True):
            if os.path.isfile(p):
                found.add(os.path.normpath(p))
    skip = set()
    for pattern in (excludes or []):
        for p in glob.glob(os.path.join(root, pattern), recursive=True):
            skip.add(os.path.normcase(os.path.normpath(p)))
    return [load_plan(p) for p in sorted(found) if os.path.normcase(p) not in skip]


def _is_all(value):
    """True for the special dependency 'all' (case-insensitive)."""
    return str(value).strip().lower() in ("all", "alle")


def package_dependencies(plan, package):
    """Ids a package waits for; 'all' expands to every other package of the plan."""
    deps = []
    for d in package.depends_on:
        if _is_all(d):
            deps.extend(p.id for p in plan.packages if p.id != package.id)
        else:
            deps.append(d)
    return deps


def startable_packages(plans, done_ids):
    """Rule 11: packages of READY/IN_PROGRESS plans that are not done and whose dependencies are all in done_ids.

    Returns a list of (plan, package) pairs. done_ids is a set of package ids."""
    done = set(done_ids)
    result = []
    for plan in plans:
        if plan.status not in STARTABLE_STATUSES:
            continue
        for pkg in plan.packages:
            if pkg.id in done:
                continue
            if all(d in done for d in package_dependencies(plan, pkg)):
                result.append((plan, pkg))
    return result


# ---- Legacy (v1) reader: only for import-legacy ----

_LEGACY_STATUS = re.compile(r"^Status:\s*([A-Za-z_]+)\s*$")
_LEGACY_RANGE = re.compile(r"([A-Za-z]+)(\d+)\s*[-–]\s*(?:[A-Za-z]+)?(\d+)")


def legacy_status(lines):
    """v1 status: 'Status: X' in line 2, else DRAFT."""
    if len(lines) >= 2:
        m = _LEGACY_STATUS.match(lines[1].strip())
        if m:
            return normalize_status(m.group(1))
    return "DRAFT"


def _legacy_deps(cell):
    """Dependency ids of a v1 table cell, with ranges like P1-P3 expanded."""
    deps = []
    for m in _LEGACY_RANGE.finditer(cell):
        for k in range(int(m.group(2)), int(m.group(3)) + 1):
            deps.append(m.group(1) + str(k))
    rest = _LEGACY_RANGE.sub("", cell)
    deps.extend(re.findall(r"[A-Za-z]+\d+", rest))
    return list(dict.fromkeys(deps))


def legacy_packages(lines):
    """Packages from the v1 Markdown table whose first header cell is Package/Paket (or Item with a dependency column)."""
    rows = []
    hdr = None
    cls_i = dep_i = cont_i = -1
    for line in lines:
        if not line.lstrip().startswith("|"):
            hdr = None
            continue
        cells = [c.strip() for c in line.strip().strip("|").split("|")]
        if hdr is None:
            first = cells[0].lower()
            has_dep = any(re.match(r"(?i)(depends on|abh)", c) for c in cells)
            if first in ("package", "paket", "id") or (first == "item" and has_dep):
                hdr = cells
                cls_i = dep_i = cont_i = -1
                for i, c in enumerate(cells):
                    if re.match(r"(?i)(class|klasse)$", c):
                        cls_i = i
                    if re.match(r"(?i)(depends on|abh)", c):
                        dep_i = i
                    if re.match(r"(?i)(content|inhalt|title|titel)", c):
                        cont_i = i
            continue
        if not cells[0] or re.match(r"^[-: ]+$", cells[0]):
            continue
        dep = cells[dep_i] if 0 <= dep_i < len(cells) else "none"
        deps = ["all"] if _is_all(dep) else _legacy_deps(dep)
        rows.append(Package(
            id=cells[0],
            title=cells[cont_i] if 0 <= cont_i < len(cells) else "",
            cls=normalize_class(cells[cls_i] if 0 <= cls_i < len(cells) else ""),
            depends_on=deps,
        ))
    return rows


def read_legacy_plan(path):
    """Read a v1 plan (Status in line 2, package table) into a Plan; used only by import-legacy."""
    with open(path, encoding="utf-8-sig") as f:
        lines = f.read().splitlines()
    return Plan(path=path, title=first_heading("\n".join(lines)), status=legacy_status(lines), packages=legacy_packages(lines))

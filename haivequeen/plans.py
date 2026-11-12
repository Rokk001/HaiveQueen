"""Plan files: TOML front matter, plan discovery, rule 11 (which packages may start) and the v1 legacy reader."""
from . import filetxn
import glob
import os
import re
import tomllib
from dataclasses import dataclass, field

STATUSES = ("DRAFT", "SENT", "PREPARING", "QUEUED", "RUNNING", "WAITING", "REVIEW", "DONE", "PAUSED", "CANCELLED")
LEGACY_STATUSES = {"SUBMITTED": "SENT", "READY": "QUEUED", "IN_PROGRESS": "RUNNING", "REJECTED": "CANCELLED"}   # old names in existing plan files; written with the new name at the next status change
CLASSES = ("light", "medium", "heavy")
VERIFIES = ("code", "build", "owner")   # who closes the last requirements of a package: the Bee (code), the build, or the owner
LEGACY_STEP_PACKAGES = ("INTEGRATION-BUILD", "INTEGRATION-DEPLOY")   # package ids of the time before the Orchestrator; retired (integration.retire_legacy_steps), a plan may not use them
DEFAULT_GLOBS = ("docs/plans/*.md",)
CUT_STATUSES = ("SENT", "PREPARING")   # plans whose Queen still cuts them into packages
STARTABLE_STATUSES = ("QUEUED", "RUNNING", "WAITING", "REVIEW")
QUEEN_STATUSES = CUT_STATUSES + STARTABLE_STATUSES   # plans that get a Hive and a Queen; a SENT plan is cut into packages by its Queen first


@dataclass
class Package:
    """One work package of a plan."""
    id: str
    title: str = ""
    cls: str = "medium"
    agent: str = ""
    capability: str = ""
    verify: str = "code"
    depends_on: list = field(default_factory=list)
    requirements: list = field(default_factory=list)   # ids of the requirements in the Queen's state file this package covers


@dataclass
class Plan:
    """A plan file: path, title (first heading), status and packages."""
    path: str
    title: str = ""
    status: str = "DRAFT"
    packages: list = field(default_factory=list)
    warnings: list = field(default_factory=list)
    errors: list = field(default_factory=list)   # header problems shown in the GUI: broken header, unknown status or class
    owner_check: str = "ask"   # "ask": the owner accepts the integrated plan; "none" (header owner_check = "none") skips that question
    branch: str = ""      # header field `branch`: the plan branch, the only source of it
    worktree: str = ""    # header field `worktree`: the folder of the plan worktree, the only source of it


def normalize_status(value):
    """Return the upper-case status (an old name as its new name) if known, else DRAFT."""
    s = str(value or "").strip().upper()
    s = LEGACY_STATUSES.get(s, s)
    return s if s in STATUSES else "DRAFT"


def status_known(value):
    """True when the value is a current or an old status name."""
    s = str(value or "").strip().upper()
    return s in STATUSES or s in LEGACY_STATUSES


def normalize_class(value):
    """Return light/medium/heavy; missing, empty or unknown becomes medium."""
    s = str(value or "").strip().lower()
    return s if s in CLASSES else "medium"


def normalize_verify(value):
    """Return (verify, valid): code, build or owner; missing or empty is code and valid, anything else is code and not valid."""
    s = str(value or "").strip().lower()
    return (s, True) if s in VERIFIES else ("code", not s)


def split_front_matter(text, errors=None):
    """Split text into (header_dict, body). Header is {} when no valid +++ TOML block starts the file; the reason of a broken header is appended to errors."""
    text = text.lstrip("﻿")
    lines = text.splitlines()
    if not lines or lines[0].strip() != "+++":
        return {}, text
    for i in range(1, len(lines)):
        if lines[i].strip() == "+++":
            try:
                header = tomllib.loads("\n".join(lines[1:i]))
            except tomllib.TOMLDecodeError as exc:
                if errors is not None:
                    errors.append("the plan header is broken: %s" % exc)
                return {}, "\n".join(lines[i + 1:])
            return header, "\n".join(lines[i + 1:])
    if errors is not None:
        errors.append("the plan header has no closing +++ line")
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


# Parse plan text with TOML front matter into a Plan; no header means DRAFT without packages.
def parse_plan_text(text, path=""):
    """Parse plan text with TOML front matter into a Plan; no header means DRAFT without packages."""
    errors = []
    header, body = split_front_matter(text, errors)
    packages, warnings = [], []
    if "status" in header and not status_known(header.get("status")):
        errors.append("the plan status '%s' is unknown (use %s)" % (header.get("status"), ", ".join(STATUSES)))
    raw = header.get("packages", [])
    if isinstance(raw, list):
        for row in raw:
            if not isinstance(row, dict) or not str(row.get("id", "")).strip():
                continue
            if "class" in row and str(row.get("class") or "").strip().lower() not in CLASSES:
                errors.append("package %s: class '%s' is unknown (use %s)" % (str(row["id"]).strip(), row.get("class"), ", ".join(CLASSES)))
            verify, valid = normalize_verify(row.get("verify"))
            if not valid:
                warnings.append("package %s: verify '%s' is unknown (use %s), treated as code" % (str(row["id"]).strip(), row.get("verify"), ", ".join(VERIFIES)))
            packages.append(Package(
                id=str(row["id"]).strip(),
                title=str(row.get("title", "")),
                cls=normalize_class(row.get("class")),
                agent="",
                capability=str(row.get("capability", "")),
                verify=verify,
                depends_on=_depends(row.get("depends_on")),
                requirements=_depends(row.get("requirements")),
            ))
    owner_check = "none" if str(header.get("owner_check", "")).strip().lower() in ("none", "false", "no", "off") else "ask"
    return Plan(path=path, title=first_heading(body), status=normalize_status(header.get("status")), packages=packages, warnings=warnings, errors=errors, owner_check=owner_check,
                branch=str(header.get("branch") or "").strip(), worktree=str(header.get("worktree") or "").strip())


@filetxn.coordinated
def load_plan(path):
    """Read one plan file (UTF-8) and parse it."""
    with open(path, encoding="utf-8") as f:
        return parse_plan_text(f.read(), path)


@filetxn.coordinated
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


REQUIREMENTS_HEADING = re.compile(r"(?im)^#{1,6}\s*requirements(?:\s*\([^)]*\))?\s*$")   # also "Requirements (<note>)"
REQUIREMENT_LINE = re.compile(r"^\s*[-*]\s*([A-Za-z0-9_.-]+)\s*:\s*(.*\S)\s*$")
REQUIREMENT_LINE_NO_COLON = re.compile(r"^\s*[-*]\s*([A-Za-z]+-\d+[A-Za-z]?)\s+(.*\S)\s*$")   # "- B-1 <text>"


def state_file_path(plan_path):
    """Path of the Queen's state file beside a plan: <name>-STATE.md, a trailing -PLAN of the name dropped."""
    base, name = os.path.split(plan_path)
    stem = re.sub(r"-PLAN$", "", os.path.splitext(name)[0])
    return os.path.join(base, stem + "-STATE.md")


def parse_requirements(text):
    """Requirements of a state file as a list of (id, text): the lines '- <id>: <text>' under a heading 'Requirements' up to the next heading."""
    found, inside = [], False
    for line in text.replace("\r\n", "\n").split("\n"):
        if line.lstrip().startswith("#"):
            inside = bool(REQUIREMENTS_HEADING.match(line.strip()))
            continue
        m = (REQUIREMENT_LINE.match(line) or REQUIREMENT_LINE_NO_COLON.match(line)) if inside else None
        if m:
            found.append((m.group(1), m.group(2)))
    return found


def load_requirements(plan_path):
    """Requirements the Queen wrote into the state file beside the plan; empty when there is no file or no such section."""
    try:
        with open(state_file_path(plan_path), encoding="utf-8-sig") as f:
            return parse_requirements(f.read())
    except OSError:
        return []


def requirement_problems(plan, requirements):
    """Problems between the requirement list and the packages: a requirement no package covers, a package naming an unknown requirement, a repeated id."""
    problems = []
    ids = [r[0] for r in requirements]
    for dup in sorted({i for i in ids if ids.count(i) > 1}):
        problems.append("requirement id %s is used more than once in the state file" % dup)
    covered = {r for p in plan.packages for r in p.requirements}
    for rid in ids:
        if rid not in covered:
            problems.append("requirement %s belongs to no package: cut the plan again and add it to the requirements of a package" % rid)
    if requirements:
        for p in plan.packages:
            for r in p.requirements:
                if r not in ids:
                    problems.append("package %s names requirement %s, which is not in the state file" % (p.id, r))
    return problems


def same_folder(a, b):
    """True when both paths name the same folder (case, separators and a trailing separator ignored)."""
    return os.path.normcase(os.path.abspath(str(a))) == os.path.normcase(os.path.abspath(str(b)))


def check_plan(plan, capabilities, requirements=(), main_checkout=None):
    """Problems that keep a cut plan from running, as a list of texts (empty when it is fine).

    `capabilities` is the set of capability names the configured providers offer. Checked: at least one package, unique ids, a title per
    package, every dependency names a package of the plan (or all), no dependency cycle and every named capability is offered by a provider.
    `requirements` is the list (id, text) the Queen wrote down; each must belong to at least one package.
    The header must name `branch` and `worktree`, and the worktree must not be `main_checkout` (the project's main checkout)."""
    problems = []
    for key in ("branch", "worktree"):
        if not getattr(plan, key, ""):
            problems.append("the header has no `%s` field (the plan %s)" % (key, "branch" if key == "branch" else "worktree folder"))
    if plan.worktree and main_checkout and same_folder(plan.worktree, main_checkout):
        problems.append("the header `worktree` is the main checkout of the project (%s): name a separate worktree folder" % plan.worktree)
    ids = [p.id for p in plan.packages]
    if not plan.packages:
        problems.append("the header lists no package")
    for dup in sorted({i for i in ids if ids.count(i) > 1}):
        problems.append("package id %s is used more than once" % dup)
    for reserved in sorted(set(ids) & set(LEGACY_STEP_PACKAGES)):
        problems.append("package id %s is retired (it was an integration step package)" % reserved)
    deps = {p.id: package_dependencies(plan, p) for p in plan.packages}
    for p in plan.packages:
        if not p.title.strip():
            problems.append("package %s has no title" % p.id)
        for d in deps[p.id]:
            if d not in ids:
                problems.append("package %s depends on %s, which is not in the plan" % (p.id, d))
        from .scheduler import canonical_capability
        available = {canonical_capability(c) for c in capabilities}
        for cap in [canonical_capability(c) for c in p.capability.split(",") if c.strip()]:
            if cap not in available:
                problems.append("package %s needs the capability %s, which no configured provider offers" % (p.id, cap))
    state = {}

    def visit(i, trail):
        """Depth-first walk over the dependencies; reports the first cycle found."""
        if state.get(i) == "done":
            return
        if state.get(i) == "open":
            problems.append("dependency cycle: " + " -> ".join(trail[trail.index(i):] + [i]))
            return
        state[i] = "open"
        for d in deps.get(i, []):
            if d in deps:
                visit(d, trail + [d])
        state[i] = "done"

    for i in ids:
        visit(i, [i])
    problems.extend(requirement_problems(plan, list(requirements)))
    return problems


def plan_warnings(plan):
    """Hints that do not block a plan: a package without a capability that waits for a package that needs one stops when that provider is missing.

    The dependency `all` is left out (a final check waits for everything on purpose)."""
    needs = {p.id: p.capability.strip() for p in plan.packages if p.capability.strip()}
    warnings = []
    for p in plan.packages:
        for d in p.depends_on:
            if d in needs and p.id not in needs and not _is_all(d):
                warnings.append("package %s waits for %s, which needs the capability %s: keep this only if %s needs its result, otherwise remove it so %s can start without that provider"
                                % (p.id, d, needs[d], p.id, p.id))
    return warnings


def startable_packages(plans, done_ids):
    """Rule 11: packages of QUEUED, RUNNING, WAITING or REVIEW plans that are not done and whose dependencies are all in done_ids.

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


@filetxn.coordinated
def read_legacy_plan(path):
    """Read a v1 plan (Status in line 2, package table) into a Plan; used only by import-legacy."""
    with open(path, encoding="utf-8-sig") as f:
        lines = f.read().splitlines()
    return Plan(path=path, title=first_heading("\n".join(lines)), status=legacy_status(lines), packages=legacy_packages(lines))

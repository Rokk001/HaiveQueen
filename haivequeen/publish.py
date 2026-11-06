"""Release at integration (H-43, H-46 k): with the Apiary switch publishReleases on and a remote on GitHub, HaiveQueen itself (never an agent)
publishes every integration into the integration branch as a GitHub release.

The version counts the last digit of the latest release up (2.0.1, 2.0.2, ...); without a release yet it counts up from the version of the
pyproject.toml of the Apiary. A bigger step is taken only when the owner enters it as nextVersion on the Apiary page; a nextVersion that is not newer
than the latest release is already used and is ignored.

Every integration is recorded at `bzz integration release` in an ordered queue (kv release-queue) with its queue row tip and the commit at which
that tip came into the integration branch (the integration point in the first-parent history, which later integrations do not move). When that
commit cannot be read then, the retry looks it up again from the history of the fetched branch and never takes a later head. The loop also queues
integrations since the switch was turned on that no `bzz integration release` recorded; integrations before that get no release. The releases go
out oldest first; each chosen version is stored before publishing. A failed preparation or publication (network, missing token, GitHub error) stays
at the head of the queue and is retried by the next loop round with the same version and commit; integrations that happen meanwhile wait behind it
in their order. The token comes from GITHUB_TOKEN or GH_TOKEN, else from `gh auth token`.
"""
import json
import os
import re
import tomllib
from datetime import datetime

from . import db, releases, runtime

QUEUE_KEY = "release-queue"       # JSON list of the integrations not yet published, oldest first
PENDING_KEY = "release-pending"   # the single pending entry of the first K10 version; taken over into the queue
LAST_KEY = "release-last"         # the version HaiveQueen published last
ERROR_KEY = "release-error"
SINCE_KEY = "release-since"       # switch-on time; older integrations get no release
BEFORE_KEY = "release-before-switch"  # integrated queue IDs already present at switch-on
DONE_PREFIX = "release-done:"

_UPSERT = ("INSERT INTO kv (project, key, value, updated_at) VALUES (?,?,?,?) "
           "ON CONFLICT(project, key) DO UPDATE SET value=excluded.value, updated_at=excluded.updated_at")


def _run(argv, cwd):
    """Run a program (the run helper of the self-update)."""
    from . import selfupdate
    return selfupdate.run_command(argv, cwd)


def github_repository(env, run=None):
    """owner/name when the remote of the Apiary (remoteName, default origin) is on GitHub, else None."""
    run = run or _run
    remote = str(env.settings.get("remoteName") or "origin")
    code, out = run(["git", "-C", str(env.workspace), "remote", "get-url", remote], env.workspace)
    if code != 0:
        return None
    try:
        return releases.repository_from_remote(out.strip())
    except ValueError:
        return None


def token(run=None) -> str:
    """A GitHub token for publishing: GITHUB_TOKEN, GH_TOKEN, else `gh auth token`; empty when none is available."""
    for key in ("GITHUB_TOKEN", "GH_TOKEN"):
        value = os.environ.get(key, "").strip()
        if value:
            return value
    run = run or _run
    code, out = run(["gh", "auth", "token"], os.getcwd())
    value = out.strip() if code == 0 else ""
    return value if value and "\n" not in value and " " not in value else ""


def base_version(env) -> str:
    """The version of the pyproject.toml of the Apiary, else 0.0.0 (the first release counts up from it)."""
    try:
        with open(env.workspace / "pyproject.toml", "rb") as handle:
            value = str(tomllib.load(handle)["project"]["version"])
        releases.next_version(value)
        return value
    except (OSError, KeyError, ValueError, tomllib.TOMLDecodeError):
        return "0.0.0"


def choose_version(env, current) -> str:
    """The next version: the owner's nextVersion when it is newer than `current`, else `current` with the last digit counted up."""
    requested = str(env.settings.get("nextVersion") or "").strip()
    if requested:
        try:
            return releases.next_version(current, requested)
        except ValueError:
            pass   # already used (or not newer): the last digit counts up
    return releases.next_version(current)


def _sha(text) -> str:
    """The first word of a Git output when it is a full object id, else empty."""
    words = (text or "").split()
    return words[0].lower() if words and re.fullmatch(r"[0-9a-fA-F]{40}", words[0]) else ""


def _git(env, run, *args):
    """Run git in the Apiary folder; returns (exit code, output)."""
    return run(["git", "-C", str(env.workspace)] + list(args), env.workspace)


def branch_heads(env, run=None, fetch=False) -> list:
    """The commits the integration branch points at, best first: the remote (ls-remote), then the local remote branch.

    With fetch the remote branch is fetched first (a failure is ignored), so the local copy holds the history of the remote head."""
    run = run or _run
    remote = str(env.settings.get("remoteName") or "origin")
    branch = str(env.settings.get("integrationBranch") or "main")
    if fetch:
        _git(env, run, "fetch", "--quiet", remote, "refs/heads/%s:refs/remotes/%s/%s" % (branch, remote, branch))
    heads = []
    code, out = _git(env, run, "ls-remote", remote, "refs/heads/" + branch)
    if code == 0 and _sha(out):
        heads.append(_sha(out))
    code, out = _git(env, run, "rev-parse", "--verify", "refs/remotes/%s/%s^{commit}" % (remote, branch))
    if code == 0 and _sha(out) and _sha(out) not in heads:
        heads.append(_sha(out))
    return heads


def tip_commit(env, tip, run=None) -> str:
    """The full id of the integrated branch tip of a queue row when it is a commit known here, else empty."""
    run = run or _run
    if not tip or not re.fullmatch(r"[0-9a-fA-F]{7,64}", str(tip)):
        return ""
    code, out = _git(env, run, "rev-parse", "--verify", "--quiet", "%s^{commit}" % tip)
    return _sha(out) if code == 0 else ""


def integration_point(env, tip, head, run=None) -> str:
    """The commit of the integration branch (first-parent history of `head`) at which the commit `tip` came in, else empty.

    A fast-forward integration is `tip` itself; a merge integration is the first merge commit of the first-parent history that contains `tip`.
    It does not depend on how far the branch moved on later, so a retry finds the same commit."""
    run = run or _run
    if not tip or not head:
        return ""
    if tip == head:
        return tip
    code, _ = _git(env, run, "merge-base", "--is-ancestor", tip, head)
    if code != 0:
        return ""
    code, chain = _git(env, run, "rev-list", "--first-parent", "%s..%s" % (tip, head))
    code2, descendants = _git(env, run, "rev-list", "--ancestry-path", "%s..%s" % (tip, head))
    if code or code2:
        return ""
    later = {line.strip().lower() for line in descendants.splitlines() if _sha(line)}
    chain = [line.strip().lower() for line in chain.splitlines() if _sha(line)]
    on_branch = [c for c in chain if c in later]   # newest first; the oldest one is where the tip came in
    if not on_branch:
        return ""
    first = on_branch[-1]
    code, parent = _git(env, run, "rev-parse", "--verify", "--quiet", first + "^1")
    return tip if code == 0 and _sha(parent) == tip else first


def integrated_commit(env, run=None, tip=None, exact=False) -> str:
    """The commit of the integration branch that holds the integration of the queue row tip `tip`.

    It is the integration point of `tip` in the history of the branch (the remote head, else the local remote branch), so it stays the same
    when the branch moves on. Only right after the integration (exact False) and only when `tip` is no commit known here, the current head is
    taken. With exact (a retry later) the remote branch is fetched first and nothing but the integration point is accepted."""
    run = run or _run
    heads = branch_heads(env, run, fetch=exact)
    full = tip_commit(env, tip, run)
    if full:
        for head in heads:
            point = integration_point(env, full, head, run)
            if point:
                return point
    elif not exact and heads:
        return heads[0]
    remote = str(env.settings.get("remoteName") or "origin")
    branch = str(env.settings.get("integrationBranch") or "main")
    if not heads:
        raise ValueError("the commit of %s/%s could not be read" % (remote, branch))
    raise ValueError("the integrated tip %s is not in the history of %s/%s" % (str(tip)[:40], remote, branch))


def _report(conn, env, text) -> None:
    """Keep the last release error for the GUI and log it as an event."""
    runtime.kv_set(conn, env.name, ERROR_KEY, text)
    with db.transaction(conn):
        db.add_event(conn, env.name, "release", text)


def _load(conn, project) -> list:
    """The ordered release queue, oldest integration first; a pending entry of the older single-entry format is taken over at the head."""
    row = conn.execute("SELECT value FROM kv WHERE project=? AND key=?", (project, QUEUE_KEY)).fetchone()
    try:
        entries = [e for e in json.loads(row[0]) if isinstance(e, dict)] if row and row[0] else []
    except ValueError:
        entries = []
    old = conn.execute("SELECT value FROM kv WHERE project=? AND key=?", (project, PENDING_KEY)).fetchone()
    if old and old[0]:
        try:
            entry = json.loads(old[0])
        except ValueError:
            entry = None
        if isinstance(entry, dict) and not any(e.get("queue_id") == entry.get("queue_id") for e in entries):
            entries.insert(0, entry)
    return entries


def _queue_tip(conn, project, queue_id) -> str:
    """The tip of a queue row (for an entry of the older format without its tip)."""
    row = conn.execute("SELECT tip FROM queue WHERE project=? AND id=?", (project, queue_id)).fetchone()
    return (row["tip"] or "") if row else ""


def _store(conn, project, entries) -> None:
    """Write the release queue (inside an open transaction) and drop the older single-entry key."""
    conn.execute(_UPSERT, (project, QUEUE_KEY, json.dumps(entries), db.now()))
    conn.execute("DELETE FROM kv WHERE project=? AND key=?", (project, PENDING_KEY))


def pending_releases(conn, project) -> list:
    """The integrations not yet published, oldest first: [{queue_id, plan, tip, commit, version}] (commit empty until it is
    read, version empty until it is chosen)."""
    return _load(conn, project)


def _since(conn, env) -> str:
    """Use the configuration edit time when an external edit enabled releases."""
    value = runtime.kv_get(conn, env.name, SINCE_KEY)
    if not value:
        try:
            modified = (env.workspace / ".haivequeen" / "project.toml").stat().st_mtime
            value = min(datetime.fromtimestamp(modified).isoformat(timespec="seconds"), db.now())
        except (OSError, OverflowError, ValueError):
            value = db.now()
        runtime.kv_set(conn, env.name, SINCE_KEY, value)
    return value


def _before_switch(conn, project):
    """Queue IDs already integrated when the current switch-on occurred."""
    try:
        return set(json.loads(runtime.kv_get(conn, project, BEFORE_KEY) or "[]"))
    except (ValueError, TypeError):
        return set()


def _append(conn, env, row, run, exact) -> dict:
    """Queue the integrated queue row `row` once; its commit is the integration point of its tip, read now (empty when not readable yet)."""
    known = next((e for e in _load(conn, env.name) if e.get("queue_id") == row["id"]), None)
    if known:
        return known
    try:
        commit = integrated_commit(env, run, row["tip"], exact=exact)
    except Exception as exc:
        commit = ""
        _report(conn, env, "release of %s: the integrated commit could not be read yet, it is looked up again from the history of the "
                           "integration branch: %s" % (row["plan_path"], str(exc)[:300]))
    entry = {"queue_id": row["id"], "plan": row["plan_path"], "tip": row["tip"] or "", "commit": commit, "version": ""}
    with db.transaction(conn):
        entries = _load(conn, env.name)
        if not any(e.get("queue_id") == row["id"] for e in entries):
            entries.append(entry)
        _store(conn, env.name, entries)
    return entry


def record(conn, env, plan, run=None):
    """Append the last integration of `plan` to the release queue once; its commit is read now, so a later retry publishes exactly this commit.

    Returns the queued entry, or None when the plan has no integration that is not yet published or it was integrated before the switch was on."""
    since = runtime.kv_get(conn, env.name, SINCE_KEY)
    _since(conn, env)
    row = conn.execute("SELECT id, status, tip, plan_path, updated_at FROM queue WHERE project=? AND plan_path=? ORDER BY id DESC LIMIT 1",
                       (env.name, plan)).fetchone()
    if not row or row["status"] != "integrated" or runtime.kv_get(conn, env.name, DONE_PREFIX + str(row["id"])):
        return None
    if row["id"] in _before_switch(conn, env.name) or (since and str(row["updated_at"] or "") < since):
        return None
    return _append(conn, env, row, run or _run, exact=False)


def discover(conn, env, run=None) -> list:
    """Queue the integrations the loop finds that no `bzz integration release` recorded: integrated queue rows since the switch was on,
    not yet published, of plans that no longer hold the integration place. Returns the newly queued entries."""
    since = _since(conn, env)
    queued = {e.get("queue_id") for e in _load(conn, env.name)}
    rows = conn.execute("SELECT id, tip, plan_path, updated_at FROM queue q WHERE project=? AND status='integrated' AND updated_at>=? "
                        "AND NOT EXISTS (SELECT 1 FROM hive_integration h WHERE h.project=q.project AND h.plan_path=q.plan_path) ORDER BY id",
                        (env.name, since)).fetchall()
    added = []
    before = _before_switch(conn, env.name)
    for row in rows:
        if row["id"] in before or row["id"] in queued or runtime.kv_get(conn, env.name, DONE_PREFIX + str(row["id"])):
            continue
        added.append(_append(conn, env, row, run or _run, exact=True))
    return added


def _key(version):
    """Sort key of a version string."""
    return tuple(int(part) for part in version.split("."))


def _current(conn, env, repository, latest) -> str:
    """The version the next release counts up from: the newer of the latest GitHub release and the one HaiveQueen published last,
    else the version of pyproject.toml."""
    candidates = [v for v in (latest(repository), runtime.kv_get(conn, env.name, LAST_KEY)) if v]
    return max(candidates, key=_key) if candidates else base_version(env)


def _drain(conn, env, repository, run, latest, publisher, token_fn) -> list:
    """Publish the queued releases oldest first; stop at the first failure, which stays at the head with its chosen version."""
    done = []
    while True:
        entries = _load(conn, env.name)
        if not entries:
            if done:
                runtime.kv_delete(conn, env.name, ERROR_KEY)
            return done
        entry = dict(entries[0])
        try:
            if not entry.get("commit"):   # never the current head: only the integration point of the tip, which a later integration does not move
                tip = entry.get("tip") or _queue_tip(conn, env.name, entry.get("queue_id"))
                entry["commit"] = integrated_commit(env, run, tip, exact=True)
            if not entry.get("version"):
                entry["version"] = choose_version(env, _current(conn, env, repository, latest))
        except Exception as exc:
            _report(conn, env, "release of %s not prepared: %s" % (repository, str(exc)[:300]))
            return done
        with db.transaction(conn):   # the chosen version and commit are kept before publishing, so a retry uses the same values
            stored = _load(conn, env.name)
            if stored and stored[0].get("queue_id") == entry.get("queue_id"):
                stored[0] = entry
                _store(conn, env.name, stored)
        secret = token_fn(run=run)
        try:
            if not secret:
                raise ValueError("no GitHub token (set GITHUB_TOKEN or log in with gh auth login)")
            publisher(repository, entry["version"], entry["commit"], secret)
        except Exception as exc:
            _report(conn, env, "release %s of %s not published: %s" % (entry["version"], repository, str(exc)[:300]))
            return done
        with db.transaction(conn):
            _store(conn, env.name, [e for e in _load(conn, env.name) if e.get("queue_id") != entry.get("queue_id")])
            conn.execute(_UPSERT, (env.name, DONE_PREFIX + str(entry["queue_id"]), entry["version"], db.now()))
            conn.execute(_UPSERT, (env.name, LAST_KEY, entry["version"], db.now()))
            db.add_event(conn, env.name, "release", "release %s of %s published at %s" % (entry["version"], repository, entry["commit"][:12]))
        done.append(entry)


def after_integration(conn, env, plan, run=None, latest=None, publisher=None, token=None):
    """The program step of `bzz integration release`: record the last integration of `plan` and publish the queue in order when the switch is on.

    Returns the published {queue_id, plan, commit, version} of this integration, or None (switch off, no GitHub remote, already published,
    or kept in the queue for the retry by the loop)."""
    run = run or _run
    latest = latest or releases.latest_version
    publisher = publisher or releases.publish_release
    token_fn = token or globals()["token"]
    if not env.settings.get("publishReleases"):
        return None
    repository = github_repository(env, run)
    if not repository:
        return None
    entry = record(conn, env, plan, run)
    if not entry:
        return None
    return next((e for e in _drain(conn, env, repository, run, latest, publisher, token_fn) if e.get("queue_id") == entry["queue_id"]), None)


def retry_pending(conn, envs, run=None, latest=None, publisher=None, token=None) -> list:
    """Publish the queued releases of every Apiary with the switch on (one loop round), after queueing the integrations no
    `bzz integration release` recorded; returns the published entries."""
    run = run or _run
    latest = latest or releases.latest_version
    publisher = publisher or releases.publish_release
    token_fn = token or globals()["token"]
    done = []
    for env in envs:
        if not env.settings.get("publishReleases"):
            continue
        repository = github_repository(env, run)
        if repository:
            discover(conn, env, run)
            done.extend(_drain(conn, env, repository, run, latest, publisher, token_fn))
    return done

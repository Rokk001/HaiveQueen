"""Release at integration (H-43, H-46 k): with the Apiary switch publishReleases on and a remote on GitHub, HaiveQueen itself (never an agent)
publishes every integration into the integration branch as a GitHub release.

The version counts the last digit of the latest release up (2.0.1, 2.0.2, ...); without a release yet it counts up from the version of the
pyproject.toml of the Apiary. A bigger step is taken only when the owner enters it as nextVersion on the Apiary page; a nextVersion that is not newer
than the latest release is already used and is ignored.

Every integration is recorded at `bzz integration release` in an ordered queue (kv release-queue) with the commit read right then. The releases go
out oldest first; each chosen version is stored before publishing. A failed preparation or publication (network, missing token, GitHub error) stays
at the head of the queue and is retried by the next loop round with the same version and commit; integrations that happen meanwhile wait behind it
in their order. The token comes from GITHUB_TOKEN or GH_TOKEN, else from `gh auth token`.
"""
import json
import os
import re
import tomllib

from . import db, releases, runtime

QUEUE_KEY = "release-queue"       # JSON list of the integrations not yet published, oldest first
PENDING_KEY = "release-pending"   # the single pending entry of the first K10 version; taken over into the queue
LAST_KEY = "release-last"         # the version HaiveQueen published last
ERROR_KEY = "release-error"
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


def integrated_commit(env, run=None) -> str:
    """The commit of the integration branch right after the push of the integration: the remote, else the local remote branch the push updated."""
    run = run or _run
    remote = str(env.settings.get("remoteName") or "origin")
    branch = str(env.settings.get("integrationBranch") or "main")
    code, out = run(["git", "-C", str(env.workspace), "ls-remote", remote, "refs/heads/" + branch], env.workspace)
    sha = out.split()[0] if code == 0 and out.split() else ""
    if re.fullmatch(r"[0-9a-fA-F]{40}", sha):
        return sha
    code, local = run(["git", "-C", str(env.workspace), "rev-parse", "--verify", "refs/remotes/%s/%s^{commit}" % (remote, branch)], env.workspace)
    local = local.strip() if code == 0 else ""
    if re.fullmatch(r"[0-9a-fA-F]{40}", local):
        return local
    raise ValueError("the commit of %s/%s could not be read: %s" % (remote, branch, out.strip()[:200]))


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


def _store(conn, project, entries) -> None:
    """Write the release queue (inside an open transaction) and drop the older single-entry key."""
    conn.execute(_UPSERT, (project, QUEUE_KEY, json.dumps(entries), db.now()))
    conn.execute("DELETE FROM kv WHERE project=? AND key=?", (project, PENDING_KEY))


def pending_releases(conn, project) -> list:
    """The integrations not yet published, oldest first: [{queue_id, plan, commit, version}] (version empty until it is chosen)."""
    return _load(conn, project)


def record(conn, env, plan, run=None):
    """Append the last integration of `plan` to the release queue once; its commit is read now, so a later retry publishes exactly this commit.

    Returns the queued entry, or None when the plan has no integration that is not yet published."""
    row = conn.execute("SELECT id, status FROM queue WHERE project=? AND plan_path=? ORDER BY id DESC LIMIT 1", (env.name, plan)).fetchone()
    if not row or row["status"] != "integrated" or runtime.kv_get(conn, env.name, DONE_PREFIX + str(row["id"])):
        return None
    known = next((e for e in _load(conn, env.name) if e.get("queue_id") == row["id"]), None)
    if known:
        return known
    try:
        commit = integrated_commit(env, run)
    except Exception as exc:
        commit = ""
        _report(conn, env, "release of %s: the integrated commit could not be read, it is read again at the retry: %s" % (plan, str(exc)[:300]))
    entry = {"queue_id": row["id"], "plan": plan, "commit": commit, "version": ""}
    with db.transaction(conn):
        entries = _load(conn, env.name)
        if not any(e.get("queue_id") == row["id"] for e in entries):
            entries.append(entry)
        _store(conn, env.name, entries)
    return entry


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
            if not entry.get("commit"):
                entry["commit"] = integrated_commit(env, run)
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
    """Publish the queued releases of every Apiary with the switch on (one loop round); returns the published entries."""
    run = run or _run
    latest = latest or releases.latest_version
    publisher = publisher or releases.publish_release
    token_fn = token or globals()["token"]
    done = []
    for env in envs:
        if not env.settings.get("publishReleases") or not _load(conn, env.name):
            continue
        repository = github_repository(env, run)
        if repository:
            done.extend(_drain(conn, env, repository, run, latest, publisher, token_fn))
    return done

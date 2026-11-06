"""Release at integration (H-43, H-46 k): with the Apiary switch publishReleases on and a remote on GitHub, HaiveQueen itself (never an agent)
publishes every integration into the integration branch as a GitHub release.

The version counts the last digit of the latest release up (2.0.1, 2.0.2, ...); without a release yet it counts up from the version of the
pyproject.toml of the Apiary. A bigger step is taken only when the owner enters it as nextVersion on the Apiary page; a nextVersion that is not newer
than the latest release is already used and is ignored. The chosen version and commit are stored before publishing (kv release-pending), so a
failed publication (network, missing token, GitHub error) is retried by the next loop round with the same version and commit.
The token comes from GITHUB_TOKEN or GH_TOKEN, else from `gh auth token`.
"""
import json
import os
import tomllib

from . import db, releases, runtime

PENDING_KEY = "release-pending"
ERROR_KEY = "release-error"
DONE_PREFIX = "release-done:"


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
    """The commit of the integration branch on the remote right after the push of the integration."""
    run = run or _run
    remote = str(env.settings.get("remoteName") or "origin")
    branch = str(env.settings.get("integrationBranch") or "main")
    code, out = run(["git", "-C", str(env.workspace), "ls-remote", remote, "refs/heads/" + branch], env.workspace)
    sha = out.split()[0] if code == 0 and out.split() else ""
    if len(sha) != 40:
        raise ValueError("the commit of %s/%s could not be read: %s" % (remote, branch, out.strip()[:200]))
    return sha


def _publish(conn, env, repository, pending, publisher, token_fn, run):
    """Publish the stored pending release; record success or the error. Returns the pending entry when published, else None."""
    secret = token_fn(run=run)
    try:
        if not secret:
            raise ValueError("no GitHub token (set GITHUB_TOKEN or log in with gh auth login)")
        publisher(repository, pending["version"], pending["commit"], secret)
    except Exception as exc:
        text = "release %s of %s not published: %s" % (pending["version"], repository, str(exc)[:300])
        runtime.kv_set(conn, env.name, ERROR_KEY, text)
        with db.transaction(conn):
            db.add_event(conn, env.name, "release", text)
        return None
    runtime.kv_set(conn, env.name, DONE_PREFIX + str(pending["queue_id"]), pending["version"])
    runtime.kv_delete(conn, env.name, PENDING_KEY)
    runtime.kv_delete(conn, env.name, ERROR_KEY)
    with db.transaction(conn):
        db.add_event(conn, env.name, "release", "release %s of %s published at %s" % (pending["version"], repository, pending["commit"][:12]))
    return pending


def _pending(conn, env):
    """The stored pending release or None."""
    raw = runtime.kv_get(conn, env.name, PENDING_KEY)
    try:
        return json.loads(raw) if raw else None
    except ValueError:
        return None


def after_integration(conn, env, plan, run=None, latest=None, publisher=None, token=None):
    """The program step of `bzz integration release`: publish the release of the last integration of `plan` when the switch is on.

    Returns the published {queue_id, version, commit}, or None (switch off, no GitHub remote, already published, or failed and kept for a retry)."""
    run = run or _run
    latest = latest or releases.latest_version
    publisher = publisher or releases.publish_release
    token_fn = token or globals()["token"]
    if not env.settings.get("publishReleases"):
        return None
    repository = github_repository(env, run)
    if not repository:
        return None
    pending = _pending(conn, env)
    if pending and not _publish(conn, env, repository, pending, publisher, token_fn, run):
        return None   # an older release must go out first; the next round retries it
    row = conn.execute("SELECT id, status FROM queue WHERE project=? AND plan_path=? ORDER BY id DESC LIMIT 1", (env.name, plan)).fetchone()
    if not row or row["status"] != "integrated" or runtime.kv_get(conn, env.name, DONE_PREFIX + str(row["id"])):
        return pending
    try:
        current = latest(repository) or base_version(env)
        pending = {"queue_id": row["id"], "plan": plan, "version": choose_version(env, current), "commit": integrated_commit(env, run)}
    except Exception as exc:
        text = "release of %s not prepared: %s" % (repository, str(exc)[:300])
        runtime.kv_set(conn, env.name, ERROR_KEY, text)
        with db.transaction(conn):
            db.add_event(conn, env.name, "release", text)
        return None
    runtime.kv_set(conn, env.name, PENDING_KEY, json.dumps(pending))
    return _publish(conn, env, repository, pending, publisher, token_fn, run)


def retry_pending(conn, envs, run=None, latest=None, publisher=None, token=None) -> list:
    """Publish the releases that failed before (one loop round); returns the published entries."""
    run = run or _run
    publisher = publisher or releases.publish_release
    token_fn = token or globals()["token"]
    done = []
    for env in envs:
        pending = _pending(conn, env)
        if not pending or not env.settings.get("publishReleases"):
            continue
        repository = github_repository(env, run)
        if repository and _publish(conn, env, repository, pending, publisher, token_fn, run):
            done.append(pending)
    return done

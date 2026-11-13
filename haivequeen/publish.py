"""Publish prereleases from the Orchestrator's durable Release step.

Tag versions come from the code repository. Local files are retained until all
remote assets are verified; provider workflows are polled for the selected tag
and commit. Legacy queue data remains readable but cannot publish independently.
"""
import json
import os
import re
import tomllib
from datetime import datetime
from pathlib import Path

from . import db, releases, runtime

QUEUE_KEY = "release-queue"       # JSON list of the integrations not yet published, oldest first
PENDING_KEY = "release-pending"   # the single pending entry of the first K10 version; taken over into the queue
LAST_KEY = "release-last"         # the version HaiveQueen published last
LAST_REPO_KEY = "release-last-repository"   # owner/name the release of LAST_KEY went to; a release elsewhere does not count
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


def code_folder(env) -> Path:
    """The code repository of the Project: codeRepo (older files: repo) when it is set, else the workspace.

    Its remote, pyproject.toml version, tags, releases and commits are those of every release; the workspace is never used for them.
    An env without these settings but with a code_repo (the project of the scheduler, the GUI check) uses that folder."""
    settings = getattr(env, "settings", None) or {}
    value = settings.get("codeRepo") or settings.get("repo") or getattr(env, "code_repo", None)
    return Path(value) if value else Path(env.workspace)


_repo = code_folder   # older name of the code repository lookup


def github_repository(env, run=None):
    """owner/name when the remote (remoteName, default origin) of the code repository of the Project is on GitHub, else None."""
    from . import git_provider
    provider, _ = git_provider.for_project(env, run or _run)
    return provider.repository if provider and provider.name == "github" else None


def token(run=None) -> str:
    """Find a GitHub credential using the provider's configured fallbacks."""
    from .git_provider import Provider
    from .git_providers import github
    return Provider("github", github, "").call("login", run=run)


def base_version(env) -> str:
    """The version of the pyproject.toml of the code repository of the Project, else 0.0.0 (the first release counts up from it)."""
    try:
        with open(code_folder(env) / "pyproject.toml", "rb") as handle:
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
    """Run git in the code repository of the Project; returns (exit code, output)."""
    folder = code_folder(env)
    return run(["git", "-C", str(folder)] + list(args), folder)


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


def observe_integration_switch(conn, project):
    """Capture an externally enabled switch before its first integration is recorded.

    Called inside the queue write transaction, so the snapshot excludes the new
    integration even when the scheduler has not yet observed the setting.
    """
    from . import config
    row = conn.execute("SELECT workspace FROM projects WHERE name=?", (project,)).fetchone()
    if not row or not row[0]:
        return
    try:
        enabled = config.load_project(row[0]).get("publishReleases", False)
    except (OSError, ValueError):
        return
    if not enabled:
        return False
    if conn.execute("SELECT 1 FROM kv WHERE project=? AND key=? AND value != ''",
                    (project, SINCE_KEY)).fetchone():
        return True
    before = [r[0] for r in conn.execute("SELECT id FROM queue WHERE project=? AND status='integrated'", (project,))]
    now = db.now()
    conn.execute(_UPSERT, (project, SINCE_KEY, now, now))
    conn.execute(_UPSERT, (project, BEFORE_KEY, json.dumps(before), now))
    return True


def exclude_integration(conn, project, queue_id):
    """Keep integrations observed while disabled out of future releases."""
    before = _before_switch(conn, project)
    before.add(queue_id)
    conn.execute(_UPSERT, (project, BEFORE_KEY, json.dumps(sorted(before)), db.now()))


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
    entry = {"queue_id": row["id"], "plan": row["plan_path"], "tip": row["tip"] or "", "commit": commit, "version": "",
             "repository": github_repository(env, run) or ""}   # the commit was read in the code repository of this GitHub repository
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


def _separate_code_repo(env) -> bool:
    """True when the Project has a code repository apart from its workspace."""
    try:
        return code_folder(env).resolve() != Path(env.workspace).resolve()
    except OSError:
        return True


def _key(version):
    """Sort key of a version string."""
    return tuple(int(part) for part in version.split("."))


def last_published(conn, project, repository) -> str:
    """The version HaiveQueen published last for `project` when it went to `repository`, else empty (a release in another
    repository, or one recorded before the repository was stored, does not count)."""
    if not repository or runtime.kv_get(conn, project, LAST_REPO_KEY) != repository:
        return ""
    return runtime.kv_get(conn, project, LAST_KEY) or ""


def repository_tags(env, run=None):
    """Read all tags from the configured code repository remote."""
    remote = str(env.settings.get("remoteName") or "origin")
    code, output = _git(env, run or _run, "ls-remote", "--tags", "--refs", remote)
    if code:
        raise ValueError("could not read repository tags: " + output.strip())
    return [line.split("refs/tags/", 1)[1].strip() for line in output.splitlines()
            if "refs/tags/" in line]


def _start_version(conn, env, entry, repository):
    """Ask once per repository and reuse the owner's validated answer."""
    item = "release-start:" + str(repository)
    questions = runtime.list_questions(conn, env.name, item=item)
    for question in reversed(questions):
        answer = str(question.get("answer") or "").strip()
        if question["status"] == "answered":
            try:
                releases.next_version(answer)
                return answer
            except ValueError:
                raise ValueError("the start version answer must be three non-negative integers")
    if not any(q["status"] in ("open", "triage") for q in questions):
        db.add_question(conn, env.name, entry.get("plan") or "", item,
                        "What start version should be used for releases of %s?" % repository,
                        "Enter a version such as 1.0.0.")
    raise ValueError("waiting for the start version")


def _release_tag(conn, env, entry, repository, run):
    tags = repository_tags(env, run)
    settings = env.settings
    try:
        return releases.prerelease_tag(tags, str(settings.get("releasePostfix") or "dev"),
                                       requested=str(settings.get("nextVersion") or "").strip())
    except ValueError as exc:
        if str(exc) != "start version required":
            raise
    start = _start_version(conn, env, entry, repository)
    return releases.prerelease_tag(tags, str(settings.get("releasePostfix") or "dev"),
                                   requested=str(settings.get("nextVersion") or "").strip(), start=start)


def _current(conn, env, repository, latest) -> str:
    """The version the next release counts up from: the newer of the latest GitHub release of `repository` and the one HaiveQueen published
    last to `repository`, else the version of the pyproject.toml of the code repository."""
    candidates = [v for v in (latest(repository), last_published(conn, env.name, repository)) if v]
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
        if entry.get("repository") != repository and (entry.get("repository") or _separate_code_repo(env)):
            # chosen for another repository (or read from the workspace before the code repository was used): choose again here
            entry["commit"] = entry["version"] = ""
        entry["repository"] = repository
        try:
            if not entry.get("commit"):   # never the current head: only the integration point of the tip, which a later integration does not move
                tip = entry.get("tip") or _queue_tip(conn, env.name, entry.get("queue_id"))
                entry["commit"] = integrated_commit(env, run, tip, exact=True)
            if not entry.get("version"):
                entry["version"] = _release_tag(conn, env, entry, repository, run)
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
            conn.execute(_UPSERT, (env.name, LAST_REPO_KEY, repository, db.now()))
            db.add_event(conn, env.name, "release", "release %s of %s published at %s" % (entry["version"], repository, entry["commit"][:12]))
        done.append(entry)


def after_integration(conn, env, plan, **kwargs):
    """Legacy command compatibility: publication belongs to the pipeline only."""
    return None


def retry_pending(conn, envs, **kwargs):
    """Legacy queues are retained for inspection, never rediscovered or published.

    A queue row is not evidence of green Build and Deploy steps. Only the
    Orchestrator's persisted Release step may publish or retry a release.
    """
    return []


class ReleaseFailed(ValueError):
    """The provider finished a tag workflow with a failed result."""


def pipeline_release(conn, env, plan, state, folder, settings):
    """Publish after Deploy, retaining the chosen tag and upload manifest across retries."""
    import hashlib
    from . import git_provider, orchestrator

    local = settings["releaseMode"] == "local"
    record = state.setdefault("release", {})
    directory = Path(settings.get("releaseFolder") or "")
    if not directory.is_absolute():
        directory = Path(folder) / directory
    directory = directory.resolve()
    context = {"mode": settings["releaseMode"], "directory": str(directory) if local else ""}
    if record.get("context") and record["context"] != context:
        raise ValueError("release mode or folder changed during publication")
    record["context"] = context
    if local and not record.get("files"):
        if not settings.get("releaseFolder") or not directory.is_dir():
            return "release folder is empty or not set; no release made"
        paths = sorted(directory.rglob("*"))
        if any(p.is_symlink() for p in paths):
            raise ValueError("release folder contains a symbolic link")
        files = [p for p in paths if p.is_file()]
        if not files:
            return "release folder is empty; no release made"
        if len({p.name for p in files}) != len(files):
            raise ValueError("release folder contains duplicate file names")
        record["files"] = [{"name": p.name, "path": p.relative_to(directory).as_posix(),
                            "sha256": hashlib.sha256(p.read_bytes()).hexdigest()} for p in files]
        orchestrator.save(conn, env.name, plan, state)
    provider, errors = git_provider.for_project(env)
    if not provider:
        raise ValueError("; ".join(errors) or "Git provider is not supported")
    required = ({"release_prepare", "release_upload", "release_asset_digest"} if local else
                {"release_tag", "release_state"})
    if not required <= provider.capabilities:
        return "provider %s does not support %s releases; skipped" % (provider.name, "local" if local else "Git provider")
    repository = json.dumps([provider.name, provider.repository])
    if record.get("repository") and record["repository"] != repository:
        raise ValueError("release repository changed during publication")
    record["repository"] = repository
    commit = (state.get("provider") or {}).get("commit") or state.get("merged")
    if not commit:
        from . import integration
        commit = integration.head_commit(folder)
    if not commit:
        raise ValueError("release commit cannot be read")
    if not record.get("tag"):
        record["tag"] = _release_tag(conn, env, {"plan": plan}, provider.repository, _run)
        record["commit"] = commit
        orchestrator.save(conn, env.name, plan, state)
    if record.get("commit") != commit:
        raise ValueError("release commit changed after tag selection")
    secret = provider.credential()
    tag = record["tag"]
    if local:
        return _upload_files(conn, env, plan, state, directory, provider, secret)
    if not record.get("tagged"):
        provider.call("release_tag", tag, commit, secret)
        record["tagged"] = True
        orchestrator.save(conn, env.name, plan, state)
    result = provider.call("release_state", tag, commit, secret)
    if result == "failure":
        raise ReleaseFailed("tag-triggered release workflow failed")
    if result == "pending":
        return None
    if result != "success":
        raise ValueError("provider returned an unknown release state")
    return "provider release completed for " + tag


def _upload_files(conn, env, plan, state, directory, provider, secret):
    """Retry the manifest, verifying remote bytes after uncertain upload responses."""
    import hashlib
    from . import orchestrator
    record = state["release"]
    expected = {item.get("path", item["name"]) for item in record["files"]}
    actual = {p.relative_to(directory).as_posix() for p in directory.rglob("*") if p.is_file()}
    if actual - expected:
        raise ValueError("release folder gained files during publication")
    if not record.get("uploaded"):
        release = provider.call("release_prepare", record["tag"], record["commit"], secret)
        assets = release.get("assets")
        if not isinstance(assets, list) or not release.get("id"):
            raise ValueError("release response has no asset list or identifier")
        for item in record["files"]:
            path = directory / item.get("path", item["name"])
            content = path.read_bytes()
            if hashlib.sha256(content).hexdigest() != item["sha256"]:
                raise ValueError("release file changed: " + item["name"])
            matches = [a for a in assets if a.get("name") == item["name"]]
            if len(matches) > 1:
                raise ValueError("duplicate release asset: " + item["name"])
            asset = matches[0] if matches else None
            if asset is None:
                # A timeout leaves the file in place. The next attempt lists and
                # verifies the remote asset before deciding whether to POST again.
                asset = provider.call("release_upload", release, item["name"], content, secret)
            digest = provider.call("release_asset_digest", release, asset, secret)
            if digest != item["sha256"]:
                raise ValueError("existing release asset cannot be verified: " + item["name"])
        record["uploaded"] = True
        orchestrator.save(conn, env.name, plan, state)
    for item in record["files"]:
        path = directory / item.get("path", item["name"])
        if path.exists():
            if hashlib.sha256(path.read_bytes()).hexdigest() != item["sha256"]:
                raise ValueError("release file changed before cleanup: " + item["name"])
            path.unlink()
    for path in sorted(directory.rglob("*"), key=lambda p: len(p.parts), reverse=True):
        if path.is_dir() and not any(path.iterdir()):
            path.rmdir()
    return "published " + record["tag"]

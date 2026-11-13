"""Discover Git provider modules and isolate their failures.

Provider modules live in :mod:`haivequeen.git_providers`; see its module guide
for the contract. Discovery is repeated on request so adding a module needs no
registry change or application restart.
"""
import importlib
import os
import pkgutil
from urllib.parse import urlencode
from dataclasses import dataclass

from . import git_providers


class ProviderError(RuntimeError):
    """An invalid provider module or a failed provider operation."""


@dataclass(frozen=True)
class Provider:
    name: str
    module: object
    repository: object

    @property
    def capabilities(self):
        return frozenset(self.module.CAPABILITIES)

    def call(self, operation, *args, **kwargs):
        """Call an advertised operation, converting module failures to ProviderError."""
        if operation not in self.capabilities:
            raise ProviderError("provider %s does not support %s" % (self.name, operation))
        try:
            return getattr(self.module, operation)(self.repository, *args, **kwargs)
        except Exception as exc:
            raise ProviderError("provider %s: %s failed: %s" % (self.name, operation, exc)) from exc


    def open_pr(self, title, source, target, token=None):
        """Create a PR and return its provider-specific stable identifier."""
        if self.name in ("github", "gitea"):
            result = self.call("pull_request", title, source, target, token=token)
        elif self.name == "gitlab":
            result = self.call("pull_request", source, target, title, token)
        elif self.name == "azure":
            result = self.call("pull_request", source, target, title, token)
        elif self.name == "bitbucket":
            result = self.call("pull_request", title, source, target)
        else:
            raise ProviderError("provider %s does not support normalized PRs" % self.name)
        identifier = result.get({"github": "number", "gitea": "number", "gitlab": "iid", "azure": "pullRequestId", "bitbucket": "id"}[self.name])
        if identifier is None:
            raise ProviderError("provider %s returned a PR without an identifier" % self.name)
        return str(identifier)

    def ensure_pr(self, title, source, target, token=None, commit=None):
        """Recover an open PR before creating one, including after an uncertain POST."""
        try:
            existing = self._find_pr(source, target, token, commit)
            if existing is not None:
                return str(existing)
            try:
                return self.open_pr(title, source, target, token)
            except ProviderError:
                existing = self._find_pr(source, target, token, commit)
                if existing is not None:
                    return str(existing)
                raise
        except ProviderError:
            raise
        except Exception as exc:
            raise ProviderError("provider %s: PR lookup failed: %s" % (self.name, exc)) from exc

    def _find_pr(self, source, target, token, commit=None):
        if "find_pr" in self.capabilities:
            return self.call("find_pr", source, target, token, **({"commit": commit} if commit else {}))
        module, repo = self.module, self.repository

        def reusable(row, sha):
            if not commit:
                return True
            state = str(row.get("state", row.get("status", ""))).lower()
            merged = row.get("merged") is True or row.get("merged_at") or state in ("merged", "completed")
            return sha == commit if merged else state in ("open", "opened", "active")

        if self.name in ("github", "gitea"):
            page = 1
            while True:
                rows = module._request(repo, "/pulls?" + urlencode({"state": "all" if commit else "open", "page": page, "per_page": 100, "limit": 100}), token)
                for row in rows:
                    if row.get("head", {}).get("ref") == source and row.get("base", {}).get("ref") == target:
                        # Avoid adopting a fork's PR with a coincidentally equal branch name.
                        head_repo = (row.get("head", {}).get("repo") or {}).get("full_name")
                        base_repo = (row.get("base", {}).get("repo") or {}).get("full_name")
                        if base_repo and head_repo != base_repo:
                            continue
                        if not reusable(row, row.get("head", {}).get("sha")):
                            continue
                        return row["number"]
                if len(rows) < 100:
                    return None
                page += 1
        if self.name == "gitlab":
            rows = module._request(repo, "GET", "/merge_requests", token, query={"state": "all" if commit else "opened", "source_branch": source, "target_branch": target})[0]
            return next((r["iid"] for r in rows if r.get("source_branch") == source and r.get("target_branch") == target and
                         r.get("source_project_id") == r.get("target_project_id") and reusable(r, r.get("sha"))), None)
        if self.name == "azure":
            rows = module._request(repo, "git/repositories/{repo}/pullrequests", token, query={
                "searchCriteria.status": "all" if commit else "active", "searchCriteria.sourceRefName": "refs/heads/" + source,
                "searchCriteria.targetRefName": "refs/heads/" + target}).get("value", [])
            return next((r["pullRequestId"] for r in rows if r.get("sourceRefName") == "refs/heads/" + source and r.get("targetRefName") == "refs/heads/" + target and
                         reusable(r, r.get("lastMergeSourceCommit", {}).get("commitId"))), None)
        if self.name == "bitbucket":
            rows = module._list(module._repo(repo) + "/pullrequests?state=OPEN" + ("&state=MERGED" if commit else ""))
            return next((r["id"] for r in rows if r.get("source", {}).get("branch", {}).get("name") == source and
                         r.get("destination", {}).get("branch", {}).get("name") == target and
                         r.get("source", {}).get("repository", {}).get("uuid") == r.get("destination", {}).get("repository", {}).get("uuid") and
                         reusable(r, r.get("source", {}).get("commit", {}).get("hash"))), None)
        raise ProviderError("provider %s does not support resumable PR creation" % self.name)

    def pr_commit(self, identifier, token=None):
        """The exact merge commit, including squash merges; no branch-head guessing."""
        result = self.call("pr_status", identifier, *(() if self.name == "bitbucket" else (token,)))
        return (result.get("merge_commit_sha") or result.get("squash_commit_sha") or result.get("merge_commit", {}).get("hash") or
                result.get("lastMergeCommit", {}).get("commitId") or "")

    def supports_step(self, step):
        if step == "Merge":
            return {"pull_request", "pr_status"} <= self.capabilities
        return bool({"pipeline", "pipeline_for_commit"} & self.capabilities)

    def credential(self):
        """Keep profile authentication in its module; retain legacy token fallbacks."""
        from . import config
        reader = getattr(config, "git_credential", None)
        credential = reader(self.name) if reader else None
        if credential:
            # Modules also need the credential's method (for example bearer vs PAT).
            return None
        if self.name == "github" and "login" in self.capabilities:
            return self.call("login")
        return os.environ.get({"gitlab": "GITLAB_TOKEN", "azure": "AZURE_DEVOPS_TOKEN",
                               "gitea": "GITEA_TOKEN", "bitbucket": "BITBUCKET_TOKEN"}.get(self.name, ""))

    def pr_state(self, identifier, token=None):
        """Return open, merged or closed; unknown responses are errors."""
        if self.name == "bitbucket":
            result = self.call("pr_status", identifier)
        else:
            result = self.call("pr_status", identifier, token)
        value = str(result.get("state", result.get("status", ""))).lower()
        if result.get("merged") is True or result.get("merged_at") or value in ("merged", "completed"):
            return "merged"
        if value in ("open", "opened", "active"):
            return "open"
        if value in ("closed", "declined", "abandoned"):
            return "closed"
        raise ProviderError("provider %s returned unknown PR state %r" % (self.name, value))

    def pipeline_state(self, commit, token=None):
        """Return pending, success or failure for a specific commit."""
        if self.name in ("github", "gitea", "gitlab"):
            if self.name == "gitlab":
                try:
                    rows = self.module._request(self.repository, "GET", "/pipelines", token,
                                                query={"sha": commit, "per_page": 1, "order_by": "id", "sort": "desc"})[0]
                    result = rows[0] if rows else None
                except Exception as exc:
                    raise ProviderError("provider gitlab: pipeline lookup failed: %s" % exc) from exc
            else:
                result = self.call("pipeline", commit, token)
            if self.name == "gitlab" and result and result.get("sha") != commit:
                return "pending"
            value = str((result or {}).get("state", (result or {}).get("status", ""))).lower()
            # Combined status with zero contexts is not evidence of a successful pipeline.
            values = [] if self.name in ("github", "gitea") and not (result or {}).get("statuses") else [value]
            if self.name == "github":
                try:
                    page = 1
                    while True:
                        checks = self.module._request(self.repository, "/commits/%s/check-runs?per_page=100&filter=latest&page=%s" % (commit, page), token).get("check_runs", [])
                        for check in checks:
                            if check.get("head_sha") != commit or check.get("status") != "completed":
                                values.append("pending")
                            else:
                                conclusion = check.get("conclusion")
                                values.append("success" if conclusion in ("success", "neutral", "skipped") else
                                              "failure" if conclusion in ("failure", "cancelled", "timed_out", "action_required", "startup_failure", "stale") else "pending")
                        if len(checks) < 100:
                            break
                        page += 1
                except Exception as exc:
                    raise ProviderError("provider github: check lookup failed: %s" % exc) from exc
        elif self.name == "bitbucket":
            values = [str(item.get("state", "")).lower() for item in self.call("pipeline_for_commit", commit)]
        elif self.name == "azure":
            builds = self.call("pipeline_for_commit", commit, token)
            values = [str(item.get("result") or item.get("status") or "").lower()
                      for item in builds if item.get("sourceVersion") == commit]
        else:
            raise ProviderError("provider %s does not support normalized pipeline state" % self.name)
        if any(v in ("failure", "failed", "error", "canceled", "cancelled", "stopped") for v in values):
            return "failure"
        if values and all(v in ("success", "successful", "succeeded", "passed") for v in values):
            return "success"
        return "pending"


def discover(remote, package=git_providers):
    """Return (matching Provider or None, errors); one broken module cannot stop others.

    detect(remote) returns a provider-specific repository identifier, or None
    when the remote is not supported. Errors name the module and the problem.
    """
    errors = []
    found = None
    importlib.invalidate_caches()
    for info in sorted(pkgutil.iter_modules(package.__path__), key=lambda item: item.name):
        if info.name.startswith('_'):
            continue
        name = info.name
        try:
            module = importlib.import_module(package.__name__ + '.' + name)
            detect = getattr(module, 'detect', None)
            capabilities = getattr(module, 'CAPABILITIES', None)
            if not callable(detect):
                raise ValueError('detect must be callable')
            if not isinstance(capabilities, (set, frozenset, tuple, list)) or any(not isinstance(c, str) or not c.isidentifier() or c.startswith('_') for c in capabilities):
                raise ValueError('CAPABILITIES must be a collection of operation names')
            for operation in capabilities:
                if not callable(getattr(module, operation, None)):
                    raise ValueError('operation %s must be callable' % operation)
            repository = detect(remote)
            if repository is not None:
                if found is not None:
                    errors.append('provider %s: remote also matched provider %s' % (name, found.name))
                else:
                    found = Provider(name, module, repository)
        except Exception as exc:
            errors.append('provider %s: %s: %s' % (name, type(exc).__name__, exc))
    return found, errors


def for_project(env, run=None):
    """Discover the provider of the configured Git remote of the code repository."""
    from . import publish
    run = run or publish._run
    folder = publish.code_folder(env)
    remote = str(env.settings.get('remoteName') or 'origin')
    code, output = run(['git', '-C', str(folder), 'remote', 'get-url', remote], folder)
    if code:
        return None, ['cannot read Git remote %s: %s' % (remote, output.strip())]
    return discover(output.strip())

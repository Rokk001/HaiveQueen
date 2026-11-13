"""Discover Git provider modules and isolate their failures.

Provider modules live in :mod:`haivequeen.git_providers`; see its module guide
for the contract. Discovery is repeated on request so adding a module needs no
registry change or application restart.
"""
import importlib
import pkgutil
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
            result = self.call("pipeline", commit, token)
            if self.name == "gitlab" and result and result.get("sha") != commit:
                return "pending"
            value = str((result or {}).get("state", (result or {}).get("status", ""))).lower()
            # Combined status with zero contexts is not evidence of a successful pipeline.
            if self.name in ("github", "gitea") and not (result or {}).get("statuses"):
                return "pending"
            values = [value]
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

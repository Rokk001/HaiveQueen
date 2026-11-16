"""Discover Git provider modules and isolate their failures.

Provider modules live in :mod:`haivequeen.git_providers`; see its module guide
for the contract. Discovery is repeated on request so adding a module needs no
registry change or application restart.
"""
import importlib
import os
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
        identifier = self.call("open_pr", title, source, target, token)
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
        return self.call("find_pr", source, target, token, **({"commit": commit} if commit else {}))

    def pr_commit(self, identifier, token=None):
        return self.call("pr_commit", identifier, token)

    def supports_step(self, step):
        if step == "Merge":
            return {"open_pr", "find_pr", "pr_state", "pr_commit"} <= self.capabilities
        return "pipeline_state" in self.capabilities

    def credential(self):
        from . import config
        reader = getattr(config, "git_credential", None)
        if reader and reader(self.name):
            return None
        return self.call("credential") if "credential" in self.capabilities else None

    def pr_state(self, identifier, token=None):
        value = self.call("pr_state", identifier, token)
        if value not in ("open", "merged", "closed"):
            raise ProviderError("provider %s returned unknown PR state %r" % (self.name, value))
        return value

    def pipeline_state(self, commit, token=None):
        value = self.call("pipeline_state", commit, token)
        if value not in ("pending", "success", "failure"):
            raise ProviderError("provider %s returned unknown pipeline state %r" % (self.name, value))
        return value


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


def login_options():
    """List provider-wide access token controls; device sign-in is deferred."""
    rows = []
    for info in sorted(pkgutil.iter_modules(git_providers.__path__), key=lambda item: item.name):
        if info.name.startswith('_'):
            continue
        try:
            options = _login_options(info.name)
            if options is not None:
                rows.append(options)
        except Exception as exc:
            rows.append({'name': info.name, 'device': False, 'method': '',
                         'error': 'provider %s: %s' % (info.name, exc)})
    return rows


def _login_options(name):
    from . import config
    module = importlib.import_module(git_providers.__name__ + '.' + name)
    options = getattr(module, 'LOGIN_OPTIONS', None)
    if options is None:
        return None
    status_error = ''
    try:
        credential = config.git_credential(name) or {}
    except ValueError as exc:
        # Keep recovery controls available when an expired credential cannot refresh.
        credential = {}
        status_error = str(exc)
    method = credential.get('method', '')
    if (not method or options.get('environment_first')) and any(os.environ.get(key) for key in options.get('token_env', ())):
        method = 'existing access'
    if not method and not status_error and callable(getattr(module, 'auth_status', None)) and module.auth_status():
        method = 'existing access'
    return {'name': name, **{k: v for k, v in options.items()
                               if k in ('token_method', 'origin_required')},
            'device': False, 'method': method,
            'status_error': status_error}


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

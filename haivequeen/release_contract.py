"""Conservative supported-HQ compatibility from authenticated installed sources.

The first supported family has identical shared-resource consumers, protocol and
requirements. It may differ in files outside the runtime projection, and in
unreferenced inert Python modules. Every runtime file is included by default;
this is deliberately not an inference from schema numbers or release text.
Changed consumers remain unknown until an explicit compatible migration model
is implemented. This is cooperative HQ coordination, not a same-user sandbox.
"""
import ast
import importlib.metadata
from pathlib import Path
import sys
import tomllib

from . import release_staging, release_tree, version_leases, version_layout


def _projection(directory):
    directory = Path(directory)
    project = tomllib.loads((directory / 'pyproject.toml').read_text(encoding='utf-8'))
    project.get('project', {}).pop('version', None)
    files = {}
    for folder in ('haivequeen', 'roles'):
        base = directory / folder
        if not base.is_dir() or base.is_symlink():
            raise ValueError('missing shared consumer directory')
        for path in base.rglob('*'):
            if '__pycache__' in path.parts or path.suffix == '.pyc':
                continue
            if path.is_symlink():
                raise ValueError('linked shared consumer')
            if path.is_file():
                files[path.relative_to(directory).as_posix()] = path.read_bytes()
    if 'haivequeen/cli.py' not in files or 'haivequeen/stable_launcher.py' not in files:
        raise ValueError('missing supported HQ launch protocol')
    # Inert, unreferenced modules cannot access data or change the behavior of
    # the included consumers. Imports, calls, attributes, functions, executable
    # expressions and special names are never eligible for this exclusion.
    # A reference of any spelling in another module retains the entire module;
    # false positives merely make a release unsupported, never compatible.
    for name, data in list(files.items()):
        if not name.startswith('haivequeen/') or not name.endswith('.py'):
            continue
        stem = Path(name).stem
        if stem.startswith('_') or any(stem.encode() in other for key, other in files.items()
                                       if key != name and key.endswith('.py')):
            continue
        module = ast.parse(data)
        inert = True
        for node in module.body:
            if isinstance(node, ast.Expr) and isinstance(node.value, ast.Constant) and isinstance(node.value.value, str):
                continue
            if (isinstance(node, ast.Assign) and all(isinstance(t, ast.Name) and not t.id.startswith('_') for t in node.targets)
                    and isinstance(node.value, ast.Constant) and type(node.value.value) in (str, int, bool, type(None))):
                continue
            inert = False
            break
        if inert:
            files.pop(name)
    return project, files


def _environment(project):
    # This explicit first support profile is the distributed HQ dependency
    # contract. Unsupported requirements do not become compatible by equality.
    value = project['project']
    if value['requires-python'] != '>=3.11' or sys.version_info < (3, 11):
        return False
    if value['dependencies'] != ['tomlkit>=0.13,<1']:
        return False
    parts = importlib.metadata.version('tomlkit').split('.')
    version = tuple(int(p) for p in parts[:2])
    return (0, 13) <= version < (1, 0)


def prepare(root, repository, fetch=None):
    """Resolve authority without holding the launch lock or stopping HQ work.

    Publication revalidates the complete inventory and each installation against
    this authenticated evidence locally. Any change loses eligibility.
    """
    try:
        evidence = {}
        for path in (Path(root) / 'versions').iterdir():
            version_layout._version(path.name)
            if path.is_symlink() or not path.is_dir():
                return {}
            verified = release_staging.verify_installed(path, repository, path.name, fetch=fetch, details=True)
            if not isinstance(verified, dict):
                return {}
            evidence[path.name] = verified
        return evidence
    except (OSError, ValueError, TypeError):
        return {}


def assess(root, target_version, expected_active, authority):
    """Recompute eligibility while the caller holds the lifetime lock.

    Cover all installed versions, including launchable but presently idle ones;
    therefore a queued old Queen launch cannot introduce an unchecked version
    after publication. Reservations naming absent versions reject eligibility.
    The fixed launcher and every supported version use the same lifetime lock
    through selection/reservation/spawn/publication. Never acquire that lock
    recursively here: Windows byte locks are not reentrant.
    """
    root = Path(root)
    unknown = {'compatible': False, 'known': False, 'reason': 'Release shared compatibility is unknown', 'evidence': {}}
    try:
        active = (root / 'active-version').read_text(encoding='utf-8').strip()
        if active != expected_active:
            raise ValueError('active version changed')
        paths = list((root / 'versions').iterdir())
        versions = {}
        for path in paths:
            version_layout._version(path.name)
            if path.is_symlink() or not path.is_dir():
                raise ValueError('unknown installed version layout')
            versions[path.name] = path
        if set(versions) != set(authority):
            raise ValueError('installed version inventory lacks authenticated evidence')
        if active not in versions or target_version not in versions:
            raise ValueError('missing active or target version')
        leases = version_leases._load(root)
        for record in leases.values():
            if not isinstance(record, dict) or record.get('version') not in versions:
                raise ValueError('reservation for an unverified version')
        reference = Path(__file__).resolve().parent.parent
        supported_project, supported_files = _projection(reference)
        if not _environment(supported_project):
            raise ValueError('unsupported shared dependency environment')
        # install_launcher writes the source through text I/O, normalizing
        # checkout line endings. Compare that exact installed text projection.
        protocol = supported_files['haivequeen/stable_launcher.py'].decode('utf-8').replace('\r\n', '\n')
        if (root / 'launcher.py').read_text(encoding='utf-8') != protocol:
            raise ValueError('unknown fixed HQ launch protocol')
        evidence = {}
        for version, path in sorted(versions.items()):
            verified = authority[version]
            if not isinstance(verified, dict) or not release_tree.verify_installation(
                    path, verified['entries'], verified['tree'], verified['generated']):
                raise ValueError('installed version lacks authenticated identity: ' + version)
            project, files = _projection(path)
            if project != supported_project or not _environment(project):
                raise ValueError('unsupported shared dependency or package contract: ' + version)
            if files != supported_files:
                raise ValueError('changed or unsupported shared consumers: ' + version)
            evidence[version] = {'commit': verified['commit'], 'tree': verified['tree']}
        return {'compatible': True, 'known': True, 'reason': '', 'evidence': evidence}
    except (OSError, ValueError, TypeError, KeyError, SyntaxError, importlib.metadata.PackageNotFoundError) as exc:
        unknown['reason'] += ': ' + str(exc)
        return unknown

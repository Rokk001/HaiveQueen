"""Conservative supported-HQ compatibility from authenticated installed sources.

The first supported family has identical shared-resource consumers, protocol and
requirements. It may differ in files outside the runtime projection, and in
unreferenced inert Python modules. Every runtime file is included by default;
this is deliberately not an inference from schema numbers or release text.
The explicit database profiles below additionally support one reversible,
incompatible consumer transition. Other changed consumers remain unknown.
This is cooperative HQ coordination, not a same-user sandbox.
"""
import ast
import importlib.metadata
import json
import os
from pathlib import Path
import sys
import tomllib

from . import release_staging, release_tree, version_leases, version_layout


def _projection(directory):
    directory = Path(directory)
    project = tomllib.loads((directory / 'pyproject.toml').read_text(encoding='utf-8'))
    project.get('project', {}).pop('version', None)
    files = {'schema.sql': (directory / 'schema.sql').read_bytes()}
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
        _, supported_files = _database_profile(supported_files)
        if not _environment(supported_project):
            raise ValueError('unsupported shared dependency environment')
        # install_launcher writes the source through text I/O, normalizing
        # checkout line endings. Compare that exact installed text projection.
        protocol = supported_files['haivequeen/stable_launcher.py'].decode('utf-8').replace('\r\n', '\n')
        if (root / 'launcher.py').read_text(encoding='utf-8') != protocol:
            raise ValueError('unknown fixed HQ launch protocol')
        evidence = {}
        profiles = {}
        for version, path in sorted(versions.items()):
            verified = authority[version]
            if not isinstance(verified, dict) or not release_tree.verify_installation(
                    path, verified['entries'], verified['tree'], verified['generated']):
                raise ValueError('installed version lacks authenticated identity: ' + version)
            project, files = _projection(path)
            if project != supported_project or not _environment(project):
                raise ValueError('unsupported shared dependency or package contract: ' + version)
            profile, files = _database_profile(files)
            profiles[version] = profile
            if files != supported_files:
                raise ValueError('changed or unsupported shared consumers: ' + version)
            evidence[version] = {'commit': verified['commit'], 'tree': verified['tree']}
        target_profile = profiles[target_version]
        if target_profile == DATABASE_PROFILES[1]:
            if any(profile not in DATABASE_PROFILES for profile in profiles.values()):
                raise ValueError('database transition lacks explicit supported old contract')
            affected = sorted(v for v, profile in profiles.items() if profile == DATABASE_PROFILES[0])
            if affected:
                if profiles[active] != DATABASE_PROFILES[0]:
                    raise ValueError('unsupported database transition direction')
                return {'compatible': False, 'known': True, 'evidence': evidence,
                        'affected_versions': affected,
                        'reason': 'Database column providers_usage.actions becomes actions_json; older HQ usage writers require actions (affected versions: ' + ', '.join(affected) + ')'}
        elif any(profile == DATABASE_PROFILES[1] for profile in profiles.values()):
            raise ValueError('unsupported reverse database transition')
        return {'compatible': True, 'known': True, 'reason': '', 'evidence': evidence}
    except (OSError, ValueError, TypeError, KeyError, SyntaxError, importlib.metadata.PackageNotFoundError) as exc:
        unknown['reason'] += ': ' + str(exc)
        return unknown

# Explicit support for one reversible schema/consumer transition. These exact
# transforms describe executable consumers and migration, not metadata trust.
DATABASE_PROFILES = ('usage-actions-v1', 'usage-actions-json-v2')
DATABASE_CHANGES = {
    'haivequeen/db.py': [
        ('SCHEMA_VERSION = 17', 'SCHEMA_VERSION = 18'),
        ('("providers_usage", "actions", "TEXT")', '("providers_usage", "actions_json", "TEXT")'),
        ('    if version < SCHEMA_VERSION:\n', '    if version == 17:\n        conn.execute("ALTER TABLE providers_usage RENAME COLUMN actions TO actions_json")\n    if version < SCHEMA_VERSION:\n')],
    'haivequeen/runtime.py': [
        ('"duration_s", "actions", "error"]', '"duration_s", "actions_json", "error"]\n    f["actions_json"] = f.get("actions")'),
        ('sql, args = "SELECT * FROM providers_usage WHERE 1=1", []', 'sql, args = "SELECT *, actions_json AS actions FROM providers_usage WHERE 1=1", []')],
    'haivequeen/legacy.py': [('"context, duration_s, actions, error) VALUES', '"context, duration_s, actions_json, error) VALUES')],
    'schema.sql': [('    actions TEXT,', '    actions_json TEXT,')],
}


def _database_profile(files):
    """Canonicalize only the explicitly supported reversible column rename.

    The authenticated descriptor selects a support model; exact source/schema
    comparison below must independently establish every modelled consumer.
    Unknown edits never become known incompatibility from a schema number.
    """
    files = dict(files)
    descriptor = files.pop('haivequeen/shared-contract.json', None)
    profile = None
    if descriptor is not None:
        value = json.loads(descriptor)
        if (not isinstance(value, dict) or set(value) != {'format', 'database'}
                or type(value['format']) is not int or value['format'] != 1
                or value['database'] not in DATABASE_PROFILES):
            raise ValueError('unsupported shared-contract description')
        profile = value['database']
    for name in list(files):
        if name.endswith(('.py', '.sql')):
            files[name] = files[name].replace(b'\r\n', b'\n')
    if profile == DATABASE_PROFILES[1]:
        for name, changes in DATABASE_CHANGES.items():
            text = files[name].decode('utf-8')
            for old, new in reversed(changes):
                if text.count(new) != 1:
                    raise ValueError('database support contradicts packaged migration/consumer: ' + name)
                text = text.replace(new, old)
            files[name] = text.encode('utf-8')
    return profile, files


def drained(root, installation_records, installation_token=None):
    """Check supported launch reservations under the caller's lifetime lock.

    Installation reservations do not execute HQ work. Their complete records
    must match the authenticated assessment's inventory. Unpublished launches,
    unknown identities and live processes all prevent incompatible publication.
    This checks HQ's launch protocol, not arbitrary same-user native activity.
    """
    leases = version_leases._load(root)
    current_installs = {k: v for k, v in leases.items() if v.get('installation') is True}
    if current_installs != installation_records:
        return False
    if installation_token is not None and installation_token not in current_installs:
        return False
    for record in leases.values():
        if record.get('installation') is True:
            continue
        pid, identity = record.get('pid'), record.get('identity')
        if not isinstance(pid, int) or pid <= 0:
            return False
        current = version_leases.platform.process_identity(pid)
        if current and identity and current != identity:
            continue
        if not process_ended(pid):
            return False
    return True


def process_ended(pid):
    """Prove exit even when a parent retains a completed process object.

    On Windows OpenProcess can still succeed after exit while a Popen handle
    remains open. A signalled process handle is authoritative completion, unlike
    failure of a best-effort liveness query. Access failures remain unknown.
    """
    if os.name == 'nt':
        import ctypes
        kernel = ctypes.WinDLL('kernel32', use_last_error=True)
        kernel.OpenProcess.restype = ctypes.c_void_p
        handle = kernel.OpenProcess(0x00101000, False, pid)
        if not handle:
            return ctypes.get_last_error() == 87
        try:
            return kernel.WaitForSingleObject(ctypes.c_void_p(handle), 0) == 0
        finally:
            kernel.CloseHandle(ctypes.c_void_p(handle))
    return (version_leases.platform.process_absent(pid)
            or version_leases.platform.is_linux() and version_leases.platform._linux_zombie(pid))


def restore_database(conn):
    """Undo the supported rename after the target core has relinquished use.

    Reverse DDL preserves rows written during the failed round; copying an old
    database snapshot back would silently discard those updates and owner data.
    The caller keeps scheduling paused until an old completed round is verified.
    """
    from . import db
    with db.transaction(conn):
        version = conn.execute('PRAGMA user_version').fetchone()[0]
        columns = {r[1] for r in conn.execute('PRAGMA table_info(providers_usage)')}
        if version not in (17, 18):
            raise ValueError('cannot restore unknown database schema')
        if 'actions_json' in columns and 'actions' not in columns:
            conn.execute('ALTER TABLE providers_usage RENAME COLUMN actions_json TO actions')
        elif 'actions' not in columns or 'actions_json' in columns:
            raise ValueError('cannot restore unknown usage columns')
        conn.execute('PRAGMA user_version = 17')

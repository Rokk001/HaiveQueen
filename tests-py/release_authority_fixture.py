"""Complete local GitHub authority for isolated release process tests."""
import io
import json
from pathlib import Path
import zipfile

from haivequeen.release_tree import _object_hash


def authority(files, version):
    """Construct real Git blob/tree identities, independently of local markers."""
    entries = []
    def tree(prefix):
        names = {}
        for path in files:
            if path.startswith(prefix):
                rest = path[len(prefix):]
                name, separator, _ = rest.partition('/')
                names[name] = bool(separator)
        children = []
        for name, directory in sorted(names.items(), key=lambda x: (x[0] + ('/' if x[1] else '')).encode()):
            path = prefix + name
            mode, kind = ('040000', 'tree') if directory else ('100644', 'blob')
            sha = tree(path + '/') if directory else _object_hash(b'blob', files[path])
            entries.append(dict(path=path, mode=mode, type=kind, sha=sha))
            children.append((b'40000' if directory else b'100644') + b' ' + name.encode() + b'\0' + bytes.fromhex(sha))
        return _object_hash(b'tree', b''.join(children))
    root = tree('')
    commit = _object_hash(b'commit', ('tree ' + root + '\n\nrelease ' + version + '\n').encode())
    output = io.BytesIO()
    with zipfile.ZipFile(output, 'w', zipfile.ZIP_DEFLATED) as archive:
        for path, data in files.items():
            archive.writestr('release/' + path, data)
    return dict(commit=commit, tree=root, entries=entries, archive=output.getvalue())


def responses(versions):
    result = {}
    for version, item in versions.items():
        result['/git/ref/tags/v' + version] = json.dumps({'object': {'type': 'commit', 'sha': item['commit']}}).encode()
        result['/git/commits/' + item['commit']] = json.dumps({'sha': item['commit'], 'tree': {'sha': item['tree']}}).encode()
        result['/git/trees/' + item['tree'] + '?recursive=1'] = json.dumps({'sha': item['tree'], 'truncated': False, 'tree': item['entries']}).encode()
        result['/releases/tags/v' + version] = json.dumps({'tag_name': 'v' + version, 'draft': False, 'prerelease': False, 'body': ''}).encode()
        result['/zipball/' + item['commit']] = item['archive']
    return result


def package_files(source):
    source = Path(source)
    files = {'pyproject.toml': (source / 'pyproject.toml').read_bytes()}
    files['schema.sql'] = (source / 'schema.sql').read_bytes()
    for folder in ('haivequeen', 'roles'):
        for path in (source / folder).rglob('*'):
            if path.is_file() and '__pycache__' not in path.parts and path.suffix != '.pyc':
                files[path.relative_to(source).as_posix()] = path.read_bytes()
    return files


def database_break(files):
    """A real schema/consumer rename, not a compatibility boolean."""
    files = dict(files)
    files['haivequeen/shared-contract.json'] = b'{"format":1,"database":"usage-actions-json-v2"}\n'
    changes = {
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
    for name, replacements in changes.items():
        text = files[name].decode().replace('\r\n', '\n')
        for old, new in replacements:
            assert text.count(old) == 1, (name, old)
            text = text.replace(old, new)
        files[name] = text.encode()
    return files

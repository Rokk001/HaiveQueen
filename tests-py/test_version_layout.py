import json
import os
from pathlib import Path
import shutil
import subprocess
import sys

import tempfile
import unittest
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from haivequeen import version_layout as layout


def _release(path, version):
    package = path / 'haivequeen'
    package.mkdir(parents=True)
    (package / '__init__.py').write_text('')
    for name in ('platform.py', 'console_tree.py'):
        shutil.copy(Path(layout.__file__).with_name(name), package / name)
    (package / 'cli.py').write_text('''import json, sys
from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from haivequeen.platform import data_dir
print(json.dumps({"version": %r, "data": str(data_dir()), "args": sys.argv[1:]}))
''' % version)
    (path / 'resource.txt').write_text(version)


def _invoke(root, name, env):
    command = root / (name + '.cmd' if os.name == 'nt' else name)
    return json.loads(subprocess.check_output([str(command), 'status'], env=env, text=True))


def check_versions_and_stable_launchers(tmp_path):
    root = tmp_path / 'fixed place'
    one, two = tmp_path / 'source1', tmp_path / 'source2'
    _release(one, '1.0'); _release(two, '2.0')
    layout.install_version(root, '1.0', one)
    layout.install_version(root, '2.0', two)
    layout.install_launcher(root)
    launchers = {p.name: p.read_bytes() for p in root.iterdir() if p.is_file()}
    env = dict(os.environ, BZZ_DATA_DIR=str(tmp_path / 'shared'))
    layout.set_active(root, '1.0')
    first = _invoke(root, 'bzz', env)
    start1 = _invoke(root, 'start', env)
    layout.set_active(root, '2.0')
    second = _invoke(root, 'bzz', env)
    start2 = _invoke(root, 'start', env)
    assert first['version'] == start1['version'] == '1.0'
    assert second['version'] == start2['version'] == '2.0'
    assert first['data'] == second['data'] == str(tmp_path / 'shared')
    assert start1['args'] == start2['args'] == ['start', 'status']
    assert all((root / name).read_bytes() == data for name, data in launchers.items())
    assert (root / 'versions' / '1.0' / 'resource.txt').read_text() == '1.0'
    assert (root / 'versions' / '2.0' / 'resource.txt').read_text() == '2.0'
    try:
        layout.install_version(root, '1.0', two)
        raise AssertionError('overwrote installed version')
    except FileExistsError:
        pass
    try:
        layout.set_active(root, '../other')
        raise AssertionError('accepted invalid version')
    except ValueError:
        pass
    assert (root / 'active-version').read_text().strip() == '2.0'


class TestVersionLayout(unittest.TestCase):
    def test_versions_and_stable_launchers(self):
        with tempfile.TemporaryDirectory() as directory:
            check_versions_and_stable_launchers(Path(directory))


if __name__ == '__main__':
    unittest.main()

import hashlib
import tempfile
import unittest
from pathlib import Path

from haivequeen import release_tree


def obj(kind, data):
    return hashlib.sha1(kind + b' ' + str(len(data)).encode() + b'\0' + data).hexdigest()


class ReleaseTreeTests(unittest.TestCase):
    def test_tree_binds_files_structure_and_root(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            (root / 'pkg').mkdir()
            (root / 'pkg' / 'cli.py').write_bytes(b'code')
            blob = obj(b'blob', b'code')
            subtree = obj(b'tree', b'100644 cli.py\0' + bytes.fromhex(blob))
            tree = obj(b'tree', b'40000 pkg\0' + bytes.fromhex(subtree))
            entries = [{'path': 'pkg', 'mode': '040000', 'type': 'tree', 'sha': subtree},
                       {'path': 'pkg/cli.py', 'mode': '100644', 'type': 'blob', 'sha': blob}]
            self.assertTrue(release_tree.verify_tree(root, entries, tree))
            self.assertFalse(release_tree.verify_tree(root, entries, '0' * 40))
            (root / 'pkg' / 'cli.py').write_bytes(b'tampered')
            self.assertFalse(release_tree.verify_tree(root, entries, tree))
            (root / 'pkg' / 'cli.py').write_bytes(b'code')
            (root / 'extra').write_bytes(b'code')
            self.assertFalse(release_tree.verify_tree(root, entries, tree))
            (root / 'extra').unlink()
            self.assertFalse(release_tree.verify_tree(root, entries[:-1], tree))
            self.assertFalse(release_tree.verify_tree(root, entries + [entries[0]], tree))

    def test_rejects_uninspectable_git_entries(self):
        with tempfile.TemporaryDirectory() as temp:
            for mode, kind in [('120000', 'blob'), ('160000', 'commit')]:
                entry = {'path': 'file', 'mode': mode, 'type': kind, 'sha': '0' * 40}
                self.assertFalse(release_tree.verify_tree(temp, [entry], '0' * 40))

import io
import json
import tempfile
import unittest
from unittest import mock
import shutil
import zipfile
from pathlib import Path

from haivequeen import release_staging, release_tree


class ProvenanceTests(unittest.TestCase):
    def setUp(self):
        self.files = {'haivequeen/cli.py': b'code'}
        blob = lambda data: release_tree._object_hash(b'blob', data)
        cli = blob(self.files['haivequeen/cli.py'])
        subtree = release_tree._object_hash(b'tree', b'100644 cli.py\0' + bytes.fromhex(cli))
        self.tree = release_tree._object_hash(b'tree', b'40000 haivequeen\0' + bytes.fromhex(subtree))
        self.entries = [dict(path='haivequeen', mode='040000', type='tree', sha=subtree),
                        dict(path='haivequeen/cli.py', mode='100644', type='blob', sha=cli)]
        self.commit = 'a' * 40
        self.ref = {'object': {'type': 'commit', 'sha': self.commit}}
        self.commit_data = {'sha': self.commit, 'tree': {'sha': self.tree}}
        self.tree_data = {'sha': self.tree, 'truncated': False, 'tree': self.entries}

    def fetch(self, url):
        if '/git/ref/' in url:
            return json.dumps(self.ref).encode()
        if '/git/commits/' in url:
            return json.dumps(self.commit_data).encode()
        if '/git/trees/' in url:
            return json.dumps(self.tree_data).encode()
        if '/releases/' in url:
            return b'{}'
        self.assertTrue(url.endswith('/zipball/' + self.commit))
        output = io.BytesIO()
        with zipfile.ZipFile(output, 'w') as z:
            for path, data in self.files.items():
                z.writestr('project/' + path, data)
        return output.getvalue()

    def stage(self, root):
        return release_staging.stage_release(root, 'example/project', '2.0.0', self.fetch)

    def test_valid_tree_and_generated_markers(self):
        with tempfile.TemporaryDirectory() as root:
            installed = self.stage(root)
            record = json.loads((installed / '.haivequeen-release-provenance.json').read_text())
            self.assertEqual(record, {'schema': 1, 'repository': 'example/project', 'commit': self.commit,
                                      'tree': self.tree, 'installed_trust': 'unknown'})
            self.assertEqual((installed / '.haivequeen-release-version').read_text(), '2.0.0\n')
            self.assertTrue(release_staging.verify_installed(installed, 'example/project', '2.0.0', self.fetch))
            (installed / 'haivequeen/cli.py').write_text('mutated')
            self.assertFalse(release_staging.verify_installed(installed, 'example/project', '2.0.0', self.fetch))
            self.assertEqual(record['installed_trust'], 'unknown')

    def test_rejects_bad_authority_and_archive(self):
        cases = ('commit', 'tree', 'truncated', 'missing', 'unsupported', 'tampered', 'marker')
        for case in cases:
            with self.subTest(case=case), tempfile.TemporaryDirectory() as root:
                self.setUp()
                if case == 'commit': self.commit_data['sha'] = 'b' * 40
                if case == 'tree': self.tree_data['sha'] = 'b' * 40
                if case == 'truncated': self.tree_data['truncated'] = True
                if case == 'missing': del self.tree_data['tree']
                if case == 'unsupported': self.entries[1]['mode'] = '120000'
                if case == 'tampered': self.files['haivequeen/cli.py'] = b'bad'
                if case == 'marker': self.files['.haivequeen-release-version'] = b'bad'
                with self.assertRaises(ValueError): self.stage(root)
                self.assertFalse((Path(root) / 'versions/2.0.0').exists())

    def test_copy_omission_is_rejected_before_publication(self):
        real_copy = shutil.copytree
        def omit(*args, **kwargs):
            result = real_copy(*args, **kwargs)
            if Path(args[1]).name.startswith('.install-'):
                (Path(args[1]) / 'haivequeen/cli.py').unlink()
            return result
        with tempfile.TemporaryDirectory() as root, mock.patch('haivequeen.version_layout.shutil.copytree', side_effect=omit):
            with self.assertRaisesRegex(ValueError, 'installed release'):
                self.stage(root)
            self.assertFalse((Path(root) / 'versions/2.0.0').exists())

    def test_later_verification_rejects_wrong_authority_extra_and_legacy(self):
        with tempfile.TemporaryDirectory() as root:
            installed = self.stage(root)
            check = lambda: release_staging.verify_installed(installed, 'example/project', '2.0.0', self.fetch)
            self.assertTrue(check())
            (installed / 'extra').write_text('unexpected')
            self.assertFalse(check())
            (installed / 'extra').unlink()
            self.ref['object']['sha'] = 'b' * 40
            self.assertFalse(check())
            self.ref['object']['sha'] = self.commit
            self.tree_data['truncated'] = True
            self.assertFalse(check())
            self.tree_data['truncated'] = False
            (installed / '.haivequeen-release-provenance.json').unlink()
            self.assertFalse(check())

    def test_generated_marker_collision_is_rejected(self):
        data = b'old'
        marker = release_tree._object_hash(b'blob', data)
        tree = release_tree._object_hash(b'tree', b'100644 marker\0' + bytes.fromhex(marker))
        entries = [dict(path='marker', mode='100644', type='blob', sha=marker)]
        with tempfile.TemporaryDirectory() as root:
            (Path(root) / 'marker').write_bytes(data)
            self.assertFalse(release_tree.verify_installation(root, entries, tree, {'marker': data}))


if __name__ == '__main__':
    unittest.main()

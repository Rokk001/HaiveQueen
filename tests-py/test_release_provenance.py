import io
import json
import tempfile
import unittest
import zipfile
from pathlib import Path

from haivequeen import release_staging, release_tree


class ProvenanceTests(unittest.TestCase):
    def setUp(self):
        self.files = {'haivequeen/cli.py': b'code', '.haivequeen-release-version': b'old'}
        blob = lambda data: release_tree._object_hash(b'blob', data)
        cli = blob(self.files['haivequeen/cli.py'])
        marker = blob(self.files['.haivequeen-release-version'])
        subtree = release_tree._object_hash(b'tree', b'100644 cli.py\0' + bytes.fromhex(cli))
        self.tree = release_tree._object_hash(b'tree', b'100644 .haivequeen-release-version\0' + bytes.fromhex(marker) + b'40000 haivequeen\0' + bytes.fromhex(subtree))
        self.entries = [dict(path='haivequeen', mode='040000', type='tree', sha=subtree),
                        dict(path='haivequeen/cli.py', mode='100644', type='blob', sha=cli),
                        dict(path='.haivequeen-release-version', mode='100644', type='blob', sha=marker)]
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
            (installed / 'haivequeen/cli.py').write_text('mutated')
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


if __name__ == '__main__':
    unittest.main()

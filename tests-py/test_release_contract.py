import io
import json
import tempfile
import unittest
import zipfile
from urllib.error import HTTPError
from unittest.mock import patch

from haivequeen import release_compat, release_staging, releases


class ContractTests(unittest.TestCase):
    def test_published_decisions_are_verified_at_staging(self):
        sha = 'a' * 40
        unknown = releases.release_decision('o/r', '2.0.1', sha)
        positive = dict(unknown, compatible_with_older=True, reason='', evidence='Reviewed integration a')
        incompatible = dict(unknown, reason='Database migration', evidence='Migration review a')
        for decision in (unknown,):
            with self.subTest(decision=decision), tempfile.TemporaryDirectory() as root:
                ref = {'object': {'type': 'commit', 'sha': sha}}
                saved = {}
                def api(req, timeout):
                    if req.full_url.endswith('/git/ref/tags/v2.0.1'):
                        return io.BytesIO(json.dumps(ref).encode())
                    if req.full_url.endswith('/releases/tags/v2.0.1'):
                        if not saved:
                            raise HTTPError(req.full_url, 404, 'missing', {}, None)
                        return io.BytesIO(json.dumps(saved).encode())
                    saved.update(json.loads(req.data))
                    return io.BytesIO(json.dumps(saved).encode())
                with patch('haivequeen.releases.urlopen', side_effect=api):
                    releases.publish_release('o/r', '2.0.1', sha, 'token', decision)
                    releases.publish_release('o/r', '2.0.1', sha, 'token', decision)
                    with self.assertRaises(ValueError):
                        releases.publish_release('o/r', '2.0.1', sha, 'token', dict(decision, reason='conflict'))
                archive = io.BytesIO()
                with zipfile.ZipFile(archive, 'w') as z:
                    z.writestr('root/haivequeen/cli.py', '')
                    z.writestr('root/haivequeen-update.json', json.dumps(positive))
                def fetch(url):
                    if '/git/ref/' in url:
                        return json.dumps(ref).encode()
                    if '/releases/tags/' in url:
                        return json.dumps(saved).encode()
                    self.assertTrue(url.endswith('/zipball/' + sha))
                    return archive.getvalue()
                path = release_staging.stage_release(root, 'o/r', '2.0.1', fetch)
                self.assertEqual(release_compat.compatibility(path), {'compatible': decision['compatible_with_older'], 'reason': decision['reason']})

    def test_free_text_evidence_cannot_authorize_a_release(self):
        sha = 'a' * 40
        unknown = releases.release_decision('o/r', '2.0.1', sha)
        for decision in (dict(unknown, compatible_with_older=True, reason='', evidence='reviewed'),
                         dict(unknown, reason='migration', evidence='reviewed')):
            with self.subTest(decision=decision):
                self.assertFalse(releases.valid_decision(decision, 'o/r', '2.0.1', sha))
                with self.assertRaises(ValueError):
                    releases.publish_release('o/r', '2.0.1', sha, 'token', decision)

    def test_stale_or_missing_metadata_cannot_authorize_archive(self):
        sha = 'a' * 40
        archive = io.BytesIO()
        with zipfile.ZipFile(archive, 'w') as z:
            z.writestr('root/haivequeen/cli.py', '')
            z.writestr('root/haivequeen-update.json', '{"compatible_with_older": true}')
        for body in (None, 'bad', json.dumps(dict(releases.release_decision('o/r', '2.0.1', sha), compatible_with_older=True, evidence='forged review')), json.dumps(releases.release_decision('o/r', '2.0.0', sha)),
                     json.dumps(releases.release_decision('other/r', '2.0.1', sha)),
                     json.dumps(releases.release_decision('o/r', '2.0.1', 'b' * 40))):
            with self.subTest(body=body), tempfile.TemporaryDirectory() as root:
                def fetch(url):
                    if '/git/ref/' in url:
                        return json.dumps({'object': {'type': 'commit', 'sha': sha}}).encode()
                    if '/releases/tags/' in url:
                        return json.dumps({'tag_name': 'v2.0.1', 'body': body}).encode()
                    return archive.getvalue()
                path = release_staging.stage_release(root, 'o/r', '2.0.1', fetch)
                self.assertEqual(release_compat.compatibility(path), {'compatible': False, 'reason': release_compat.UNKNOWN_REASON})

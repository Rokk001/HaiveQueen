import os
import sys
import tempfile
import time
import unittest
from pathlib import Path
from unittest import mock

from haivequeen import web_login


class WebLoginTests(unittest.TestCase):
    def test_codex_uses_device_login(self):
        self.assertEqual(web_login.command({'protocol': 'codex', 'command': 'codex'}), 'codex login --device-auth')

    def test_login_streams_link_and_code_and_accepts_browser_reply(self):
        with tempfile.TemporaryDirectory() as folder:
            script = Path(folder) / 'login.py'
            script.write_text('print("https://example.invalid/login CODE-123", flush=True)\nprint("received:" + input(), flush=True)\n', encoding='utf-8')
            provider = {'name': 'custom', 'protocol': 'custom', 'loginCommand': '"%s" -u "%s"' % (sys.executable, script),
                        'profileEnv': {'HQ_LOGIN_TEST_HOME': '{home}'}, 'loginFiles': ['auth.json']}
            with mock.patch('haivequeen.profile.prepare', return_value=True), mock.patch('haivequeen.profile.environment', return_value=dict(os.environ)):
                result = web_login.start(provider)
                self.addCleanup(web_login.close)
                token = result['id']
                deadline = time.monotonic() + 5
                while 'CODE-123' not in web_login.status(token)['output'] and time.monotonic() < deadline:
                    time.sleep(0.02)
                self.assertIn('https://example.invalid/login', web_login.status(token)['links'])
                self.assertEqual(web_login.start(provider)['id'], token)
                web_login.send(token, 'browser-code')
                while web_login.status(token)['running'] and time.monotonic() < deadline:
                    time.sleep(0.02)
                result = web_login.status(token)
                self.assertFalse(result['running'])
                self.assertEqual(result['exit'], 0)
                self.assertIn('received:browser-code', result['output'])
                with self.assertRaisesRegex(ValueError, 'no longer running'):
                    web_login.send(token, 'late')

    def test_provider_without_login_does_not_launch_a_process(self):
        with mock.patch('subprocess.Popen') as popen, self.assertRaisesRegex(ValueError, 'no configured login'):
            web_login.start({'name': 'unsupported', 'protocol': 'unsupported'})
        popen.assert_not_called()

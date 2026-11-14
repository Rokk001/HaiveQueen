"""The shared emblem is used in the browser header and icon assets."""
import base64
from pathlib import Path
import struct
import unittest

import isolation
from haivequeen import web_ui, web_access


class Branding(unittest.TestCase):
    def test_header_uses_transparent_emblem_and_live_wordmark(self):
        root = Path(web_ui.__file__).parent
        page = web_ui.page('test', language='English')
        logo = base64.b64encode((root / 'web-login-logo.png').read_bytes()).decode()
        self.assertIn('class="header-queen" alt="" src="data:image/png;base64,' + logo, page)
        self.assertIn('class="header-wordmark"', page)
        self.assertNotIn('class="brand-banner"', page)
        self.assertIn('id="honeycomb"', page)

    def test_login_and_dashboard_share_favicon(self):
        root = Path(web_ui.__file__).parent
        favicon = base64.b64encode((root / 'web-icon.png').read_bytes()).decode()
        for page in (web_ui.page('test'), web_access.page()):
            self.assertIn('href="data:image/png;base64,' + favicon, page)

    def test_tray_contains_small_and_high_dpi_sizes(self):
        data = Path(web_ui.__file__).with_name('app.ico').read_bytes()
        reserved, kind, count = struct.unpack_from('<HHH', data)
        self.assertEqual((reserved, kind), (0, 1))
        sizes = {data[6 + i * 16] or 256 for i in range(count)}
        self.assertTrue({16, 20, 24, 32, 40, 48, 64, 128, 256} <= sizes)

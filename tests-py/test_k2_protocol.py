import unittest
from haivequeen import desktop, legacy, routine


class ProtocolCompatibilityTests(unittest.TestCase):
    def test_counts_accept_english_and_german(self):
        for text in ("Open requirements: 0", "Offene Anforderungen: 0"):
            self.assertEqual(routine.COUNT_RE.search(text).group(1), "0")
            self.assertEqual(routine.lock_reason(text), "")

    def test_queue_statuses_accept_both_languages(self):
        for text, expected in (("blocked", "blocked"), ("blockiert", "blocked"),
                               ("integrated", "integrated"), ("integriert", "integrated"),
                               ("withdrawn", "withdrawn"), ("zurückgezogen", "withdrawn")):
            self.assertEqual(legacy.queue_status(text), expected)

    def test_desktop_languages_and_fallback(self):
        result = {"ok": True}
        self.assertEqual(desktop.result_text("start", result, "German"), "HQ wurde gestartet.")
        self.assertEqual(desktop.result_text("start", result, "English"), "HQ has started.")
        self.assertEqual(desktop.result_text("start", result, "French"), "HQ has started.")

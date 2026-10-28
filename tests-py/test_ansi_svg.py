"""Tests of haivequeen.ansi_svg: colours, bold, escaping and the size of the picture."""
import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from haivequeen import ansi_svg  # noqa: E402


class AnsiSvgTest(unittest.TestCase):
    """Convert small ANSI texts."""

    def test_colours_and_reset(self):
        """A basic colour, a true colour and a reset give the matching fills."""
        svg = ansi_svg.to_svg("\x1b[32mgreen\x1b[0m plain \x1b[38;2;255;111;181mpink\x1b[0m")
        self.assertIn('<tspan fill="#0dbc79">green</tspan>', svg)
        self.assertIn('<tspan fill="#d4d4d4"> plain </tspan>', svg)
        self.assertIn('<tspan fill="#ff6fb5">pink</tspan>', svg)

    def test_bold_escape_and_lines(self):
        """Bold sets the weight, markup characters are escaped and every line is one text element."""
        svg = ansi_svg.to_svg("\x1b[1ma<b&c\x1b[0m\nsecond")
        self.assertIn('font-weight="bold">a&lt;b&amp;c</tspan>', svg)
        self.assertEqual(svg.count("<text "), 2)
        self.assertNotIn("\x1b", svg)

    def test_colour_carries_over_lines(self):
        """A colour set on one line stays on the next line until reset."""
        svg = ansi_svg.to_svg("\x1b[31mone\ntwo\x1b[0m")
        self.assertEqual(svg.count('fill="#cd3131"'), 2)


if __name__ == "__main__":
    unittest.main()

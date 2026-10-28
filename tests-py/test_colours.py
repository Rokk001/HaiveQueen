"""Agent colours: every running agent gets a colour that no other running agent has, and the palette colours look clearly different."""
import colorsys
import unittest

from orchtest import RoundCase, runtime
from orchlib import loop


def hue(hex_colour):
    """Hue of an RRGGBB colour in degrees."""
    r, g, b = (int(hex_colour[i:i + 2], 16) / 255 for i in (0, 2, 4))
    return colorsys.rgb_to_hsv(r, g, b)[0] * 360


class AgentColourTest(RoundCase):
    """loop.agent_identity with several agents started one after the other."""

    def start(self, n, prefix):
        """Start n running agents like the loop does (identity, then the row) and return their colours."""
        res = []
        for i in range(n):
            ident = loop.agent_identity(self.conn)
            self.agent("%s%d" % (prefix, i), package="P%d" % i, status="running", pid=1, color=ident["Color"], person=ident["Person"])
            res.append(ident["Color"])
        return res

    def test_palette_hues_differ(self):
        """No two palette colours are within 25 degrees of hue (no two purples, blues or pinks)."""
        hues = [hue(c) for c in loop.AGENT_COLOURS]
        for i, a in enumerate(hues):
            for b in hues[i + 1:]:
                d = abs(a - b)
                self.assertGreaterEqual(min(d, 360 - d), 25)

    def distinct(self, n):
        """n agents started one after the other get n different colours."""
        cols = self.start(n, "R")
        self.assertEqual(len(set(cols)), n, cols)

    def test_two_running_agents(self):
        """Two running agents: different colours."""
        self.distinct(2)

    def test_four_running_agents(self):
        """Four running agents: different colours."""
        self.distinct(4)

    def test_as_many_agents_as_colours(self):
        """As many running agents as there are colours: no colour twice."""
        self.distinct(len(loop.AGENT_COLOURS))

    def test_recently_ended_agents_do_not_cause_a_repeat(self):
        """Agents that ended within the hour hold all but one colour: two new running agents still differ."""
        for i, c in enumerate(loop.AGENT_COLOURS[:-1]):
            self.agent("E%d" % i, package="E%d" % i, status="done", ended=5, color=c, person="Old%d" % i)
        cols = self.start(2, "N")
        self.assertNotEqual(cols[0], cols[1])


if __name__ == "__main__":
    unittest.main()

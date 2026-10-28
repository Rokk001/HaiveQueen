"""A small terminal model for the redraw tests: screen, scrollback, scroll region, autowrap and the escape sequences the panes use.

It is deliberately pessimistic where Windows Terminal is: erasing the whole screen (2J) moves the lines on it into the scrollback;
only 3J empties the scrollback. Unknown sequences are ignored.
"""
import re

CSI = re.compile(r"\x1b\[([?]?)([0-9;]*)([A-Za-z])")


class Screen:
    """cols x rows screen with scrollback."""

    def __init__(self, cols, rows):
        """Empty screen, cursor at the top left, autowrap on, scroll region = whole screen."""
        self.cols, self.rows = cols, rows
        self.grid = [[" "] * cols for _ in range(rows)]
        self.scrollback = []
        self.r = self.c = 0
        self.saved = (0, 0)
        self.top, self.bottom = 0, rows - 1
        self.autowrap = True

    def text(self, row):
        """Text of one screen row without trailing blanks."""
        return "".join(self.grid[row]).rstrip()

    def lines(self):
        """All screen rows as text."""
        return [self.text(i) for i in range(self.rows)]

    def _linefeed(self):
        """Move down one row; at the bottom of the scroll region the region scrolls (its top row goes to the scrollback when the region is the whole screen)."""
        if self.r == self.bottom:
            if self.top == 0 and self.bottom == self.rows - 1:
                self.scrollback.append("".join(self.grid[0]).rstrip())
            del self.grid[self.top]
            self.grid.insert(self.bottom, [" "] * self.cols)
        elif self.r < self.rows - 1:
            self.r += 1

    def _put(self, ch):
        """Write one character at the cursor, wrapping or overwriting the last column."""
        if self.c >= self.cols:
            if self.autowrap:
                self.c = 0
                self._linefeed()
            else:
                self.c = self.cols - 1
        self.grid[self.r][self.c] = ch
        self.c += 1

    def _erase_display(self, mode):
        """ED: 0 from the cursor to the end, 2 the whole screen (kept in the scrollback), 3 the scrollback."""
        if mode == 3:
            self.scrollback = []
        elif mode == 2:
            for i in range(self.rows):
                if self.text(i):
                    self.scrollback.append(self.text(i))
                self.grid[i] = [" "] * self.cols
        elif mode == 0:
            self.grid[self.r][self.c:] = [" "] * (self.cols - self.c)
            for i in range(self.r + 1, self.rows):
                self.grid[i] = [" "] * self.cols

    def feed(self, data):
        """Process a string of text and escape sequences."""
        i = 0
        while i < len(data):
            ch = data[i]
            if ch == "\x1b":
                m = CSI.match(data, i)
                if m:
                    self._csi(m.group(1), m.group(2), m.group(3))
                    i = m.end()
                    continue
                if data[i + 1:i + 2] == "7":
                    self.saved = (self.r, self.c)
                elif data[i + 1:i + 2] == "8":
                    self.r, self.c = self.saved
                i += 2
                continue
            if ch == "\r":
                self.c = 0
            elif ch == "\n":
                self.c = 0   # the console adds the carriage return to a line feed
                self._linefeed()
            else:
                self._put(ch)
            i += 1

    def _csi(self, private, params, final):
        """Handle one CSI sequence."""
        nums = [int(p) if p else 0 for p in params.split(";")] if params else []
        if final == "H":
            self.r = min(self.rows - 1, max(0, (nums[0] if nums and nums[0] else 1) - 1))
            self.c = min(self.cols - 1, max(0, (nums[1] if len(nums) > 1 and nums[1] else 1) - 1))
        elif final == "J":
            self._erase_display(nums[0] if nums else 0)
        elif final == "K":
            mode = nums[0] if nums else 0
            if mode == 2:
                self.grid[self.r] = [" "] * self.cols
            elif mode == 0:
                self.grid[self.r][self.c:] = [" "] * (self.cols - self.c)
        elif final == "r":
            self.top, self.bottom = (nums[0] - 1, nums[1] - 1) if len(nums) == 2 else (0, self.rows - 1)
            self.r = self.c = 0
        elif final in "hl" and private and nums == [7]:
            self.autowrap = final == "h"

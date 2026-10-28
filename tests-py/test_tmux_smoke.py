"""Smoke test of the tmux backend against a real tmux (skipped on Windows and without tmux)."""
import os
import shutil
import subprocess
import sys
import tempfile
import time
import unittest
import uuid
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
from orchlib.terminal import get_terminal  # noqa: E402

HAVE_TMUX = sys.platform.startswith("linux") and shutil.which("tmux") is not None


@unittest.skipUnless(HAVE_TMUX, "needs Linux and tmux")
class TmuxSmokeTest(unittest.TestCase):
    """Open a session, split a titled pane, list, close, end the session; all with a private tmux socket."""

    def setUp(self):
        """Give tmux its own socket folder and a unique session name."""
        self.tmp = tempfile.mkdtemp(prefix="orchtmux")
        saved = os.environ.get("TMUX_TMPDIR")
        os.environ["TMUX_TMPDIR"] = self.tmp
        self.addCleanup(self._restore_env, saved)
        self.session = "orchsmoke" + uuid.uuid4().hex[:8]
        self.term = get_terminal("tmux")
        self.addCleanup(self._cleanup)

    @staticmethod
    def _restore_env(saved):
        """Put TMUX_TMPDIR back as it was."""
        if saved is None:
            os.environ.pop("TMUX_TMPDIR", None)
        else:
            os.environ["TMUX_TMPDIR"] = saved

    def _tmux(self, *args):
        """Run a raw tmux command with the private socket; return the completed process."""
        return subprocess.run(["tmux"] + list(args), capture_output=True, text=True)

    def _cleanup(self):
        """End the session and the tmux server and remove the socket folder."""
        self._tmux("kill-session", "-t", self.session)
        self._tmux("kill-server")
        shutil.rmtree(self.tmp, ignore_errors=True)

    def test_session_lifecycle(self):
        """open_window, split_pane, list_panes, set_title and close_pane work; kill-session ends the session."""
        first = self.term.open_window(self.session, "First", ["sleep", "30"])
        second = self.term.split_pane(self.session, "Second", None, ["sleep", "30"],
                                      direction="vertical", size=0.5, target=first)
        self.assertNotEqual(first, second)

        panes = {p.id: p for p in self.term.list_panes(self.session)}
        self.assertEqual(set(panes), {first, second})
        self.assertEqual(panes[first].title, "First")
        self.assertEqual(panes[second].title, "Second")
        self.assertTrue(panes[second].alive)
        self.assertGreater(panes[second].pid, 0)

        self.term.set_title(second, "Renamed")
        titles = {p.id: p.title for p in self.term.list_panes(self.session)}
        self.assertEqual(titles[second], "Renamed")

        self.term.close_pane(second)
        deadline = time.time() + 5
        while time.time() < deadline and len(self.term.list_panes(self.session)) != 1:
            time.sleep(0.1)
        self.assertEqual([p.id for p in self.term.list_panes(self.session)], [first])

        self.assertEqual(self._tmux("kill-session", "-t", self.session).returncode, 0)
        self.assertNotEqual(self._tmux("has-session", "-t", self.session).returncode, 0)


if __name__ == "__main__":
    unittest.main()

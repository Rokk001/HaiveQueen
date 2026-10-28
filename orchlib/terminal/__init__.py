"""Terminal backends (Windows Terminal, tmux, headless) behind one interface."""
from .base import PaneInfo, Terminal, TerminalError, get_terminal, pane_title

__all__ = ["PaneInfo", "Terminal", "TerminalError", "get_terminal", "pane_title"]

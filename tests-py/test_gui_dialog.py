"""Tests of the dialogs, pop-up menus and widget colours in the look of the dashboard (haivequeen/gui_dialog.py)."""
import unittest
from unittest import mock


# Find a button of a window by its text.
def find_button(widget, text):
    """The tk.Button with the label `text` below `widget`, or None."""
    import tkinter as tk
    if isinstance(widget, tk.Button) and widget.cget("text") == text:
        return widget
    for child in widget.winfo_children():
        found = find_button(child, text)
        if found:
            return found
    return None


class DialogTest(unittest.TestCase):
    """Message windows, menus and colours need a display; without one the tests are skipped."""

    # Create a transparent root window, or skip without a display.
    def setUp(self):
        """Open a fully transparent root window: the test windows must not flash on the user's screen."""
        import tkinter as tk
        try:
            self.root = tk.Tk()
            self.root.attributes("-alpha", 0.0)
        except tk.TclError:
            self.skipTest("no display")
        self.addCleanup(self.root.destroy)

    # Press a button of the window that is open on the root.
    def press_later(self, text):
        """Schedule a click on the button `text` of the open message window (made transparent first); `show` blocks until then."""
        import tkinter as tk

        def press():
            window = next(w for w in self.root.winfo_children() if isinstance(w, tk.Toplevel))
            window.attributes("-alpha", 0.0)
            find_button(window, text).invoke()
        self.root.after(80, press)

    # A button of the dialog gives its label back and closes the window.
    def test_dialog_result_and_close(self):
        """Pressing Yes stores "Yes" and destroys the window; the other button gives the other label."""
        from haivequeen import gui_dialog
        dialog = gui_dialog.Dialog(self.root, "Title", "Text", "Consolas", ("Yes", "No"), "No")
        dialog.win.attributes("-alpha", 0.0)
        self.assertEqual(dialog.result, "No")   # closing without a choice answers the cancel label
        dialog.buttons["Yes"].invoke()
        self.assertEqual(dialog.result, "Yes")
        self.assertFalse(dialog.win.winfo_exists())

    # ask answers True for Yes and False for No.
    def test_ask(self):
        """The yes/no question returns True after Yes and False after No."""
        from haivequeen import gui_dialog
        self.press_later("Yes")
        self.assertTrue(gui_dialog.ask(self.root, "Title", "Really?", "Consolas"))
        self.press_later("No")
        self.assertFalse(gui_dialog.ask(self.root, "Title", "Really?", "Consolas"))

    # info and error show an OK button and return.
    def test_info_and_error(self):
        """Both message windows close with OK."""
        from haivequeen import gui_dialog
        self.press_later("OK")
        gui_dialog.info(self.root, "Title", "Text", "Consolas")
        self.press_later("OK")
        gui_dialog.error(self.root, "Title", "Text", "Consolas")

    # The pop-up menu runs the chosen action once and closes.
    def test_popup_menu_choose_runs_the_action_and_closes(self):
        """A menu with an action, a disabled line and a separator lists two entries; choosing the action runs it and removes the menu, closing twice is harmless."""
        from haivequeen import gui_dialog
        action = mock.Mock()
        menu = gui_dialog.PopupMenu(self.root, [("Restart", action), None, ("Disabled", None)], "Consolas", 0, 0)
        menu.win.attributes("-alpha", 0.0)
        self.assertEqual([(row.cget("text"), run is None) for row, run in menu.entries], [("Restart", False), ("Disabled", True)])
        win = menu.win
        menu.choose(action)
        action.assert_called_once_with()
        self.assertFalse(win.winfo_exists())
        menu.close()

    # A click elsewhere in the dashboard closes the menu.
    def test_popup_menu_closes_on_click_elsewhere(self):
        """A click on the dashboard window removes the menu without running an action."""
        from haivequeen import gui_dialog
        action = mock.Mock()
        menu = gui_dialog.PopupMenu(self.root, [("Restart", action)], "Consolas", 0, 0)
        menu.win.attributes("-alpha", 0.0)
        win = menu.win
        self.root.event_generate("<Button-1>", x=1, y=1)
        self.root.update()
        self.assertFalse(win.winfo_exists())
        action.assert_not_called()

    # Widgets created after apply_style carry the dashboard colours.
    def test_apply_style_colours_new_widgets(self):
        """A button and an entry created after apply_style use the gold-on-dark and dark-field colours of the dashboard."""
        import tkinter as tk
        from haivequeen import gui, gui_dialog
        gui_dialog.apply_style(self.root, "Consolas")
        button, entry = tk.Button(self.root), tk.Entry(self.root)
        self.assertEqual((button.cget("bg"), button.cget("fg")), (gui.COL["select"], gui.COL["gold"]))
        self.assertEqual((entry.cget("bg"), entry.cget("fg")), (gui.COL["track"], gui.COL["text"]))


if __name__ == "__main__":
    unittest.main()

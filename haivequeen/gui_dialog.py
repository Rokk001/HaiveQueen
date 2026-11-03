"""Dialogs, pop-up menus and widget colours in the look of the dashboard (dark panels, gold frame) instead of the grey system look.

`apply_style(root, mono)` puts the colours into the option database, so every tk widget created afterwards (also in the questions and settings windows)
is dark; `show`, `info`, `error` and `ask` replace tkinter.messagebox, `popup_menu` replaces the tk.Menu pop-ups. Only the file dialog stays the system one.
"""
from .gui import COL


# Give every tk widget that is created from now on the colours of the dashboard.
def apply_style(root, mono):
    """Write the dashboard colours and the monospace font `mono` into the option database of `root`; widgets that set a colour themselves keep it."""
    bg, panel, text, gold, dim = COL["bg"], COL["panel"], COL["text"], COL["gold"], COL["dim"]
    track, select, selectedge, edge = COL["track"], COL["select"], COL["selectedge"], COL["edge"]
    field = {"background": track, "foreground": text, "insertBackground": text, "selectBackground": selectedge, "selectForeground": text,
             "relief": "flat", "highlightThickness": 1, "highlightBackground": selectedge, "highlightColor": gold}
    rules = {
        "Toplevel": {"background": bg}, "Frame": {"background": bg}, "Canvas": {"background": bg},
        "Label": {"background": bg, "foreground": text}, "Labelframe": {"background": bg, "foreground": gold},
        "Button": {"background": select, "foreground": gold, "activeBackground": selectedge, "activeForeground": gold, "disabledForeground": dim,
                   "relief": "flat", "borderWidth": 0, "padX": 12, "padY": 3, "highlightThickness": 0},
        "Checkbutton": {"background": bg, "foreground": text, "activeBackground": bg, "activeForeground": gold, "selectColor": track, "highlightThickness": 0},
        "Menubutton": {"background": track, "foreground": text, "activeBackground": select, "activeForeground": gold, "relief": "flat", "highlightThickness": 0},
        "Menu": {"background": panel, "foreground": text, "activeBackground": select, "activeForeground": gold, "relief": "flat"},
        "Entry": field, "Text": field,
        "Listbox": {"background": panel, "foreground": text, "selectBackground": select, "selectForeground": gold, "relief": "flat",
                    "highlightThickness": 1, "highlightBackground": selectedge, "highlightColor": gold, "activeStyle": "none"},
        "Scrollbar": {"background": edge, "troughColor": track, "activeBackground": gold, "relief": "flat", "borderWidth": 0},
    }
    for widget, options in rules.items():
        for name, value in options.items():
            root.option_add("*%s.%s" % (widget, name), value, 60)
    root.option_add("*Font", "{%s} 10" % mono, 60)
    from tkinter import ttk
    style = ttk.Style(root)
    style.theme_use("clam")   # the only built-in ttk theme that takes colours for scroll bars
    for orient in ("Vertical", "Horizontal"):
        name = "%s.TScrollbar" % orient
        style.configure(name, background=edge, troughcolor=track, bordercolor=bg, lightcolor=edge, darkcolor=edge, arrowcolor=gold, relief="flat", gripcount=0)
        style.map(name, background=[("active", gold), ("pressed", gold)])


# A scroll bar in the dashboard colours.
def scrollbar(parent, command, orient="vertical"):
    """A ttk scroll bar (coloured by apply_style) for `command`, e.g. `text.yview`; use it like tk.Scrollbar."""
    from tkinter import ttk
    return ttk.Scrollbar(parent, orient=orient, command=command)


# A file name prompt in the dashboard look (instead of the system save dialog).
def ask_path(parent, title, initial, mono):
    """Ask for a file path in a modal dashboard window; returns the typed path, or None for Cancel, Escape or closing."""
    import tkinter as tk
    panel = COL["panel"]
    result = {"path": None}
    win = tk.Toplevel(parent)
    win.title("HaiveQueen")
    win.configure(bg=COL["edge"])
    win.resizable(False, False)
    win.transient(parent)
    inner = tk.Frame(win, bg=panel)
    inner.pack(padx=2, pady=2)
    tk.Label(inner, text=title, bg=panel, fg=COL["gold"], font=(mono, 14, "bold"), anchor="w").pack(fill="x", padx=18, pady=(14, 4))
    var = tk.StringVar(value=initial)
    entry = tk.Entry(inner, textvariable=var, width=64, font=(mono, 11))
    entry.pack(padx=18, pady=(4, 14))
    row = tk.Frame(inner, bg=panel)
    row.pack(fill="x", padx=18, pady=(0, 14))

    def close(save):
        """Keep the typed path (Save) or nothing (Cancel) and close the window."""
        result["path"] = var.get().strip() or None if save else None
        win.destroy()
    tk.Button(row, text="Cancel", font=(mono, 11), command=lambda: close(False)).pack(side="right", padx=(8, 0))
    tk.Button(row, text="Save", font=(mono, 11), command=lambda: close(True)).pack(side="right")
    win.bind("<Return>", lambda _e: close(True))
    win.bind("<Escape>", lambda _e: close(False))
    win.protocol("WM_DELETE_WINDOW", lambda: close(False))
    win.update_idletasks()
    x = parent.winfo_rootx() + max(0, (parent.winfo_width() - win.winfo_reqwidth()) // 2)
    y = parent.winfo_rooty() + max(0, (parent.winfo_height() - win.winfo_reqheight()) // 2)
    win.geometry("+%d+%d" % (x, y))
    win.grab_set()
    entry.focus_set()
    entry.select_range(0, "end")
    parent.wait_window(win)
    return result["path"]


# A drop-down list in the dashboard look.
def option_menu(parent, var, values, mono, command=None):
    """A menu button that shows the value of the StringVar `var` with an arrow and lets the user pick one of `values`; `command(value)` runs after a pick."""
    import tkinter as tk
    button = tk.Menubutton(parent, font=(mono, 10), anchor="w", relief="flat", indicatoron=False, padx=8, pady=3,
                           highlightthickness=1, highlightbackground=COL["selectedge"], highlightcolor=COL["gold"])
    menu = tk.Menu(button, tearoff=0)
    button["menu"] = menu

    def pick(value):
        """Set the variable and tell the caller."""
        var.set(value)
        if command:
            command(value)
    for value in values:
        menu.add_command(label=value, command=lambda v=value: pick(v))
    show = lambda *_: button.configure(text="%s  ▾" % var.get())
    var.trace_add("write", show)
    show()
    return button


# A modal message window in the dashboard look.
class Dialog:
    """A modal window with a gold frame, a title, a text and buttons; `result` holds the label of the pressed button (`cancel` for Escape or closing)."""

    # Build the window with its buttons and keys.
    def __init__(self, parent, title, text, mono, buttons, cancel, kind="info"):
        """Create the window on `parent`; Enter presses the first button, Escape and the close button press `cancel`; `kind` "error" draws the title red."""
        import tkinter as tk
        panel = COL["panel"]
        self.result, self.buttons = cancel, {}
        self.win = win = tk.Toplevel(parent)
        win.title("HaiveQueen")
        win.configure(bg=COL["edge"])
        win.resizable(False, False)
        win.transient(parent)
        inner = tk.Frame(win, bg=panel)
        inner.pack(padx=2, pady=2)
        tk.Label(inner, text=title, bg=panel, fg=COL["red"] if kind == "error" else COL["gold"], font=(mono, 14, "bold"), anchor="w").pack(fill="x", padx=18, pady=(14, 4))
        tk.Label(inner, text=text, bg=panel, fg=COL["text"], font=(mono, 11), justify="left", anchor="w", wraplength=560).pack(fill="x", padx=18, pady=(4, 14))
        row = tk.Frame(inner, bg=panel)
        row.pack(fill="x", padx=18, pady=(0, 14))
        for label in reversed(buttons):
            self.buttons[label] = tk.Button(row, text=label, font=(mono, 11), command=lambda picked=label: self.close(picked))
            self.buttons[label].pack(side="right", padx=(8, 0))
        win.bind("<Return>", lambda _e: self.close(buttons[0]))
        win.bind("<Escape>", lambda _e: self.close(cancel))
        win.protocol("WM_DELETE_WINDOW", lambda: self.close(cancel))
        self.centre(parent)

    # Place the window in the middle of its parent.
    def centre(self, parent):
        """Put the window over the middle of `parent` (or the screen middle when the parent has no size yet)."""
        self.win.update_idletasks()
        width, height = self.win.winfo_reqwidth(), self.win.winfo_reqheight()
        if parent.winfo_width() > 1:
            x = parent.winfo_rootx() + (parent.winfo_width() - width) // 2
            y = parent.winfo_rooty() + (parent.winfo_height() - height) // 2
        else:
            x, y = (self.win.winfo_screenwidth() - width) // 2, (self.win.winfo_screenheight() - height) // 2
        self.win.geometry("+%d+%d" % (max(0, x), max(0, y)))

    # Close the window and remember the answer.
    def close(self, picked):
        """Store the label of the pressed button and destroy the window."""
        self.result = picked
        self.win.destroy()


# Show a message window and wait for the answer.
def show(parent, title, text, mono, buttons=("OK",), cancel=None, kind="info") -> str:
    """Open a Dialog on `parent`, wait until it is closed and return the label of the pressed button (`cancel`, default the last button, for Escape)."""
    dialog = Dialog(parent, title, text, mono, buttons, cancel or buttons[-1], kind)
    try:
        dialog.win.grab_set()   # modal: the dashboard stays idle until the answer is given
    except Exception:   # a window that is not visible yet cannot grab
        pass
    dialog.win.focus_force()
    parent.wait_window(dialog.win)
    return dialog.result


# Show an information window.
def info(parent, title, text, mono):
    """Show `text` under `title` with an OK button."""
    show(parent, title, text, mono)


# Show an error window.
def error(parent, title, text, mono):
    """Show `text` under `title` in the error colour with an OK button."""
    show(parent, title, text, mono, kind="error")


# Ask a yes/no question.
def ask(parent, title, text, mono) -> bool:
    """True when the user pressed Yes; Escape and closing the window answer No."""
    return show(parent, title, text, mono, ("Yes", "No")) == "Yes"


# A small menu in the dashboard look.
class PopupMenu:
    """A menu window that opens at a screen position and closes on a choice, Escape or a click elsewhere in the dashboard."""

    # Build the menu entries and show the menu.
    def __init__(self, parent, items, mono, x, y):
        """Open the menu on `parent` at the screen position (x, y); `items` are (label, action), (label, None) for a disabled line, or None for a separator."""
        import tkinter as tk
        panel, text = COL["panel"], COL["text"]
        self.root, self.entries = parent.winfo_toplevel(), []
        self.win = win = tk.Toplevel(parent)
        win.overrideredirect(True)
        win.configure(bg=COL["edge"])
        inner = tk.Frame(win, bg=panel)
        inner.pack(padx=1, pady=1)
        for item in items:
            if item is None:
                tk.Frame(inner, height=1, bg=COL["selectedge"]).pack(fill="x", pady=3)
                continue
            label, action = item
            row = tk.Label(inner, text=label, anchor="w", font=(mono, 11), bg=panel, fg=text if action else COL["dim"], padx=16, pady=4)
            row.pack(fill="x")
            if action:
                row.bind("<Enter>", lambda _e, w=row: w.config(bg=COL["select"], fg=COL["gold"]))
                row.bind("<Leave>", lambda _e, w=row: w.config(bg=panel, fg=text))
                row.bind("<Button-1>", lambda _e, a=action: self.choose(a))
            self.entries.append((row, action))
        win.update_idletasks()
        x = max(0, min(x, win.winfo_screenwidth() - win.winfo_reqwidth()))
        y = max(0, min(y, win.winfo_screenheight() - win.winfo_reqheight()))
        win.geometry("+%d+%d" % (x, y))
        win.bind("<Escape>", lambda _e: self.close())
        self.clicks = self.root.bind("<Button-1>", lambda _e: self.close(), "+")
        self.keys = self.root.bind("<Escape>", lambda _e: self.close(), "+")
        win.focus_force()

    # Close the menu and run the chosen action.
    def choose(self, action):
        """Close the menu, then run `action`."""
        self.close()
        action()

    # Close the menu.
    def close(self):
        """Remove the menu window and the bindings it put on the dashboard window; closing twice does nothing."""
        if self.win is None:
            return
        win, self.win = self.win, None
        for sequence, funcid in (("<Button-1>", self.clicks), ("<Escape>", self.keys)):
            try:
                self.root.unbind(sequence, funcid)
            except Exception:   # the dashboard window may be gone already
                pass
        win.destroy()


# Open a menu at the pointer.
def popup_menu(parent, items, mono) -> PopupMenu:
    """Open a PopupMenu with `items` at the mouse pointer and return it."""
    return PopupMenu(parent, items, mono, parent.winfo_pointerx(), parent.winfo_pointery())

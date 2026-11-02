"""Editor for the TOML settings in the graphical window: config.toml of the machine and the project.toml of every Apiary.

This file holds the data layer (read, split the allowed tools, write TOML, check, save with a backup); the window is `ConfigWindow` below.
Only the standard library is used: Python can read TOML but not write it, so `dumps` writes the few TOML forms the settings use.
"""
import json
import math
import os
import re
import shutil
import tomllib
from pathlib import Path

ALLOWED_FLAG = "--allowedTools"   # the Claude Code argument whose value is the comma separated list of allowed tools
CHOICES = {"protocol": ("claude", "codex"), "use": ("preferred", "fallback", "capability-only"), "beeMode": ("subagent", "process")}   # fields that offer a fixed choice
CLASSES = ("light", "medium", "heavy")


# Read a TOML file into a dict.
def load_raw(path) -> dict:
    """The parsed content of the TOML file `path`; a missing file gives an empty dict."""
    path = Path(path)
    if not path.is_file():
        return {}
    with open(path, "rb") as handle:
        return tomllib.load(handle)


# Cut a comma separated tool list into its entries.
def split_tools(text) -> list:
    """Split `Bash(git merge *),Read` at the commas outside parentheses; empty entries are dropped and entries are trimmed."""
    out, depth, current = [], 0, ""
    for ch in str(text or ""):
        if ch == "(":
            depth += 1
        elif ch == ")":
            depth = max(0, depth - 1)
        if ch == "," and depth == 0:
            out.append(current)
            current = ""
        else:
            current += ch
    out.append(current)
    return [item.strip() for item in out if item.strip()]


# Separate the allowed tools from the other arguments of a provider.
def split_args(args) -> tuple:
    """(other arguments, allowed tools, position): the `--allowedTools <list>` pair is taken out of `args`; `position` is where it stood (None when absent)."""
    args = [str(a) for a in (args or [])]
    if ALLOWED_FLAG in args:
        i = args.index(ALLOWED_FLAG)
        if i + 1 < len(args):
            return args[:i] + args[i + 2:], split_tools(args[i + 1]), i
    return args, [], None


# Put the allowed tools back into the argument list.
def join_args(rest, tools, position=None) -> list:
    """The argument list with `--allowedTools <comma separated tools>` inserted at `position` (at the end when unknown); no tools means no flag."""
    out = list(rest)
    if tools:
        i = len(out) if position is None else min(position, len(out))
        out[i:i] = [ALLOWED_FLAG, ",".join(tools)]
    return out


# TOML text of a key.
def _key(key) -> str:
    """The key as bare TOML key when it only has letters, digits, `_` and `-`, else as quoted string."""
    return key if re.fullmatch(r"[A-Za-z0-9_-]+", key) else json.dumps(key, ensure_ascii=False)


# TOML text of a value that stays on one line.
def _value(value) -> str:
    """One-line TOML text of a bool, int, float, string, list or dict (a dict becomes an inline table)."""
    if isinstance(value, bool):
        return "true" if value else "false"
    if isinstance(value, int):
        return str(value)
    if isinstance(value, float):
        if not math.isfinite(value):
            raise ValueError("a number must be finite")
        return repr(value)
    if isinstance(value, str):
        return json.dumps(value, ensure_ascii=False)
    if isinstance(value, list):
        return "[" + ", ".join(_value(v) for v in value) + "]"
    if isinstance(value, dict):
        return "{ " + ", ".join("%s = %s" % (_key(k), _value(v)) for k, v in value.items()) + " }" if value else "{}"
    raise ValueError("cannot write %r as TOML" % (value,))


# A dict that must become its own [table] because it holds other dicts.
def _is_table(value) -> bool:
    """True for a dict with at least one dict value."""
    return isinstance(value, dict) and any(isinstance(v, dict) for v in value.values())


# A list of dicts that must become [[array of tables]].
def _is_array(value) -> bool:
    """True for a non-empty list whose entries are all dicts."""
    return isinstance(value, list) and bool(value) and all(isinstance(v, dict) for v in value)


# Write one table and everything below it.
def _table(lines, path, data):
    """Append the key lines of `data`, then its sub-tables and arrays of tables, with the header names built from `path`."""
    for key, value in data.items():
        if not _is_table(value) and not _is_array(value):
            lines.append("%s = %s" % (_key(key), _value(value)))
    for key, value in data.items():
        if _is_table(value):
            lines += ["", "[%s]" % ".".join(_key(p) for p in path + [key])]
            _table(lines, path + [key], value)
    for key, value in data.items():
        if _is_array(value):
            for item in value:
                lines += ["", "[[%s]]" % ".".join(_key(p) for p in path + [key])]
                _table(lines, path + [key], item)


# TOML text of a whole settings dict.
def dumps(data) -> str:
    """TOML text of `data`: scalars first, then tables, then arrays of tables; reading it back with tomllib gives `data` again."""
    lines = []
    _table(lines, [], data)
    return "\n".join(lines).lstrip("\n") + "\n"


# Look for problems before a file is written.
def check(data, machine=True) -> list:
    """List of problem texts: the text must read back unchanged; a machine config must also pass the checks the loop makes when it reloads settings."""
    problems = []
    try:
        if tomllib.loads(dumps(data)) != data:
            problems.append("the settings cannot be written as TOML without changing them")
    except (ValueError, tomllib.TOMLDecodeError) as exc:
        problems.append(str(exc))
    if machine and not problems:
        from . import scheduler
        try:
            scheduler.get_providers(data)
            int(data.get("hives", 2))
            minutes = float(data.get("roundMinutes", 5))
            if not math.isfinite(minutes) or minutes <= 0:
                problems.append("roundMinutes must be greater than zero")
            names = [p.get("name") for p in data.get("providers") or []]
            if len(set(names)) != len(names):
                problems.append("provider names must be unique")
        except (ValueError, TypeError, AttributeError) as exc:
            problems.append(str(exc))
    return problems


# Write a settings file and keep the old one.
def save_file(path, data):
    """Write `data` to `path` as TOML: the comment lines at the top of the old file stay, the old file is kept as `<name>.bak`, the new one replaces it in one step."""
    path = Path(path)
    header = ""
    if path.is_file():
        kept = []
        for line in path.read_text(encoding="utf-8").splitlines():
            if line.startswith("#") or not line.strip():
                kept.append(line)
            else:
                break
        header = "\n".join(kept).rstrip("\n") + "\n\n" if any(x.strip() for x in kept) else ""
        shutil.copy2(path, path.with_name(path.name + ".bak"))
    path.parent.mkdir(parents=True, exist_ok=True)
    temp = path.with_name(path.name + ".tmp")
    temp.write_text(header + dumps(data), encoding="utf-8", newline="\n")
    os.replace(temp, path)


# Settings files the editor offers.
def settings_files(conn, config_dir) -> list:
    """[(label, path, is_machine)]: config.toml first, then the project.toml of every registered Apiary that has one."""
    out = [("config.toml (machine)", Path(config_dir) / "config.toml", True)]
    for name, workspace in conn.execute("SELECT name, workspace FROM projects ORDER BY name"):
        if workspace:
            path = Path(workspace) / ".haivequeen" / "project.toml"
            if path.is_file():
                out.append(("project.toml (%s)" % name, path, False))
    return out


# Convert text typed into the editor to the right type.
def parse_typed(text, like=None):
    """Convert `text` to the type of `like` (bool, int, float, str); without a model value guess: true/false, whole number, decimal, else text."""
    text = str(text).strip()
    if like is not None and not isinstance(like, str):
        if isinstance(like, bool):
            if text.lower() in ("true", "false"):
                return text.lower() == "true"
            raise ValueError("true or false expected")
        return type(like)(text)
    if like is None:
        if text.lower() in ("true", "false"):
            return text.lower() == "true"
        for cast in (int, float):
            try:
                return cast(text)
            except ValueError:
                pass
    return text


# The window of the editor.
class ConfigWindow:
    """A Toplevel with one form per part of the settings: General, every provider (with its allowed tools) and every project."""

    # Build the window for the settings files of `conn`.
    def __init__(self, root, conn, config_dir, mono="Consolas", on_saved=None):
        """Open the editor on `root`; `config_dir` holds config.toml, `mono` is the font family, `on_saved` is called after a successful save."""
        import tkinter as tk
        self.tk, self.root, self.conn, self.mono, self.on_saved = tk, root, conn, mono, on_saved
        self.files = settings_files(conn, config_dir)
        self.index, self.data, self.node, self.getters, self.entries, self.position = 0, {}, ("general", None), [], [], None
        self.win = tk.Toplevel(root)
        self.win.title("HaiveQueen settings")
        self.win.geometry("1100x700")
        top = tk.Frame(self.win)
        top.pack(fill="x", padx=8, pady=6)
        self.file_var = tk.StringVar(value=self.files[0][0])
        tk.Label(top, text="File:").pack(side="left")
        tk.OptionMenu(top, self.file_var, *[f[0] for f in self.files], command=self.pick_file).pack(side="left", padx=6)
        from .gui import COL
        self.col = COL   # the dashboard colours, also for the status texts
        self.status = tk.Label(top, anchor="w", fg=COL["red"])
        self.status.pack(side="left", padx=12, fill="x", expand=True)
        body = tk.Frame(self.win)
        body.pack(fill="both", expand=True, padx=8)
        side = tk.Frame(body)
        side.pack(side="left", fill="y")
        self.nodes = tk.Listbox(side, width=28, exportselection=False, font=(mono, 10))
        self.nodes.pack(fill="y", expand=True)
        self.nodes.bind("<<ListboxSelect>>", self.pick_node)
        for text, command in (("Add provider", self.add_provider), ("Add project", self.add_project), ("Remove entry", self.remove_node)):
            tk.Button(side, text=text, command=command).pack(fill="x", pady=1)
        canvas = tk.Canvas(body, highlightthickness=0)
        scroll = tk.Scrollbar(body, orient="vertical", command=canvas.yview)
        canvas.configure(yscrollcommand=scroll.set)
        scroll.pack(side="right", fill="y")
        canvas.pack(side="left", fill="both", expand=True, padx=(8, 0))
        self.form = tk.Frame(canvas)
        window = canvas.create_window((0, 0), window=self.form, anchor="nw")
        self.form.bind("<Configure>", lambda _e: canvas.configure(scrollregion=canvas.bbox("all")))
        canvas.bind("<Configure>", lambda e: canvas.itemconfigure(window, width=e.width))
        canvas.bind("<Enter>", lambda _e: canvas.bind_all("<MouseWheel>", lambda e: canvas.yview_scroll(int(-e.delta / 120), "units")))
        canvas.bind("<Leave>", lambda _e: canvas.unbind_all("<MouseWheel>"))
        bottom = tk.Frame(self.win)
        bottom.pack(fill="x", padx=8, pady=6)
        for text, command in (("Save", self.save), ("Revert", self.reload), ("Open folder", self.open_folder), ("Close", self.win.destroy)):
            tk.Button(bottom, text=text, width=12, command=command).pack(side="left", padx=4)
        tk.Label(bottom, text="Changes apply to the running HaiveQueen without a restart. The old file is kept as <name>.bak.", fg=COL["dim"]).pack(side="left", padx=12)
        self.reload()

    # Is the file shown a machine config (providers, projects)?
    def machine(self) -> bool:
        """True while config.toml is the selected file."""
        return self.files[self.index][2]

    # Read the selected file again and show its parts.
    def reload(self):
        """Drop unsaved changes: read the selected file and list General, the providers and the projects."""
        self.data = load_raw(self.files[self.index][1])
        self.status.config(text="")
        self.fill_nodes()
        self.select(0)

    # List the parts of the settings.
    def fill_nodes(self):
        """Rebuild the left list: General, then every provider and project."""
        self.nodes.delete(0, "end")
        self.entries = [("general", None)]
        self.nodes.insert("end", "General")
        for kind in ("providers", "projects"):
            for i, item in enumerate(self.data.get(kind) or []):
                self.entries.append((kind, i))
                self.nodes.insert("end", "%s: %s" % (kind[:-1], item.get("name", "?")))

    # Change the selected file.
    def pick_file(self, label):
        """Switch to the file with this label; typed but unsaved changes of the old file are dropped."""
        self.index = [f[0] for f in self.files].index(label)
        self.reload()

    # Show one part.
    def select(self, row):
        """Select list row `row` and build its form."""
        self.nodes.selection_clear(0, "end")
        self.nodes.selection_set(row)
        self.node = self.entries[row]
        self.build_form()

    # Row clicked in the list.
    def pick_node(self, _event):
        """Keep the typed values of the old part, then show the clicked part; a wrong value stays on screen with its message."""
        picked = self.nodes.curselection()
        if not picked:
            return
        if not self.collect():
            self.nodes.selection_clear(0, "end")
            self.nodes.selection_set(self.entries.index(self.node))
            return
        self.node = self.entries[picked[0]]
        self.build_form()

    # The dict a part edits.
    def target(self) -> dict:
        """The dict of the shown part: the top level for General, else the provider or project entry."""
        kind, i = self.node
        return self.data if kind == "general" else self.data[kind][i]

    # Build the form of the shown part.
    def build_form(self):
        """Fill the right side with one row per setting of the shown part; a provider gets its allowed tools and other arguments as two lists."""
        for child in self.form.winfo_children():
            child.destroy()
        self.getters = []
        kind, _i = self.node
        data = self.target()
        shown = {k: v for k, v in data.items() if kind != "general" or k not in ("providers", "projects")}
        self.position = None
        if kind == "providers":
            rest, tools, self.position = split_args(shown.get("args"))
            shown = {k: v for k, v in shown.items() if k != "args"}
            shown["allowedTools"], shown["args"] = tools, rest
            shown.setdefault("beeMode", "process" if shown.get("protocol") == "codex" else "subagent")   # always offer the setting, with the value that applies without an entry
        self.fields(self.form, shown, ())
        self.add_row(self.form, ())

    # Rows for the keys of a dict.
    def fields(self, parent, data, path):
        """Add one labelled editor per key of `data` below `parent`; `path` names the nesting for the collected values."""
        tk = self.tk
        for key, value in data.items():
            row = tk.Frame(parent)
            row.pack(fill="x", pady=2, anchor="w")
            if isinstance(value, dict):
                box = tk.LabelFrame(row, text=key, padx=6, pady=4)
                box.pack(fill="x", expand=True)
                self.fields(box, value, path + (key,))
                self.add_row(box, path + (key,))
                continue
            tk.Label(row, text=key, width=22, anchor="nw", font=(self.mono, 10)).pack(side="left", anchor="n")
            self.getters.append((path + (key,), self.editor(row, key, value), value))

    # One editor widget for a value; returns the function that reads it back.
    def editor(self, parent, key, value):
        """Make the editor for `value` (check box, choice, line, one entry per line, or JSON for anything else) and return its getter."""
        tk = self.tk
        if isinstance(value, bool):
            var = tk.BooleanVar(value=value)
            tk.Checkbutton(parent, variable=var).pack(side="left")
            return var.get
        if isinstance(value, str) and key in CHOICES:
            var = tk.StringVar(value=value)
            tk.OptionMenu(parent, var, *dict.fromkeys(CHOICES[key] + (value,))).pack(side="left")
            return var.get
        if isinstance(value, (int, float, str)):
            var = tk.StringVar(value=str(value))
            tk.Entry(parent, textvariable=var, font=(self.mono, 10)).pack(side="left", fill="x", expand=True)
            return var.get
        text = tk.Text(parent, height=min(max(len(value) if isinstance(value, list) else 3, 2), 14), width=90, font=(self.mono, 10), wrap="none")
        if isinstance(value, list) and all(isinstance(v, str) for v in value):
            text.insert("1.0", "\n".join(value))
            text.pack(side="left", fill="x", expand=True)
            return lambda: [line.strip() for line in text.get("1.0", "end").splitlines() if line.strip()]
        text.insert("1.0", json.dumps(value))
        text.pack(side="left", fill="x", expand=True)
        return lambda: json.loads(text.get("1.0", "end"))

    # A row to add one setting.
    def add_row(self, parent, path):
        """Add a small `new setting / value / Add` row below `parent` that creates a setting in the dict at `path` (text, number or true/false)."""
        tk = self.tk
        row = tk.Frame(parent)
        row.pack(fill="x", pady=(6, 2), anchor="w")
        key, value = tk.StringVar(), tk.StringVar()
        tk.Label(row, text="new setting", width=22, anchor="w", fg=self.col["dim"], font=(self.mono, 10)).pack(side="left")
        tk.Entry(row, textvariable=key, width=22, font=(self.mono, 10)).pack(side="left")
        tk.Entry(row, textvariable=value, width=40, font=(self.mono, 10)).pack(side="left", padx=4)

        def add():
            """Create the setting and redraw the form."""
            if not key.get().strip() or not self.collect():
                return
            target = self.target()
            for step in path:
                target = target[step]
            target[key.get().strip()] = parse_typed(value.get())
            self.build_form()
        tk.Button(row, text="Add", command=add).pack(side="left")

    # Read the typed values back into the data.
    def collect(self) -> bool:
        """Write every editor value of the shown part into `self.data`; show the first wrong value in the status line and return False."""
        data = self.target()
        values = {}
        where = ()
        try:
            for path, getter, like in self.getters:
                where = path
                raw = getter()
                values[path] = raw if isinstance(raw, (bool, list, dict)) or isinstance(like, (bool, list)) else parse_typed(raw, like)
        except (ValueError, TypeError) as exc:
            self.status.config(text="%s: %s" % (".".join(where), exc), fg=self.col["red"])
            return False
        for path, raw in values.items():
            if self.node[0] == "providers" and path in (("allowedTools",), ("args",)):
                continue
            target = data
            for step in path[:-1]:
                target = target[step]
            target[path[-1]] = raw
        if self.node[0] == "providers":
            data["args"] = join_args(values.get(("args",), []), values.get(("allowedTools",), []), self.position)
        self.status.config(text="")
        return True

    # Add a provider with the fields the loop needs.
    def add_provider(self):
        """Append a new provider (claude protocol, one slot, fallback use) and show it."""
        if self.machine() and self.collect():
            self.data.setdefault("providers", []).append({"name": "new-provider", "protocol": "claude", "command": "claude", "args": [], "slots": 1, "use": "fallback",
                                                          "classes": {"medium": {"model": "", "effort": "medium"}}})
            self.fill_nodes()
            self.select(1 + len(self.data["providers"]) - 1)

    # Add a project.
    def add_project(self):
        """Append a new project entry (name and workspace) and show it."""
        if self.machine() and self.collect():
            self.data.setdefault("projects", []).append({"name": "new-project", "workspace": ""})
            self.fill_nodes()
            self.select(len(self.entries) - 1)

    # Remove the shown provider or project.
    def remove_node(self):
        """Delete the shown provider or project from the settings (not from the file until Save)."""
        kind, i = self.node
        if kind == "general":
            return
        from . import gui_dialog
        if gui_dialog.ask(self.win, "HaiveQueen", "Remove this %s from the settings?" % kind[:-1], self.mono):
            del self.data[kind][i]
            self.fill_nodes()
            self.select(0)

    # Check and write the file.
    def save(self):
        """Collect the shown part, check everything, write the file and tell the main window; a problem is shown and nothing is written."""
        if not self.collect():
            return
        problems = check(self.data, self.machine())
        if problems:
            self.status.config(text="; ".join(problems)[:300], fg=self.col["red"])
            return
        save_file(self.files[self.index][1], self.data)
        self.status.config(text="Saved.", fg=self.col["green"])
        if self.on_saved:
            self.on_saved()

    # Open the settings folder.
    def open_folder(self):
        """Open the folder of the shown file in the file manager."""
        folder = self.files[self.index][1].parent
        if os.name == "nt":
            os.startfile(str(folder))

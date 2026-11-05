"""Editor for the TOML settings in the graphical window: config.toml of the machine and the project.toml of every Apiary.

This file holds the data layer (read, split the allowed tools, write TOML, check, save with a backup); the window is `ConfigWindow` below.
Only the standard library is used: Python can read TOML but not write it, so `dumps` writes the few TOML forms the settings use.
"""
import json
import math
import os
import re
import shutil
import threading
import tomllib
from pathlib import Path

ALLOWED_FLAG = "--allowedTools"   # the Claude Code argument whose value is the comma separated list of allowed tools
CHOICES = {"protocol": ("claude", "codex", "opencode", "kilo"), "outputFormat": ("claude", "codex", "opencode"), "use": ("preferred", "fallback", "capability-only"), "beeMode": ("subagent", "process")}   # fields that offer a fixed choice
CLASSES = ("light", "medium", "heavy")
# The block "Build" of the General page: exactly these three fields, each with one sentence of explanation (key in the buildJobs table, label, explanation).
BUILD_FIELDS = (
    ("cores", "CPU cores per build", "How many of your processor cores builds may use in total; a build uses fewer when the free RAM is short."),
    ("parallel", "Parallel builds", "How many targets the build command builds at the same time (the number after --parallel); each target gets the cores divided by this number."),
    ("minFreeRamGB", "Minimum free RAM before a build", "A build only starts while at least this many GB of RAM are free, otherwise it waits."),
)


# The number of logical cores of this machine.
def machine_cores() -> int:
    """The number of logical cores (the largest choice of "CPU cores per build")."""
    from . import platform as osplat
    return osplat.logical_cores()


# The values the block "Build" shows for a settings dict.
def build_values(data, cores=None) -> dict:
    """The shown values {cores, parallel, minFreeRamGB} of the block "Build": the core count that applies now (an old coreShare, `fixed` or number gives the
    count it gives today, a count above the cores of the machine is shown as the cores of the machine, none gives two thirds rounded up), the parallel value
    (default 1) and the minimum free RAM in GB (default 2)."""
    from . import buildjobs
    cores = cores or machine_cores()
    raw = data.get("buildJobs")
    bj = buildjobs.settings_from(raw if raw is not None else 0)
    return {"cores": buildjobs.core_limit(bj, cores), "parallel": bj["Parallel"], "minFreeRamGB": bj["MinFreeGB"]}


# Write the three values of the block "Build" into a settings dict.
def apply_build(data, cores_text, parallel_text, ram_text, cores=None):
    """Check the three typed values and write them into `data["buildJobs"]`: the core count (a whole number from 1 to the cores of the machine), the
    parallel value (a whole number from 1) and the minimum free RAM in GB (a number above 0). The old keys coreShare and fixed and an old number
    buildJobs are removed (the new core count replaces them); the other keys of the table stay. A wrong value raises ValueError with the message."""
    cores = cores or machine_cores()

    def whole(text, label, low, high=None):
        """A whole number between low and high."""
        try:
            value = int(str(text).strip())
        except ValueError:
            raise ValueError("%s: a whole number is expected" % label)
        if value < low or (high is not None and value > high):
            raise ValueError("%s: a whole number from %d%s is expected" % (label, low, " to %d" % high if high is not None else " up"))
        return value
    n_cores = whole(cores_text, "CPU cores per build", 1, cores)
    parallel = whole(parallel_text, "Parallel builds", 1)
    try:
        ram = float(str(ram_text).strip().replace(",", "."))
    except ValueError:
        raise ValueError("Minimum free RAM before a build: a number is expected")
    if not math.isfinite(ram) or ram <= 0:
        raise ValueError("Minimum free RAM before a build: a number above 0 is expected")
    old = data.get("buildJobs")
    table = {k: v for k, v in old.items() if k not in ("coreShare", "fixed", "cores", "parallel", "minFreeRamGB")} if isinstance(old, dict) else {}
    table.update({"cores": n_cores, "parallel": parallel, "minFreeRamGB": int(ram) if ram == int(ram) else ram})
    data["buildJobs"] = table


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
        from . import gui_dialog   # imported here: gui_dialog imports gui, which imports this module only on demand
        self.dialog = gui_dialog
        self.files = settings_files(conn, config_dir)
        self.index, self.data, self.node, self.getters, self.entries, self.position = 0, {}, ("general", None), [], [], None
        self.build_vars = None
        self.win = tk.Toplevel(root)
        self.win.title("HaiveQueen settings")
        self.win.geometry("1100x700")
        top = tk.Frame(self.win)
        top.pack(fill="x", padx=8, pady=6)
        self.file_var = tk.StringVar(value=self.files[0][0])
        tk.Label(top, text="File:").pack(side="left")
        self.dialog.option_menu(top, self.file_var, [f[0] for f in self.files], self.mono, self.pick_file).pack(side="left", padx=6)
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
        scroll = self.dialog.scrollbar(body, canvas.yview)
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
        self.login_status = self.login_button = self.benchmark_button = self.benchmark_status = None
        self.build_vars = None   # the three variables of the block "Build" while the General page of config.toml is shown
        if kind == "providers":
            from . import cli_presets
            key = cli_presets.key_for(data)
            if key == cli_presets.CUSTOM:
                data.setdefault("profileEnv", {})   # an own CLI: the variables that point it to the profile folder ({home} = its profile folder)
                shown["profileEnv"] = data["profileEnv"]
                shown.setdefault("loginCommand", "")   # the command that makes the login
                shown.setdefault("loginFiles", [])   # the login files, relative to the profile folder of the CLI
                shown.setdefault("outputFormat", "")   # the output format the CLI writes: claude, codex or opencode
            self.cli_row(key)
            self.login_row()
        if kind == "general" and self.machine():
            shown.pop("buildJobs", None)   # the build settings are the block "Build" below, never the raw table
            self.build_block()
        if kind == "general":
            shown.setdefault("language", "English")   # always offer the language of the owner texts, with the value that applies without an entry
        if kind == "providers":
            rest, tools, self.position = split_args(shown.get("args"))
            shown = {k: v for k, v in shown.items() if k != "args"}
            shown["allowedTools"], shown["args"] = tools, rest
            shown.setdefault("beeMode", "process" if shown.get("protocol") == "codex" else "subagent")   # always offer the setting, with the value that applies without an entry
        self.fields(self.form, shown, ())
        self.add_row(self.form, ())

    # The block "Build" of the General page.
    def build_block(self):
        """Add the block "Build" at the top of the General page: CPU cores per build (a choice from 1 to the cores of the machine), Parallel builds and
        Minimum free RAM before a build (GB), each with one sentence of explanation."""
        tk = self.tk
        cores = machine_cores()
        shown = build_values(self.data, cores)
        box = tk.LabelFrame(self.form, text="Build", padx=6, pady=4)
        box.pack(fill="x", pady=(0, 10), anchor="w")
        cores_var, parallel_var, ram_var = tk.StringVar(value=str(shown["cores"])), tk.StringVar(value=str(shown["parallel"])), tk.StringVar(value="%g" % shown["minFreeRamGB"])
        self.build_vars = (cores_var, parallel_var, ram_var)
        for (key, label, explanation), var in zip(BUILD_FIELDS, self.build_vars):
            row = tk.Frame(box)
            row.pack(fill="x", pady=2, anchor="w")
            tk.Label(row, text=label, width=32, anchor="nw", font=(self.mono, 10)).pack(side="left", anchor="n")
            if key == "cores":
                self.dialog.option_menu(row, var, [str(n) for n in range(1, cores + 1)], self.mono).pack(side="left")
            else:
                tk.Entry(row, textvariable=var, width=8, font=(self.mono, 10)).pack(side="left")
            tk.Label(row, text=explanation, anchor="w", justify="left", fg=self.col["dim"], font=(self.mono, 9), wraplength=620).pack(side="left", padx=12)

    # The provider of the shown page in the form the profile functions read.
    def login_provider(self) -> dict:
        """The shown provider entry as the profile functions need it (name, protocol, command and the own-CLI values), with the defaults config.toml applies."""
        from . import profile
        raw = self.target()
        name = str(raw.get("name") or "")
        proto = str(raw.get("protocol") or name)
        return {"name": name, "protocol": proto, "command": str(raw.get("command") or proto), **profile.custom_fields(raw)}

    # The CLI choice of a provider page.
    def cli_row(self, key):
        """Add the list of the CLIs that run (and Custom CLI) at the top of a provider page; choosing one sets all its values."""
        from . import cli_presets
        tk = self.tk
        row = tk.Frame(self.form)
        row.pack(fill="x", pady=(0, 8), anchor="w")
        tk.Label(row, text="cli", width=22, anchor="nw", font=(self.mono, 10)).pack(side="left", anchor="n")
        self.cli_var = tk.StringVar(value=cli_presets.label_of(key))
        self.dialog.option_menu(row, self.cli_var, cli_presets.labels(), self.mono, self.choose_cli).pack(side="left")

    # A CLI was chosen in the list.
    def choose_cli(self, label):
        """Set the shown provider to the chosen CLI (protocol, command, arguments, Bee mode); Custom CLI keeps the values and offers the fields of an own CLI."""
        from . import cli_presets
        if not self.collect():
            return
        data = self.target()
        key = cli_presets.key_of_label(label)
        if key == cli_presets.CUSTOM:
            data.setdefault("outputFormat", "")
        else:
            cli_presets.apply(data, key)
        self.build_form()

    # Status line and Login button of a provider page.
    def login_row(self):
        """Add the login status ("Login present" or "Login missing", from the profile) and the Login button at the top of a provider page."""
        tk = self.tk
        row = tk.Frame(self.form)
        row.pack(fill="x", pady=(0, 8), anchor="w")
        tk.Label(row, text="login", width=22, anchor="nw", font=(self.mono, 10)).pack(side="left", anchor="n")
        self.login_status = tk.Label(row, anchor="w", font=(self.mono, 10, "bold"))
        self.login_status.pack(side="left")
        self.login_button = tk.Button(row, text="Login", width=12, command=self.login_click)
        self.login_button.pack(side="left", padx=12)
        self.benchmark_button = tk.Button(row, text="Benchmark Start Context", command=self.benchmark_click)
        self.benchmark_button.pack(side="left")
        self.benchmark_status = tk.Label(self.form, anchor="w", justify="left", font=(self.mono, 9), wraplength=900)
        self.benchmark_status.pack(fill="x", pady=(0, 8), anchor="w")
        self.show_benchmark()
        self.refresh_login()

    # Show whether the shown provider has its login in the profile.
    def refresh_login(self):
        """Read the login state again from the profile folder and show it; does nothing on a page without a login row."""
        from . import profile
        if self.login_status is None:
            return
        present = profile.has_login(self.login_provider())
        self.login_status.config(text="Login present" if present else "Login missing", fg=self.col["green"] if present else self.col["red"])

    # The Login button.
    def login_click(self):
        """After a safety question open the terminal window for the login of the shown provider; the state is read again when that window is closed."""
        from . import profile
        if not self.collect():
            return
        prov = self.login_provider()
        if not profile.login_command(prov):
            self.dialog.info(self.win, "HaiveQueen", "No login is set up for this provider. For a CLI that is not built in, set the profile variables, "
                             "the login command and the login files on this page.", self.mono)
            return
        if not self.dialog.ask(self.win, "HaiveQueen", "Open a terminal window and run `%s` for provider %s in the HaiveQueen profile?" % (profile.login_command(prov), prov["name"]), self.mono):
            return
        proc = profile.login_run(prov)
        if proc is None:
            self.dialog.error(self.win, "HaiveQueen", "The login window could not be opened (no terminal program found).", self.mono)
            return
        self.watch_login(proc)

    # The stored benchmark of the shown provider.
    def benchmark_lines(self, name) -> list:
        """One line per Apiary with the stored benchmark of provider `name` (with the date of the measurement), or the note that there is none yet."""
        from . import startctx
        rows = startctx.benchmarks(self.conn, name)
        out = []
        for project in sorted({r["project"] for r in rows}):
            mine = [r for r in rows if r["project"] == project]
            out.append("%s: %s (%s)" % (project, startctx.benchmark_text(self.conn, name, project), max(r["measured_at"] for r in mine)[:10]))
        return out or ["Start context: no benchmark yet"]

    # Show the stored benchmark and an error line on the page.
    def show_benchmark(self, notes=(), error=False):
        """Write the stored benchmark of the shown provider and the extra lines `notes` (errors in red) into the status text under the buttons."""
        if self.benchmark_status is None:
            return
        self.benchmark_status.config(text="\n".join(list(notes) + self.benchmark_lines(self.login_provider()["name"])), fg=self.col["red"] if error else self.col["dim"])

    # The Benchmark Start Context button.
    def benchmark_click(self, wait=False):
        """After a safety question measure the start context of the shown provider in every registered Apiary, with and without the HaiveQueen profile
        (in a thread, so the window stays usable; `wait` runs it here). The results and the errors of failed runs are shown on the page."""
        from . import startctx, db
        if not self.collect():
            return
        name = self.login_provider()["name"]
        if not self.dialog.ask(self.win, "HaiveQueen", "Measure the start context of provider %s in every registered Apiary, twice: once with the HaiveQueen profile and once "
                               "with your own configuration? This starts the CLI %d times and uses a few tokens of your login." % (
                                   name, 2 * self.conn.execute("SELECT COUNT(*) FROM projects").fetchone()[0]), self.mono):
            return
        cfg = dict(self.data)
        self.benchmark_button.config(state="disabled")
        self.benchmark_status.config(text="measuring …", fg=self.col["dim"])
        box = {}
        path = next((r[2] for r in self.conn.execute("PRAGMA database_list") if r[1] == "main"), "")

        def work():
            """Measure on an own connection (SQLite connections belong to one thread)."""
            conn = self.conn if wait else db.connect(path)
            try:
                box["result"] = startctx.benchmark(conn, cfg, name, progress=lambda text: box.__setitem__("progress", text))
            except Exception as exc:
                box["result"] = {"excluded": False, "errors": [str(exc)], "results": []}
            finally:
                if conn is not self.conn:
                    conn.close()

        def finish():
            """Show the result in the status text."""
            res = box["result"]
            try:
                self.benchmark_button.config(state="normal")
                if res["excluded"]:
                    self.show_benchmark(["Provider %s is excluded (excludedProviders): nothing was measured." % name], True)
                else:
                    self.show_benchmark(["Error: " + e for e in res["errors"]], bool(res["errors"]))
            except self.tk.TclError:
                pass   # the page was closed meanwhile

        def poll():
            """Wait for the thread without blocking the window."""
            try:
                if not self.win.winfo_exists():
                    return
                if "result" in box:
                    finish()
                else:
                    if box.get("progress"):
                        self.benchmark_status.config(text=box["progress"], fg=self.col["dim"])   # e.g. "2/4: ODP with the HaiveQueen profile ..."
                    self.win.after(300, poll)
            except self.tk.TclError:
                pass
        if wait:
            work()
            finish()
        else:
            threading.Thread(target=work, daemon=True).start()
            self.win.after(300, poll)

    # Refresh the state when the terminal window is closed.
    def watch_login(self, proc):
        """Check every 2 seconds whether the login window is still open; when it is closed, read the login state again."""
        def check():
            """Re-arm the timer while the window is open, else refresh."""
            try:
                if not self.win.winfo_exists():
                    return
                if proc.poll() is None:
                    self.win.after(2000, check)
                else:
                    self.refresh_login()
            except self.tk.TclError:
                pass
        self.win.after(2000, check)

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
            self.dialog.option_menu(parent, var, list(dict.fromkeys(CHOICES[key] + (value,))), self.mono).pack(side="left")
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
        if self.build_vars is not None:
            try:
                apply_build(self.data, *[v.get() for v in self.build_vars], cores=machine_cores())
            except ValueError as exc:
                self.status.config(text=str(exc), fg=self.col["red"])
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

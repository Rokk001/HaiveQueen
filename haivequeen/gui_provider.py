"""The provider page and the server page of the settings window.

The provider page shows five sections from top to bottom: CLI, Model source, Models, Use and Advanced (closed each time the page opens).
Every field has a one-line explanation in the language of the language setting. The server page holds the fields of a server entry and the
"Test connection" button. `ProviderPages` is a mixin of `gui_config.ConfigWindow`: it uses the rows, getters and collect of that class.
"""
import threading

from . import cli_presets, servers
from .i18n import N_, tr

CLASSES = ("light", "medium", "heavy")
SOURCES = (N_("Account login"), N_("Own server"))
NEW_SERVER = N_("New server")
# (label, explanation) of every field of the two pages, by the key the page uses.
TEXT = {
    "name": (N_("Name"), N_("How this provider is called in HaiveQueen; it follows the CLI and the model source until you change it yourself.")),
    "cli": (N_("CLI"), N_("The program HaiveQueen starts for the agents of this provider.")),
    "protocol": (N_("Protocol"), N_("How HaiveQueen talks to your own CLI and reads its output: claude, codex, opencode or kilo.")),
    "command": (N_("Command"), N_("The command that starts your own CLI.")),
    "outputFormat": (N_("Output format"), N_("Which output format your own CLI writes: claude, codex or opencode.")),
    "loginCommand": (N_("Login command"), N_("The command that makes the login of your own CLI.")),
    "loginFiles": (N_("Login files"), N_("The login files of your own CLI, relative to its profile folder, one per line.")),
    "profileEnv": (N_("Profile variables"), N_("Variables that point your own CLI to its profile folder; {home} stands for that folder.")),
    "source": (N_("Model source"), N_("Where the models come from: the login of your account, or an LLM server of your own.")),
    "server": (N_("Server"), N_("The server this provider uses; servers are set up on the tab Servers.")),
    "login": (N_("Login"), N_("The login of the HaiveQueen profile of this CLI; use the Login button when it is missing.")),
    "light": (N_("Light"), N_("Model and effort for small tasks; leave the model empty and this provider does not offer this class.")),
    "medium": (N_("Medium"), N_("Model and effort for normal tasks; leave the model empty and this provider does not offer this class.")),
    "heavy": (N_("Heavy"), N_("Model and effort for the hardest tasks; leave the model empty and this provider does not offer this class.")),
    "benchmark": (N_("Benchmark"), N_("Measures how much context an agent of this provider can use; only for a saved provider that is ready.")),
    "slots": (N_("Simultaneous agents"), N_("How many agents of this provider may work at the same time.")),
    "use": (N_("Priority"), N_("A preferred provider gets work first; a fallback provider only when the preferred ones are busy or not available.")),
    "capabilities": (N_("Capabilities"), N_("What this provider may be used for; a task only goes to a provider that has the capability it needs.")),
    "beeMode": (N_("Bee mode"), N_("subagent runs Bees inside the session of the Queen (Claude Code only); process starts every Bee as a process of its own.")),
    "allowedTools": (N_("Allowed tools"), N_("The tools an agent may use without asking, one per line.")),
    "args": (N_("Arguments"), N_("The arguments HaiveQueen gives to the CLI, one per line; {model}, {effort} and the like are filled in at the start.")),
    "limit": (N_("Usage check"), N_("Where HaiveQueen reads how much of the usage limit is left: none, claude or codex.")),
    "own": (N_("Own settings"), N_("Other settings of this provider exactly as they stand in config.toml; a new setting is written as typed.")),
    "serverName": (N_("Name"), N_("How this server is called in the provider pages; renaming it renames it in the providers that use it.")),
    "address": (N_("Address"), N_("Where the server answers, for example http://192.168.1.20:1234, without /v1.")),
    "apiKey": (N_("API key"), N_("The key of the server; optional, shown masked and kept only in config.toml of this machine.")),
    "contextWindow": (N_("Context window (tokens)"), N_("How many tokens the model of the server can hold; empty means nothing is handed to the CLI.")),
    "test": (N_("Test connection"), N_("Checks the address, reads the model list and tests the interface each CLI needs; the result is kept.")),
}
SHARED_LOGIN = N_("This login is shared by all providers with this CLI.")
CUSTOM_SOURCE = N_("A custom CLI takes only the account login: HaiveQueen does not know how it takes a server address.")
HANDLED = {"name", "nameEdited", "protocol", "command", "server", "slots", "use", "capabilities", "capabilitiesVersion", "beeMode", "allowedTools", "args",
           "limit", "classes", "profileEnv", "loginCommand", "loginFiles", "outputFormat"}
USAGE_SOURCES = ("none", "claude", "codex")


class ProviderPages:
    """Mixin of ConfigWindow: builds the provider page and the server page and handles their choices."""

    # ------------------------------------------------------------ small building blocks

    def section(self, key, title):
        """A framed section of the provider page, remembered in `self.sections` in the order of the page."""
        box = self.tk.LabelFrame(self.form, text=tr(title), padx=6, pady=4)
        box.pack(fill="x", pady=(0, 8), anchor="w")
        self.sections.append((key, box))
        return box

    def note(self, parent, key, text=None):
        """The one-line explanation under a field (dim, wrapped to the width that is available); remembered in `self.notes` by `key`."""
        source = text if text is not None else TEXT[key][1]
        shown = tr(source)
        self.notes[key], self.note_sources[key] = shown, source
        label = self.tk.Label(parent, text=shown, anchor="w", justify="left", fg=self.col["dim"], font=(self.mono, 9), wraplength=300)
        label.pack(fill="x", anchor="w", padx=(4, 0))
        parent.bind("<Configure>", lambda e, n=label: n.configure(wraplength=max(120, e.width - 12)), add="+")
        return label

    def line(self, parent, key, label=None):
        """A row with its label on the left; returns (item frame, row frame). The explanation is added below the row by the caller."""
        item = self.tk.Frame(parent)
        item.pack(fill="x", pady=2, anchor="w")
        row = self.tk.Frame(item)
        row.pack(fill="x", anchor="w")
        self.tk.Label(row, text=tr(label if label is not None else TEXT[key][0]), width=24, anchor="nw", justify="left", wraplength=200, font=(self.mono, 10)).pack(side="left", anchor="n")
        return item, row

    def field(self, parent, frame, path, value, key, choices=None, texts=None):
        """A labelled editor for the setting at `path` with its explanation (label and explanation of TEXT[texts or key]); the getter is registered for
        collect and belongs to `frame`."""
        item, row = self.line(parent, texts or key)
        self.getters.append((path, self.editor(row, path[-1], value, choices), value))
        self.frame_of[path] = frame
        self.note(item, key, TEXT[texts or key][1])

    def in_frame(self, path, frame) -> bool:
        """True when the editor of `path` sits in `frame` (a section of the page)."""
        return self.frame_of.get(path) is frame

    def mark(self, start, frame):
        """Assign the getters registered since index `start` to `frame`."""
        for path, _getter, _like in self.getters[start:]:
            self.frame_of[path] = frame

    def reshow(self):
        """Show the page again with the list on the left refreshed (a name or an entry changed)."""
        self.fill_nodes()
        self.select(self.entries.index(self.node))

    # ------------------------------------------------------------ the provider page

    def is_fresh(self, entry) -> bool:
        """True for a provider added in this window and not saved yet."""
        return any(entry is e for e in self.fresh)

    def proposed_name(self, entry) -> str:
        """The name proposed from CLI and source ("codex", "codex-lmstudio"); a taken name gets a number added ("codex-2")."""
        key = cli_presets.key_for(entry)
        base = key if key != cli_presets.CUSTOM else "custom"
        if entry.get("server"):
            base += "-" + servers.server_id(entry["server"])
        taken = {p.get("name") for p in self.data.get("providers") or [] if p is not entry}
        name, n = base, 1
        while name in taken:
            n += 1
            name = "%s-%d" % (base, n)
        return name

    def follow_name(self, entry):
        """A provider whose name the user did not edit takes the proposal; an existing provider (no mark) keeps its name."""
        if entry.get("nameEdited", True) is False:
            entry["name"] = self.proposed_name(entry)

    def offered(self, entry):
        """(models, efforts, own_only) the Models section offers: the list of the server (last test) or of the CLI; a custom CLI offers only the own entry."""
        key = cli_presets.key_for(entry)
        if key == cli_presets.CUSTOM:
            return [], [], True
        efforts = cli_presets.effort_list(key)
        if entry.get("server"):
            try:
                server = servers.get_server(self.data, entry["server"])
            except ValueError:
                server = None
            result = servers.kept(server) if server else None
            return (list(result["models"]) if result else []), efforts, False
        return cli_presets.model_list(key), efforts, False

    def provider_form(self):
        """Fill the form with the provider page: name, ready line and the five sections."""
        tk = self.tk
        data = self.target()
        key = cli_presets.key_for(data)
        if isinstance(data.get("limit"), str):
            data["limit"] = {"source": data["limit"]}   # the same meaning, the form of a table
        self.sections, self.notes, self.note_sources, self.frame_of = [], {}, {}, {}
        top = tk.Frame(self.form)
        top.pack(fill="x", anchor="w")
        self.field(top, top, ("name",), str(data.get("name") or ""), "name")
        self.ready_label = tk.Label(top, anchor="w", justify="left", fg=self.col["red"], font=(self.mono, 10, "bold"))
        self.ready_label.pack(fill="x", anchor="w", pady=(0, 6))
        self.cli_section(self.section("cli", N_("CLI")), key, data)
        self.source_section(self.section("source", N_("Model source")), key, data)
        self.models_section(self.section("models", N_("Models")), key, data)
        self.use_section(self.section("use", N_("Use")), data)
        self.advanced_section(key, data)
        self.initial_values = {path: getter() for path, getter, _like in self.getters}
        self.refresh_login()
        self.refresh_state()

    def cli_section(self, box, key, data):
        """CLI: the choice of the CLIs; protocol, command and the own-CLI values only for Custom CLI."""
        item, row = self.line(box, "cli")
        self.cli_var = self.tk.StringVar(value=cli_presets.label_of(key))
        self.dialog.option_menu(row, self.cli_var, cli_presets.labels(), self.mono, self.choose_cli).pack(side="left")
        self.note(item, "cli")
        if key != cli_presets.CUSTOM:
            return
        start = len(self.getters)
        self.field(box, box, ("protocol",), str(data.get("protocol") or data.get("name") or ""), "protocol", cli_presets_protocols())
        self.field(box, box, ("command",), str(data.get("command") or data.get("protocol") or ""), "command")
        self.field(box, box, ("outputFormat",), str(data.get("outputFormat") or ""), "outputFormat", ("", "claude", "codex", "opencode"))
        self.field(box, box, ("loginCommand",), str(data.get("loginCommand") or ""), "loginCommand")
        self.field(box, box, ("loginFiles",), list(data.get("loginFiles") or []), "loginFiles")
        self.field(box, box, ("profileEnv",), dict(data.get("profileEnv") or {}), "profileEnv")
        self.mark(start, box)

    def source_section(self, box, key, data):
        """Model source: Account login (status, Login button, shared-login sentence) or Own server (choice of servers and New server)."""
        tk = self.tk
        custom = key == cli_presets.CUSTOM
        self.source_values = [tr(SOURCES[0])] if custom else [tr(s) for s in SOURCES]
        item, row = self.line(box, "source")
        self.source_var = tk.StringVar(value=tr(SOURCES[1]) if data.get("server") and not custom else tr(SOURCES[0]))
        self.dialog.option_menu(row, self.source_var, self.source_values, self.mono, self.choose_source).pack(side="left")
        self.note(item, "source")
        self.login_status = self.login_button = None
        self.server_var, self.server_values = None, []
        if custom:
            tk.Label(box, text=tr(CUSTOM_SOURCE), anchor="w", justify="left", fg=self.col["dim"], font=(self.mono, 9), wraplength=500).pack(fill="x", anchor="w", padx=(4, 0))
        if data.get("server") and not custom:
            names = [s.get("name") for s in self.data.get("servers") or []]
            self.server_values = names + [tr(NEW_SERVER)]
            item, row = self.line(box, "server")
            self.server_var = tk.StringVar(value=str(data["server"]))
            self.dialog.option_menu(row, self.server_var, self.server_values, self.mono, self.choose_server).pack(side="left")
            self.note(item, "server")
            return
        item, row = self.line(box, "login")
        flow = tk.Frame(row)
        flow.pack(side="left", anchor="n")
        self.login_status = tk.Label(flow, anchor="w", font=(self.mono, 10, "bold"))
        self.login_button = tk.Button(flow, text=tr("Login"), width=12, command=self.login_click)
        self.login_status.pack(side="left", padx=(0, 8))
        self.login_button.pack(side="left")
        self.note(item, "login")
        tk.Label(item, text=tr(SHARED_LOGIN), anchor="w", justify="left", fg=self.col["dim"], font=(self.mono, 9)).pack(fill="x", anchor="w", padx=(4, 0))

    def models_section(self, box, key, data):
        """Models: three rows light, medium, heavy with a model choice (list plus own entry) and an effort choice; a custom CLI offers only the own entry."""
        tk = self.tk
        models, efforts, own_only = self.offered(data)
        self.offered_models, self.offered_own_only = models, own_only
        self.effort_values = list(efforts)
        self.model_vars, self.effort_vars, self.model_boxes, self.model_entries = {}, {}, {}, {}
        classes = data.get("classes") if isinstance(data.get("classes"), dict) else {}
        for cls in CLASSES:
            current = classes.get(cls) if isinstance(classes.get(cls), dict) else {}
            model, effort = str(current.get("model") or ""), str(current.get("effort") or "")
            item, row = self.line(box, cls)
            self.model_vars[cls], self.effort_vars[cls] = tk.StringVar(value=model), tk.StringVar(value=effort)
            if own_only:
                entry = tk.Entry(row, textvariable=self.model_vars[cls], width=34, font=(self.mono, 10))
                entry.pack(side="left", padx=(0, 6))
                self.model_entries[cls] = entry
            else:
                from tkinter import ttk
                combo = ttk.Combobox(row, textvariable=self.model_vars[cls], values=[""] + list(models), width=34, font=(self.mono, 10))
                combo.pack(side="left", padx=(0, 6))
                self.model_boxes[cls] = combo
            if self.effort_values:
                self.dialog.option_menu(row, self.effort_vars[cls], list(dict.fromkeys(self.effort_values + ([effort] if effort else []))), self.mono).pack(side="left")
            else:
                tk.Entry(row, textvariable=self.effort_vars[cls], width=12, font=(self.mono, 10)).pack(side="left")
            self.note(item, cls)
            for name, var in (("model", self.model_vars[cls]), ("effort", self.effort_vars[cls])):
                path = ("classes", cls, name)
                self.getters.append((path, var.get, var.get()))
                self.frame_of[path] = box
        item, row = self.line(box, "benchmark")
        self.benchmark_button = tk.Button(row, text=tr("Benchmark Start Context"), command=self.benchmark_click)
        self.benchmark_button.pack(side="left")
        self.note(item, "benchmark")

    def use_section(self, box, data):
        """Use: simultaneous agents, priority and the capability ticks."""
        from . import scheduler
        self.use_frame = box
        self.field(box, box, ("slots",), data.get("slots", self.data.get("slots", 1)), "slots")
        use = "preferred" if data.get("use") == "capability-only" else data.get("use", "preferred")
        self.field(box, box, ("use",), use, "use")
        self.field(box, box, ("capabilities",), scheduler.provider_capabilities(data), "capabilities")

    def advanced_section(self, key, data):
        """Advanced (closed): Bee mode, allowed tools, arguments, usage check and the own settings (other class settings and any other key)."""
        from . import gui_config
        tk = self.tk
        outer = tk.Frame(self.form)
        outer.pack(fill="x", pady=(0, 8), anchor="w")
        self.sections.append(("advanced", outer))
        self.advanced_open = False
        self.advanced_button = tk.Button(outer, anchor="w", relief="flat", command=self.toggle_advanced)
        self.advanced_button.pack(fill="x", anchor="w")
        frame = tk.Frame(outer, padx=6, pady=4)
        self.advanced_frame = frame
        self.show_advanced()
        self.field(frame, frame, ("beeMode",), str(data.get("beeMode") or ("process" if data.get("protocol") == "codex" else "subagent")), "beeMode")
        rest, tools, self.position = gui_config.split_args(data.get("args"))
        self.field(frame, frame, ("allowedTools",), tools, "allowedTools")
        self.field(frame, frame, ("args",), rest, "args")
        limit = data.get("limit") if isinstance(data.get("limit"), dict) else {}
        self.field(frame, frame, ("limit", "source"), str(limit.get("source") or "none"), "limit", USAGE_SOURCES)
        # own settings: every key without a field above, the other settings of a class and the kept endpoint
        start = len(self.getters)
        item = tk.Frame(frame)
        item.pack(fill="x", pady=(8, 2), anchor="w")
        tk.Label(item, text=tr(TEXT["own"][0]), anchor="w", font=(self.mono, 10, "bold")).pack(fill="x")
        self.note(item, "own")
        extra = {k: v for k, v in data.items() if k not in HANDLED}
        if extra:
            self.fields(frame, extra, ())
        other_limit = {k: v for k, v in limit.items() if k != "source"}
        if other_limit:
            self.fields(frame, {"limit": other_limit}, ())
        classes = data.get("classes") if isinstance(data.get("classes"), dict) else {}
        for cls, entry in classes.items():
            if not isinstance(entry, dict):
                continue
            rest_of = {k: v for k, v in entry.items() if k not in ("model", "effort", "customModel")} if cls in CLASSES else entry
            if rest_of and cls in CLASSES:
                box = tk.LabelFrame(frame, text=tr("Own settings of class %s", tr(TEXT[cls][0])), padx=6, pady=4)
                box.pack(fill="x", pady=2, anchor="w")
                self.fields(box, rest_of, ("classes", cls))
            elif cls not in CLASSES:
                self.fields(frame, {"classes": {cls: entry}}, ())
        self.add_row(frame, ())
        self.mark(start, frame)

    def show_advanced(self):
        """Show or hide the Advanced rows by `self.advanced_open` and set the text of the button."""
        self.advanced_button.config(text=("▾ " if self.advanced_open else "▸ ") + tr("Advanced"))
        if self.advanced_open:
            self.advanced_frame.pack(fill="x", anchor="w")
        else:
            self.advanced_frame.pack_forget()

    def toggle_advanced(self):
        """The Advanced button."""
        self.advanced_open = not self.advanced_open
        self.show_advanced()

    # ------------------------------------------------------------ choices on the provider page

    def choose_cli(self, label):
        """Set the shown provider to the chosen CLI (protocol, command, arguments, Bee mode); Custom CLI keeps the values and offers only the account login;
        a provider whose name the user did not edit takes the new proposal."""
        if not self.collect():
            return
        data = self.target()
        key = cli_presets.key_of_label(label)
        if key == cli_presets.CUSTOM:
            data.setdefault("outputFormat", "")
            data.pop("server", None)
        else:
            cli_presets.apply(data, key)
        self.follow_name(data)
        self.reshow()

    def choose_source(self, label):
        """Switch the model source: Account login, or Own server (the first server; a new one when there is none). The ticks and the usage check stay (a new provider gets the usage check off)."""
        if not self.collect():
            return
        data = self.target()
        if label == tr(SOURCES[1]):
            names = [s.get("name") for s in self.data.get("servers") or []]
            if not names:
                self.new_server(data)
                return
            servers.set_source(data, names[0], new=self.is_fresh(data))
        else:
            servers.set_source(data, None)
        self.follow_name(data)
        self.reshow()

    def choose_server(self, label):
        """Use the chosen server, or set up a new one on its own page."""
        if not self.collect():
            return
        data = self.target()
        if label == tr(NEW_SERVER):
            self.new_server(data)
            return
        servers.set_source(data, label, new=False)
        self.follow_name(data)
        self.reshow()

    def new_server(self, provider=None):
        """Add a server entry (given to `provider` when there is one) and show its page."""
        names = {s.get("name") for s in self.data.get("servers") or []}
        name, n = "server", 1
        while name in names:
            n += 1
            name = "server-%d" % n
        self.data.setdefault("servers", []).append({"name": name, "address": ""})
        if provider is not None:
            servers.set_source(provider, name, new=self.is_fresh(provider))
            self.follow_name(provider)
        self.fill_nodes()
        self.pick_tab_quiet("servers")
        self.select(self.entries.index(("servers", len(self.data["servers"]) - 1)))

    def pick_tab_quiet(self, tab):
        """Show the pages of `tab` in the list without collecting (the caller did)."""
        self.show_tab(tab)

    def add_provider(self):
        """Append a new provider (Claude Code, account login, name proposed from CLI and source) and show it."""
        if not self.collect():
            return
        entry = {"name": "", "slots": 1, "use": "fallback", "capabilities": ["Coding", "Queen"], "capabilitiesVersion": 1, "nameEdited": False,
                 "classes": {"medium": {"model": "", "effort": "medium"}}}
        cli_presets.apply(entry, "claude")
        self.data.setdefault("providers", []).append(entry)
        entry["name"] = self.proposed_name(entry)
        self.fresh.append(entry)
        self.fill_nodes()
        self.select(len(self.data["providers"]))

    def add_server(self):
        """Append a new server entry and show its page."""
        if self.collect():
            self.new_server()

    # ------------------------------------------------------------ ready line and benchmark button

    def provider_state(self) -> tuple:
        """(ready, what is missing) of the shown provider as the loop evaluates it now (login or last connection test, models)."""
        from . import scheduler
        try:
            resolved = scheduler.get_providers({**self.data, "providers": [self.target()]})[0]
        except (ValueError, TypeError, KeyError, AttributeError) as exc:
            return False, str(exc)
        return bool(resolved["login"]), resolved["notReady"]

    def saved_provider(self) -> bool:
        """True when the shown provider is in config.toml exactly as it stands on the page."""
        from . import gui_config
        entry = self.target()
        try:
            saved = gui_config.load_migrated(self.config_path)
        except Exception:
            return False
        return any(p == entry for p in saved.get("providers") or [])

    def refresh_state(self):
        """Show "Not ready: ..." when the provider is not ready, and enable Benchmark Start Context only for a saved, ready provider."""
        if getattr(self, "ready_label", None) is None or self.node[0] != "providers" or self.node[1] is None:
            return
        ready, why = self.provider_state()
        self.ready_label.config(text="" if ready else tr("Not ready: %s", why))
        if self.benchmark_button is not None:
            self.benchmark_button.config(state="normal" if ready and self.saved_provider() else "disabled")

    # ------------------------------------------------------------ the server page

    def server_form(self):
        """Fill the form with the page of one server: name, address, API key (masked), context window and the Test connection button."""
        tk = self.tk
        data = self.target()
        self.notes, self.note_sources, self.frame_of = {}, {}, {}
        form = self.form
        self.field(form, form, ("name",), str(data.get("name") or ""), "name", texts="serverName")
        self.field(form, form, ("address",), str(data.get("address") or ""), "address")
        item, row = self.line(form, "apiKey")
        key_var = tk.StringVar(value=servers.mask_key(data.get("apiKey")))
        tk.Entry(row, textvariable=key_var, show="*", width=40, font=(self.mono, 10)).pack(side="left", fill="x", expand=True)
        self.getters.append((("apiKey",), key_var.get, key_var.get()))
        self.note(item, "apiKey")
        window = servers.window_of(data)
        self.field(form, form, ("contextWindow",), str(window) if window else "", "contextWindow")
        item, row = self.line(form, "test")
        self.test_button = tk.Button(row, text=tr("Test connection"), command=self.test_click)
        self.test_button.pack(side="left")
        self.note(item, "test")
        self.result_label = tk.Label(form, anchor="w", justify="left", font=(self.mono, 10))
        self.result_label.pack(fill="x", anchor="w", pady=4)
        self.show_result()
        self.initial_values = {path: getter() for path, getter, _like in self.getters}

    def server_entry(self) -> dict:
        """The shown server as the tests use it: name, address without /v1, the stored API key and the context window."""
        data = self.target()
        return {"name": str(data.get("name") or ""), "address": servers.clean_address(data.get("address")), "apiKey": str(data.get("apiKey") or ""),
                "contextWindow": servers.window_of(data)}

    def show_result(self):
        """Show the kept result of the last test of the shown server (time and one line per CLI)."""
        if getattr(self, "result_label", None) is not None:
            self.result_label.config(text=servers.summary(self.server_entry()))

    def test_click(self):
        """The Test connection button: keep the typed values, test in the background and show the result with its time when it is there."""
        if not self.collect():
            return
        server = self.server_entry()
        if not server["address"]:
            self.result_label.config(text=tr("server %s has no address", server["name"]))
            return
        self.result_label.config(text=tr("Testing ..."))
        outcome = {}

        def work():
            """Run the test (it keeps its result in the data folder)."""
            try:
                servers.run_test(server)
            except Exception as exc:   # shown on the page, never lost in the thread
                outcome["error"] = str(exc)
        thread = threading.Thread(target=work, daemon=True)
        thread.start()
        shown = self.node

        def poll():
            """Wait for the test and show its result, unless another page is shown meanwhile."""
            try:
                if not self.win.winfo_exists():
                    return
                if thread.is_alive():
                    self.win.after(100, poll)
                elif self.node == shown and getattr(self, "result_label", None) is not None:
                    self.result_label.config(text=outcome.get("error") or servers.summary(server))
            except self.tk.TclError:
                pass
        self.win.after(100, poll)


def cli_presets_protocols() -> tuple:
    """The protocols a custom CLI can name."""
    return tuple(p["protocol"] for p in cli_presets.PRESETS.values())

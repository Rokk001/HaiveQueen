"""The provider form of the settings window.

One form per provider: name, the dropdown "Access type" (Subscription, API key or Local LLM) and below it the sections CLI, the fields of the chosen access type,
Models, Use and Advanced (closed each time the page opens). Changing the access type changes the form at once. Every field has a one-line explanation in the
language of the language setting. `ProviderPages` is a mixin of `gui_config.ConfigWindow`: it uses the rows, getters and collect of that class.
"""
import copy
import threading

from . import cli_presets, gui_dialog, servers
from .i18n import N_, tr

CLASSES = ("light", "medium", "heavy")
ACCESS = (("subscription", N_("Subscription")), ("api", N_("API key")), ("llm", N_("Local LLM")))   # (key, label) of the access types, in the order of the dropdown
TYPE_FIELDS = ("apiKey", "address", "contextWindow")   # the entry values that belong to one access type only
# (label, explanation) of every field of the two pages, by the key the page uses.
TEXT = {
    "enabled": (N_("Enabled"), N_("A disabled provider gets no new work and shows nowhere in the dashboard; its settings stay.")),
    "name": (N_("Name"), N_("How this provider is called in HaiveQueen; it follows the CLI and the access type until you change it yourself.")),
    "cli": (N_("CLI"), N_("The program HaiveQueen starts for the agents of this provider.")),
    "protocol": (N_("Protocol"), N_("How HaiveQueen talks to your own CLI and reads its output: claude, codex, opencode or kilo.")),
    "command": (N_("Command"), N_("The command that starts your own CLI.")),
    "outputFormat": (N_("Output format"), N_("Which output format your own CLI writes: claude, codex or opencode.")),
    "loginCommand": (N_("Login command"), N_("The command that makes the login of your own CLI.")),
    "loginFiles": (N_("Login files"), N_("The login files of your own CLI, relative to its profile folder, one per line.")),
    "profileEnv": (N_("Profile variables"), N_("Variables that point your own CLI to its profile folder; {home} stands for that folder.")),
    "access": (N_("Access type"), N_("How this provider reaches its models: with the login of a subscription, with an API key, or with a local LLM of your own.")),
    "login": (N_("Login"), N_("The login of the HaiveQueen profile of this CLI; use the Login button when it is missing.")),
    "light": (N_("Light"), N_("Model and effort for small tasks; leave the model empty and this provider does not offer this class.")),
    "medium": (N_("Medium"), N_("Model and effort for normal tasks; leave the model empty and this provider does not offer this class.")),
    "heavy": (N_("Heavy"), N_("Model and effort for the hardest tasks; leave the model empty and this provider does not offer this class.")),
    "benchmark": (N_("Benchmark"), N_("Measures how much context an agent of this provider can use; only for a saved provider that is ready.")),
    "slots": (N_("Simultaneous agents"), N_("How many agents of this provider may work at the same time.")),
    "use": (N_("Priority"), N_("A preferred provider gets work first; a fallback provider only when the preferred ones are busy or not available.")),
    "capabilities": (N_("Capabilities"), N_("What this provider may be used for; a task only goes to a provider that has the capability it needs.")),
    "beeMode": (N_("Bee mode"), N_("subagent runs Bees inside the session of the Queen (Claude Code only); process starts every Bee as a process of its own.")),
    "allowedTools": (N_("Commands allowed without asking"), N_("Commands agents may run without asking, one per line.")),
    "args": (N_("Arguments"), N_("The arguments HaiveQueen gives to the CLI, one per line; {model}, {effort} and the like are filled in at the start.")),
    "limit": (N_("Usage check"), N_("Where HaiveQueen reads how much of the usage limit is left: none, claude or codex.")),
    "own": (N_("Own settings"), N_("Other settings of this provider exactly as they stand in config.toml; a new setting is written as typed.")),
    "address": (N_("Address"), N_("Where the local LLM answers, for example http://192.168.1.20:1234, without /v1.")),
    "apiKey": (N_("API key"), N_("The key of the local LLM; optional, shown masked and kept only in config.toml of this machine.")),
    "providerKey": (N_("API key"), N_("Your API key; shown masked, kept only in config.toml of this machine and handed only to the agents HaiveQueen starts.")),
    "contextWindow": (N_("Context window (tokens)"), N_("How many tokens the model of the local LLM can hold; empty means nothing is handed to the CLI.")),
    "test": (N_("Test connection"), N_("Checks the address, reads the model list and tests the interface each CLI needs; the result is kept.")),
}
SHARED_LOGIN = N_("This login is shared by all providers with this CLI.")
HANDLED = {"enabled", "name", "nameEdited", "access", "apiKey", "address", "contextWindow", "protocol", "command", "server", "slots", "use", "capabilities", "capabilitiesVersion", "beeMode", "allowedTools", "args",
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
        self.rows[path] = row
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
        """The name proposed from CLI and access type: Subscription the CLI name ("codex"), API key plus "-api", Local LLM plus "-llm", Custom CLI "custom";
        a taken name gets a number added ("codex-2")."""
        key = cli_presets.key_for(entry)
        base = key if key != cli_presets.CUSTOM else "custom"
        if key != cli_presets.CUSTOM:
            base += {"api": "-api", "llm": "-llm"}.get(servers.access_of(entry), "")
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
        """(models, efforts, own_only) the Models section offers: the list of the Local LLM (last test) or of the CLI; a custom CLI offers only the own entry."""
        key = cli_presets.key_for(entry)
        if key == cli_presets.CUSTOM:
            return [], [], True
        efforts = cli_presets.effort_list(key)
        server = servers.server_of(entry)
        if server:
            result = servers.kept(server)
            return (list(result["models"]) if result else []), efforts, False
        return cli_presets.model_list(key), efforts, False

    def provider_form(self):
        """Fill the form with the provider page: name, ready line and the five sections."""
        tk = self.tk
        data = self.target()
        key = cli_presets.key_for(data)
        if key == cli_presets.CUSTOM and servers.access_of(data) != "subscription":   # a Custom CLI has the access type Subscription
            servers.clear_type_values(data)
            servers.set_access(data, "subscription")
        access = servers.access_of(data)
        if isinstance(data.get("limit"), str):
            data["limit"] = {"source": data["limit"]}   # the same meaning, the form of a table
        self.sections, self.notes, self.note_sources, self.frame_of = [], {}, {}, {}
        top = tk.LabelFrame(self.form, text="", padx=6, pady=4)   # same border and padding as the sections, so all fields share one column
        top.pack(fill="x", anchor="w")
        self.top = top
        self.field(top, top, ("enabled",), data.get("enabled", True) is not False, "enabled")
        self.field(top, top, ("name",), str(data.get("name") or ""), "name")
        item, row = self.line(top, "access")
        self.access_values = [tr(label) for _k, label in ACCESS]
        self.access_var = tk.StringVar(value=tr(dict(ACCESS)[access]))
        self.dialog.option_menu(row, self.access_var, self.access_values, self.mono, self.choose_access).pack(side="left")
        self.note(item, "access")
        self.ready_label = tk.Label(top, anchor="w", justify="left", fg=self.col["red"], font=(self.mono, 10, "bold"))
        self.ready_label.pack(fill="x", anchor="w", pady=(0, 6))
        self.cli_section(self.section("cli", N_("CLI")), key, data, access)
        self.access_section(self.section("source", dict(ACCESS)[access] if access != "subscription" else N_("Login")), data, access)
        self.models_section(self.section("models", N_("Models")), key, data)
        self.use_section(self.section("use", N_("Use")), data)
        self.provider_advanced(key, data)
        self.initial_values = {path: getter() for path, getter, _like in self.getters}
        self.refresh_login()
        self.refresh_state()

    def cli_section(self, box, key, data, access):
        """CLI: the choice of the CLIs offered for the access type (Custom CLI only for Subscription); protocol, command and the own-CLI values only for Custom CLI."""
        item, row = self.line(box, "cli")
        self.cli_var = self.tk.StringVar(value=cli_presets.label_of(key))
        self.cli_values = cli_presets.labels_for_access(access)
        self.dialog.option_menu(row, self.cli_var, self.cli_values, self.mono, self.choose_cli).pack(side="left")
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

    def access_section(self, box, data, access):
        """The fields of the chosen access type: Subscription the login (status, Login button, shared-login sentence), API key the masked key, Local LLM the
        address, the optional key, the context window and the Test connection button with the last result."""
        tk = self.tk
        self.login_status = self.login_button = None
        if access == "api":
            self.key_field(box, data, "providerKey")
        elif access == "llm":
            self.field(box, box, ("address",), str(data.get("address") or ""), "address")
            self.key_field(box, data, "apiKey")
            window = servers.window_of(data)
            self.field(box, box, ("contextWindow",), str(window) if window else "", "contextWindow")
            item, row = self.line(box, "test")
            self.test_button = tk.Button(row, text=tr("Test connection"), command=self.test_click)
            self.test_button.pack(side="left")
            self.note(item, "test")
            self.result_label = tk.Label(box, anchor="w", justify="left", font=(self.mono, 10))
            self.result_label.pack(fill="x", anchor="w", pady=4)
            self.show_result()
        else:
            item, row = self.line(box, "login")
            flow = tk.Frame(row)
            flow.pack(side="left", anchor="n")
            self.login_status = tk.Label(flow, anchor="w", font=(self.mono, 10, "bold"))
            self.login_button = tk.Button(flow, text=tr("Login"), width=12, command=self.login_click)
            self.login_status.pack(side="left", padx=(0, 8))
            self.login_button.pack(side="left")
            self.note(item, "login")
            tk.Label(item, text=tr(SHARED_LOGIN), anchor="w", justify="left", fg=self.col["dim"], font=(self.mono, 9)).pack(fill="x", anchor="w", padx=(4, 0))

    def key_field(self, box, data, texts):
        """The API key row: a masked entry (the stored key is never shown, the mask stands for it); the getter is registered under ("apiKey",)."""
        tk = self.tk
        item, row = self.line(box, texts)
        var = tk.StringVar(value=servers.mask_key(data.get("apiKey")))
        tk.Entry(row, textvariable=var, show="*", width=40, font=(self.mono, 10)).pack(side="left", fill="x", expand=True)
        self.rows[("apiKey",)] = row
        self.getters.append((("apiKey",), var.get, var.get()))
        self.frame_of[("apiKey",)] = box
        self.note(item, "apiKey", TEXT[texts][1])

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
                combo = gui_dialog.combobox(row, self.model_vars[cls], [""] + list(models), self.mono, wheel=self.wheel)
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

    def provider_advanced(self, key, data):
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
        """Set the shown provider to the chosen CLI (protocol, command, arguments, Bee mode); a CLI that is not offered for the access type (Custom CLI is
        offered only for Subscription) changes nothing; Custom CLI keeps the values; a provider whose name the user did not edit takes the new proposal."""
        if not self.collect():
            return
        data = self.target()
        key = cli_presets.key_of_label(label)
        if key not in cli_presets.keys_for_access(servers.access_of(data)):
            self.reshow()
            return
        if key == cli_presets.CUSTOM:
            data.setdefault("outputFormat", "")
        else:
            cli_presets.apply(data, key)
        self.follow_name(data)
        self.reshow()

    def stash_of(self, entry) -> dict:
        """The values of the access types that are not shown now, for this provider entry; kept only while the window is open (never written)."""
        for held, values in self.type_stash:
            if held is entry:
                return values
        values = {}
        self.type_stash.append((entry, values))
        return values

    def choose_access(self, label):
        """Switch the access type of the shown provider at once. CLI, Use and Advanced stay; the values of the old type (key, address, context window, models)
        are kept in memory until the window is closed, so switching back shows them again; the entry holds only the chosen type. When the CLI is not offered
        for the new type, the first CLI offered for it is chosen. The usage check is set to none for a new provider with API key or Local LLM."""
        if not self.collect():
            return
        data = self.target()
        new = next((k for k, text in ACCESS if tr(text) == label), None)
        old = servers.access_of(data)
        if new is None or new == old:
            return
        stash = self.stash_of(data)
        stash[old] = {"values": {k: copy.deepcopy(data[k]) for k in TYPE_FIELDS if k in data}, "models": self.models_of(data)}
        servers.clear_type_values(data)
        servers.set_access(data, new, new=self.is_fresh(data))
        kept = stash.get(new)
        if kept is None and old != "llm" and new != "llm":
            kept = {"values": {}, "models": copy.deepcopy(stash[old]["models"])}   # Subscription and API key share the model list of the CLI
        data.update(copy.deepcopy((kept or {}).get("values") or {}))
        self.put_models(data, (kept or {}).get("models") or {})
        offered = cli_presets.keys_for_access(new)
        if cli_presets.key_for(data) not in offered:
            cli_presets.apply(data, offered[0])
            self.put_models(data, {})
        self.follow_name(data)
        self.reshow()

    @staticmethod
    def models_of(data) -> dict:
        """{class: {model, customModel}} of the models a provider entry names."""
        out = {}
        for cls, entry in (data.get("classes") or {}).items():
            if isinstance(entry, dict):
                out[cls] = {k: entry[k] for k in ("model", "customModel") if k in entry}
        return out

    @staticmethod
    def put_models(data, models) -> None:
        """Set the models of the classes to `models` ({class: {model, customModel}}); a class without an entry there has no model."""
        classes = data.setdefault("classes", {})
        for cls in CLASSES:
            entry = classes.get(cls)
            if isinstance(entry, dict):
                entry.pop("model", None)
                entry.pop("customModel", None)
        for cls, values in models.items():
            if values:
                classes.setdefault(cls, {}).update(copy.deepcopy(values))

    def add_provider(self):
        """Append a new provider (Subscription, Claude Code, name proposed from CLI and access type) and show it."""
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

    # ------------------------------------------------------------ the test of a Local LLM

    def server_entry(self) -> dict:
        """The shown Local LLM as the tests use it: name (the provider name), address without /v1, the stored API key and the context window."""
        return servers.server_of({**self.target(), "access": "llm"})

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
            self.result_label.config(text=tr("the address of the Local LLM is missing"))
            return
        self.result_label.config(text=tr("Testing ..."))
        outcome = {}

        def work():
            """Run the test (it keeps its result in the data folder)."""
            try:
                servers.run_test(server)
            except Exception as exc:   # shown on the page, never lost in the thread
                outcome["error"] = str(exc)
        thread = threading.Thread(target=servers.in_thread(work), daemon=True)
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
                    self.refresh_state()   # the red ready line follows the new result at once
            except self.tk.TclError:
                pass
        self.win.after(100, poll)


def cli_presets_protocols() -> tuple:
    """The protocols a custom CLI can name."""
    return tuple(p["protocol"] for p in cli_presets.PRESETS.values())

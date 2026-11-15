# Getting started

**Requirements**

- Python 3.11 or newer and a web browser
- at least one of the supported CLIs: [Claude Code](https://claude.com/claude-code) (`claude`), the Codex CLI (`codex`), [OpenCode](https://opencode.ai) (`opencode`) or [Kilo Code](https://kilo.ai) (`kilo`)
- Git (Plans work in worktrees)

Follow the [README installation and first-start steps](../README.md#installation-and-first-start) for virtual-environment commands on Windows and Linux. Keep that checkout in place for future updates.

Each Project’s `.haivequeen/` folder contains machine-local settings and runtime files; keep it local to each machine and do not commit it. `bzz init` and `bzz register` add it to the project folder’s `.gitignore` and warn if it is already tracked.


To install a particular release instead of the newest code, check out its tag after cloning (use `git checkout <release-tag>` with an existing release tag) before `pip install -e .`. Keep the clone where it is: the [update process](OPERATIONS.md#updates) installs new versions into that folder.



## Log in and run

Use **AI providers → Add provider** in the browser, choose your access type, and follow [provider setup](PROVIDERS.md). Subscription logins live in the [HaiveQueen profile](PROFILE.md); API and local endpoints use their own connection settings.

Open the setup browser with `bzz gui` before starting plan execution. Use `bzz start` after a provider is ready and a plan is submitted.

The browser guides you through initial web-account setup. That account protects the interface; it is separate from provider authentication. See [web access, tray and Linux service](WEB-API.md).

Machine configuration is `%APPDATA%\HaiveQueen\config.toml` on Windows and `${XDG_CONFIG_HOME:-~/.config}/HaiveQueen/config.toml` on Linux. Data is stored separately under `%LOCALAPPDATA%\HaiveQueen` or `${XDG_DATA_HOME:-~/.local/share}/HaiveQueen`. `BZZ_CONFIG_DIR` and `BZZ_DATA_DIR` override those locations.

Submit work using [a SENT plan](PLANS.md).

## Troubleshooting the first start

- Check `python --version` (or `python3 --version` on Linux): the selected interpreter must be at least 3.11. Install into the virtual environment used to run bzz.
- If PowerShell blocks activation, call `.venv\Scripts\python.exe` and `.venv\Scripts\bzz.exe` directly; activation is only a shell convenience. On Linux the equivalents are `.venv/bin/python` and `.venv/bin/bzz`.
- If bzz or a provider CLI cannot be found, check the current shell PATH and reopen the shell after installing it. The provider executable must also be visible to the background process.
- A provider marked Login missing needs authentication in the HaiveQueen profile, even if its CLI is already logged in through your personal profile. Use its Login button and finish the returned browser/device flow.
- The first web account protects browser access; it does not log a coding provider in. Configure both separately. For account reset, listener conflicts or TLS setup, see the [web guide](WEB-API.md).
- A start refusal during update, graceful stopping or another startup is deliberate. Read the event and `runs/orchestrator.log`; wait for the existing operation to finish rather than launching a second loop.

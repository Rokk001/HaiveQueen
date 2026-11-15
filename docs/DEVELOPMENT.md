# Development

Python 3.11 or newer is required. Runtime dependencies are declared in [pyproject.toml](../pyproject.toml); `tomlkit` preserves TOML comments during settings edits. The backend uses SQLite and Python HTTP services; the web interface uses JavaScript and CSS without a frontend package build.

Install with `pip install -e .`. Regression tests use `unittest`; run the focused tests for the files you change, for example:

```bash
python -m unittest discover -s tests-py -p test_end_user_docs.py -v
```

CI runs the full suite on Windows and Linux with Python 3.11 and 3.14, plus the Linux tmux smoke test. Display-dependent historical tests skip when no display is available. The Orchestrator runs the configured project compile check after accepted ready; package agents do not run the whole suite or full compile check.

Role templates live in [roles](../roles). See [Agent rules](AGENT-RULES.md) for startup context sources and [runtime API](RUNTIME-API.md) for the database protocol. Historical contracts are labelled as archives in the [documentation map](DOCUMENTATION-MAP.md).

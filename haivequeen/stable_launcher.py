"""Fixed launcher: this file is copied once and is independent of any version."""
import os
from pathlib import Path
import re
import sys
import subprocess


def main():
    root = Path(__file__).resolve().parent
    version = (root / "active-version").read_text(encoding="utf-8").strip()
    if not re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9._-]*", version) or version in (".", ".."):
        raise ValueError("invalid active version")
    cli = root / "versions" / version / "haivequeen" / "cli.py"
    if not cli.is_file():
        raise FileNotFoundError("active version is not installed")
    argv = [sys.executable, "-I", str(cli), *sys.argv[1:]]
    if os.name == "nt":
        return subprocess.call(argv)
    os.execv(sys.executable, argv)


if __name__ == "__main__":
    sys.exit(main())

"""Fixed Queen and Bee instructions live in the roles/ folder of the repository, not in code.

A role file is plain text. {{name}} marks a value that comes from the settings or the plan;
every start reads the file again, so a changed file takes effect at the next start.
"""
import re
from pathlib import Path

ROLES_DIR = Path(__file__).resolve().parent.parent / "roles"


def role_text(name, values=None, roles_dir=None):
    """Return the text of roles/<name>.md with each {{key}} replaced by its value; an unknown key stays as written."""
    text = (Path(roles_dir) if roles_dir else ROLES_DIR).joinpath(name + ".md").read_text(encoding="utf-8").replace("\r\n", "\n")
    text = text.rstrip("\n")
    vals = {k: str(v) for k, v in (values or {}).items()}
    return re.sub(r"\{\{(\w+)\}\}", lambda m: vals.get(m.group(1), m.group(0)), text)

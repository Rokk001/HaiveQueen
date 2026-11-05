"""Version selection for integration releases."""
import re


def next_version(current: str, requested: str = "") -> str:
    """Increment the patch or accept a strictly newer three-part version."""
    def parse(value):
        if not re.fullmatch(r"(0|[1-9]\d*)\.(0|[1-9]\d*)\.(0|[1-9]\d*)", value):
            raise ValueError("version must be three non-negative integers")
        return tuple(map(int, value.split(".")))

    old = parse(current)
    if requested:
        new = parse(requested)
        if new <= old:
            raise ValueError("next version must be newer than the current version")
    else:
        new = (old[0], old[1], old[2] + 1)
    return ".".join(map(str, new))

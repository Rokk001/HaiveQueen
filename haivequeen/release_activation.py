"""Activation primitive for an already checked compatible release."""
from pathlib import Path

from . import version_layout, version_leases
from .stable_launcher import lifetime_lock


def activate(root, version, first_round, after_rollback=None, installation_token=None):
    """Switch the pointer and restore it if the successor fails its first round.

    The caller must establish compatibility before calling and ensure that
    first_round checks the successor, not an earlier core. The old version is
    reserved until the result is known so collection cannot remove rollback.
    Exceptions from first_round trigger rollback and are propagated.
    """
    root = Path(root)
    with lifetime_lock(root):
        old = (root / 'active-version').read_text(encoding='utf-8').strip()
        token = version_leases._reserve_locked(root, old)
        try:
            version_layout._set_active_locked(root, version, installation_token=installation_token)
        except BaseException:
            leases = version_leases._load(root)
            leases.pop(token, None)
            version_leases._save(root, leases)
            raise
    try:
        try:
            passed = first_round()
        except BaseException:
            with lifetime_lock(root):
                if (root / 'active-version').read_text(encoding='utf-8').strip() == version:
                    version_layout._set_active_locked(root, old)
            if after_rollback is not None:
                after_rollback(old)
            raise
        if not passed:
            with lifetime_lock(root):
                if (root / 'active-version').read_text(encoding='utf-8').strip() != version:
                    raise RuntimeError('active version changed during first round')
                version_layout._set_active_locked(root, old)
            if after_rollback is not None:
                after_rollback(old)
        return bool(passed)
    finally:
        version_leases.release(root, token)

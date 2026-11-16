"""Activation primitive for an already checked compatible release."""
import os
from contextlib import nullcontext
from pathlib import Path

from . import version_layout, version_leases
from .stable_launcher import lifetime_lock


def activate(root, version, first_round, after_rollback=None, installation_token=None,
             target_verifier=None, publication_lock=nullcontext):
    """Switch the pointer and restore it if the successor fails its first round.

    The caller must establish compatibility before calling and ensure that
    first_round checks the successor, not an earlier core. target_verifier must
    independently verify the candidate immediately before publication under the
    lifetime lock; missing, false, or failing verification prevents activation.
    The old version is
    reserved until the result is known so collection cannot remove rollback.
    Exceptions from first_round trigger rollback and are propagated.
    """
    root = Path(root)
    with publication_lock(), lifetime_lock(root):
        try:
            if target_verifier is None or target_verifier() is not True:
                raise RuntimeError('target verification failed')
        except BaseException:
            # The verifier may reject or raise. Do not guess ownership when a
            # process identity cannot be established; leave recovery to retry.
            if isinstance(installation_token, str):
                leases = version_leases._load(root)
                record = leases.get(installation_token, {})
                try:
                    identity = version_leases.platform.process_identity(os.getpid())
                except Exception:
                    identity = None
                if (record.get('installation') is True and record.get('version') == version
                        and record.get('installer_pid') == os.getpid()
                        and identity and record.get('installer_identity') == identity):
                    leases.pop(installation_token)
                    version_leases._save(root, leases)
            raise
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

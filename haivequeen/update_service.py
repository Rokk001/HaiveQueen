"""Isolated update coordination; application adapters provide durable scheduling and drain.

The drain adapter must atomically prevent starts and report all running agents,
including agents in every Apiary. It must retain that barrier until released.
"""
from datetime import timedelta
import threading


class UpdateService:
    def __init__(self, check, drain, install, restart, interval=timedelta(hours=24)):
        if interval <= timedelta(0):
            raise ValueError("check interval must be positive")
        self.check = check
        self.drain = drain
        self.install = install
        self.restart = restart
        self.interval = interval
        self.last_attempt = None
        self.available = None
        self._lock = threading.Lock()

    def check_now(self, now):
        """Check immediately; record attempts even on error to avoid retry storms."""
        with self._lock:
            self.last_attempt = now
            self.available = None
            self.available = self.check()
            return self.available

    def check_due(self, now):
        """Check at most once per interval, unless explicitly checked manually."""
        with self._lock:
            if self.last_attempt is not None and now - self.last_attempt < self.interval:
                return False
            self.last_attempt = now
            self.available = None
            self.available = self.check()
            return True

    def install_confirmed(self, release):
        """Install the currently offered release after an exclusive, agent-free drain.

        begin() establishes the start barrier; wait() returns only after all agents
        have stopped. end() releases it even when an operation fails. The adapter
        must coordinate across processes; this object's lock only protects callers
        in this process. Restart is attempted only after successful installation.
        """
        with self._lock:
            if release is None or release != self.available:
                raise ValueError("release is not the currently available update")
            self.drain.begin()
            try:
                self.drain.wait()
                self.install(release)
                self.restart()
                self.available = None
            finally:
                self.drain.end()

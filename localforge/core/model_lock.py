"""One model job at a time (a chat or an agent task): this CPU cannot run two usefully."""

from __future__ import annotations

import time


class ModelLock:
    """A plain holder is enough because everything runs on one event loop.

    The lock remembers when its holder last showed activity, so a holder that never ran
    its cleanup cannot block the model for longer than `stale_after_s`.
    """

    def __init__(self, stale_after_s: float):
        self.stale_after_s = stale_after_s
        self.holder: str | None = None
        self._last_activity = 0.0

    @property
    def busy(self) -> bool:
        return self.holder is not None and time.monotonic() - self._last_activity < self.stale_after_s

    def try_acquire(self, holder: str) -> bool:
        if self.busy:
            return False
        self.holder = holder
        self._last_activity = time.monotonic()
        return True

    def touch(self) -> None:
        """Called by long holders (agent tasks) before each model call."""
        self._last_activity = time.monotonic()

    def release(self, holder: str) -> None:
        if self.holder == holder:
            self.holder = None

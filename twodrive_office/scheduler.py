"""Bounded debounce and retry state, independent of editor UI and threads."""
from dataclasses import dataclass


@dataclass
class PendingSave:
    debounce: float = 1.0
    max_wait: float = 5.0
    revision: int = 0
    saved_revision: int = 0
    first_change: float | None = None
    due_at: float | None = None
    retry_at: float = 0
    failures: int = 0

    def changed(self, now: float):
        self.revision += 1
        if self.first_change is None:
            self.first_change = now
        self.due_at = min(now + self.debounce, self.first_change + self.max_wait)

    def due(self, now: float):
        return (self.revision > self.saved_revision and self.due_at is not None
                and now >= max(self.due_at, self.retry_at))

    def succeeded(self, revision: int, now: float):
        self.saved_revision = max(self.saved_revision, revision)
        self.failures = 0
        self.retry_at = 0
        if self.revision == self.saved_revision:
            self.first_change = self.due_at = None
        else:
            self.first_change = now
            self.due_at = now + self.debounce

    def failed(self, now: float):
        self.failures += 1
        self.retry_at = now + min(30, 2 ** min(self.failures, 5))

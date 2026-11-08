"""Durable local revisions, with bounded publication to the TwoDrive mount.

This is user data, not a disposable cache. Keep failed publications across
editor/bridge exits. flock also excludes another editor saving the same target.
"""
from __future__ import annotations

import fcntl
import hashlib
import json
import os
import tempfile
import threading
import time
from pathlib import Path

from .policy import SaveError, SavePlan, Settings, atomic_write, fingerprint, prepare_target


def state_directory():
    base = Path(os.environ.get("XDG_STATE_HOME", str(Path.home() / ".local/state")))
    return base / "twodrive-office" / "pending"


def durable_replace(path: Path, content: bytes):
    fd, name = tempfile.mkstemp(dir=path.parent, prefix=".local-")
    try:
        with os.fdopen(fd, "wb") as stream:
            stream.write(content)
            stream.flush()
            os.fsync(stream.fileno())
        os.replace(name, path)
        fd = os.open(path.parent, os.O_RDONLY | os.O_DIRECTORY)
        try:
            os.fsync(fd)
        finally:
            os.close(fd)
    finally:
        if os.path.exists(name):
            os.unlink(name)


class LocalRevision:
    def __init__(self, settings, plan, expected, root, clock=time.monotonic):
        self.settings, self.plan, self.expected = settings, plan, expected
        self.clock, self.lock = clock, threading.RLock()
        self.directory = root / hashlib.sha256(str(plan.target).encode()).hexdigest()
        self.directory.mkdir(parents=True, exist_ok=True, mode=0o700)
        self.guard = (self.directory / "lock").open("a+b")
        os.chmod(self.directory / "lock", 0o600)
        try:
            fcntl.flock(self.guard, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError as exc:
            self.guard.close()
            raise SaveError("另一编辑器正在保存此目标") from exc
        self.manifest = self.directory / "pending.json"
        self.blob = None
        self.closed = False
        self.error = ""
        self.due_at = clock() + settings.sync_interval_seconds
        self.retry_at = 0
        self.failures = 0
        self.writer = None
        self.recovered = False
        self.auto_publish = True

    def validate(self):
        prepare_target(self.settings, self.plan)
        if fingerprint(self.plan.target) != self.expected:
            raise SaveError("目标文件被其他程序修改；本地待同步版本已保留")

    def stage(self, content):
        with self.lock:
            self.validate()
            old = self.blob
            blob = self.directory / (hashlib.sha256(content).hexdigest() + ".document")
            if not blob.exists():
                durable_replace(blob, content)
            data = {"target": str(self.plan.target), "in_place": self.plan.in_place,
                    "extension": self.plan.extension, "expected": self.expected,
                    "blob": blob.name, "mount_root": str(self.settings.mount_root)}
            durable_replace(self.manifest, json.dumps(data, ensure_ascii=False).encode())
            self.blob = blob
            if old is None:
                self.due_at = self.clock() + self.settings.sync_interval_seconds
            if old and old != blob:
                old.unlink(missing_ok=True)

    def publish(self, force=False):
        with self.lock:
            if not self.blob:
                return
            if not force and not self.auto_publish:
                return
            now = self.clock()
            if not force and now < max(self.due_at, self.retry_at):
                return
            try:
                self.validate()
                if self.writer:
                    self.expected = self.writer()
                else:
                    self.expected = atomic_write(self.settings, self.plan, self.blob.read_bytes(), self.expected)
                # Removing the manifest first makes an interruption harmless:
                # no acknowledged local revision is discarded before publication.
                self.manifest.unlink()
                self.blob.unlink(missing_ok=True)
                self.blob = None
                self.error, self.failures, self.retry_at = "", 0, 0
                self.due_at = self.clock() + self.settings.sync_interval_seconds
            except (SaveError, OSError) as exc:
                self.error = str(exc)
                self.failures += 1
                self.retry_at = now + min(60, 2 ** min(self.failures, 6))
                if force:
                    raise

    def release(self):
        self.guard.close()

    def __del__(self):
        if hasattr(self, "guard"):
            self.guard.close()

    def native_saved(self):
        """An editor's successful explicit Save supersedes its staged revision."""
        with self.lock:
            self.manifest.unlink(missing_ok=True)
            if self.blob:
                self.blob.unlink(missing_ok=True)
            self.blob = None
            self.expected = fingerprint(self.plan.target)
            self.due_at = self.clock() + self.settings.sync_interval_seconds
            self.error, self.failures, self.retry_at = "", 0, 0


class LocalSaveQueue:
    def __init__(self, settings: Settings, root=None, clock=time.monotonic):
        self.settings, self.clock = settings, clock
        self.root = Path(root) if root else state_directory()
        self.root.mkdir(parents=True, exist_ok=True, mode=0o700)
        self.records = []
        self.unavailable = set()
        self.lock = threading.RLock()
        self.recover()

    def recover(self):
        for manifest in self.root.glob("*/pending.json"):
            if any(record.manifest == manifest for record in self.records):
                continue
            record = None
            try:
                data = json.loads(manifest.read_text())
                if data["mount_root"] != str(self.settings.mount_root):
                    continue
                plan = SavePlan(Path(data["target"]), data["in_place"], data["extension"])
                expected = tuple(data["expected"]) if data["expected"] is not None else None
                record = LocalRevision(self.settings, plan, expected, self.root, self.clock)
                if record.directory != manifest.parent:
                    raise ValueError("mismatched revision identity")
                name = data["blob"]
                if len(name) != 73 or not name.endswith(".document") or any(c not in "0123456789abcdef" for c in name[:64]):
                    raise ValueError("invalid local revision")
                record.blob = record.directory / name
                if hashlib.sha256(record.blob.read_bytes()).hexdigest() != name[:64]:
                    raise ValueError("damaged local revision")
                # An editor may already have loaded the old mount contents.
                # Do not publish a crash-recovered revision behind that model.
                # Recovery is permitted only after all editors have exited.
                record.closed, record.recovered, record.due_at = True, True, float("inf")
                self.records.append(record)
                self.unavailable.discard(manifest)
            except (KeyError, TypeError, ValueError, OSError, SaveError):
                if record:
                    record.release()
                self.unavailable.add(manifest)
                # Preserve unrecognized/damaged data for explicit recovery.

    def open(self, plan, expected):
        with self.lock:
            if any(r.plan.target == plan.target for r in self.records):
                raise SaveError("此目标仍有本地待同步版本，请先处理待同步状态")
            record = LocalRevision(self.settings, plan, expected, self.root, self.clock)
            if record.manifest.exists():
                record.release()
                raise SaveError("此目标有未完成的本地版本，请先处理待同步状态")
            self.records.append(record)
            return record

    def close(self, record):
        with record.lock:
            record.closed = True
            try:
                record.publish(force=True)
            except (SaveError, OSError):
                pass  # Durable revision remains queued, including after restart.
        self.tick()

    def tick(self):
        with self.lock:
            for record in list(self.records):
                record.publish()
                if record.closed and not record.blob:
                    record.release()
                    self.records.remove(record)

    def pending(self):
        with self.lock:
            return any(record.blob for record in self.records) or any(path.exists() for path in self.unavailable)

    def allow_recovery(self):
        """Caller must establish that no editor has an old model open."""
        with self.lock:
            self.recover()
            for record in self.records:
                if record.recovered:
                    record.recovered = False
                    record.due_at = self.clock()

    def shutdown(self):
        with self.lock:
            for record in self.records:
                record.release()
            self.records.clear()

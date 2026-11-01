import os
import tempfile
import unittest
from dataclasses import replace
from pathlib import Path
from unittest.mock import patch

from twodrive_office.local_save import LocalSaveQueue
from twodrive_office.policy import Settings, SaveError, fingerprint, plan_save


class LocalSaveTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        root = Path(self.temp.name)
        self.mount = root / "mount"
        self.mount.mkdir()
        self.target = self.mount / "report.docx"
        self.target.write_bytes(b"original")
        self.settings = Settings(self.mount, self.mount / "Copies", require_mount=False)
        from twodrive_office.policy import SavePlan
        self.plan = SavePlan(self.target, True, "docx")
        self.now = 0
        self.root = root / "state"
        self.queue = self.make_queue()
        self.record = self.queue.open(self.plan, fingerprint(self.target))

    def make_queue(self):
        queue = LocalSaveQueue(self.settings, self.root, lambda: self.now)
        self.addCleanup(queue.shutdown)
        return queue

    def test_continuous_local_edits_publish_only_latest_at_bounded_interval(self):
        before = fingerprint(self.target)
        for second in range(180):
            self.now = second
            self.record.stage(str(second).encode())
            self.queue.tick()
        self.assertEqual(fingerprint(self.target), before)
        self.assertEqual(self.target.read_bytes(), b"original")
        self.assertEqual(self.record.blob.read_bytes(), b"179")
        self.assertEqual(len(list(self.record.directory.glob("*.document"))), 1)
        self.now = 180
        self.queue.tick()
        self.assertEqual(self.target.read_bytes(), b"179")
        self.assertFalse(self.queue.pending())
        self.record.stage(b"next")
        self.queue.close(self.record)
        self.assertEqual(self.target.read_bytes(), b"next")

    def test_unchanged_open_close_never_writes_target(self):
        before = fingerprint(self.target)
        self.now = 1000
        self.queue.tick()
        self.queue.close(self.record)
        self.assertEqual(fingerprint(self.target), before)

    def test_failed_close_retains_local_revision_and_restart_recovers_it(self):
        self.record.stage(b"latest")
        with patch("twodrive_office.local_save.atomic_write", side_effect=OSError("offline")):
            self.queue.close(self.record)
        self.assertEqual(self.target.read_bytes(), b"original")
        blob = self.record.blob
        self.assertEqual(blob.read_bytes(), b"latest")
        self.queue.shutdown()
        queue = self.make_queue()
        self.assertTrue(queue.pending())
        queue.tick()
        self.assertEqual(self.target.read_bytes(), b"original", 'Crash recovery must not replace an already open old model')
        queue.allow_recovery()
        queue.tick()
        self.assertEqual(self.target.read_bytes(), b"latest")
        self.assertFalse(blob.exists())

    def test_mount_disconnect_and_external_edits_never_discard_or_overwrite(self):
        self.record.stage(b"pending")
        self.record.settings = replace(self.settings, require_mount=True)
        with patch("os.path.ismount", return_value=False), self.assertRaises(SaveError):
            self.record.publish(force=True)
        self.assertEqual(self.record.blob.read_bytes(), b"pending")
        self.record.settings = self.settings
        self.target.write_bytes(b"other editor")
        self.queue.close(self.record)
        self.assertEqual(self.target.read_bytes(), b"other editor")
        self.assertEqual(self.record.blob.read_bytes(), b"pending")
        self.queue.shutdown()
        queue = self.make_queue()
        queue.allow_recovery()
        queue.tick()
        self.assertTrue(queue.pending())
        self.assertEqual(self.target.read_bytes(), b"other editor")
        with self.assertRaises(SaveError):
            queue.open(self.plan, fingerprint(self.target))

    def test_second_editor_cannot_take_over_target_and_native_save_supersedes_pending(self):
        second = self.make_queue()
        with self.assertRaises(SaveError):
            second.open(self.plan, fingerprint(self.target))
        self.record.stage(b"staged")
        self.target.write_bytes(b"explicit Ctrl+S")
        self.record.native_saved()
        self.now = 1000
        self.queue.tick()
        self.assertEqual(self.target.read_bytes(), b"explicit Ctrl+S")
        self.assertFalse(self.queue.pending())

    def test_locked_or_damaged_recovery_never_reports_no_pending_work(self):
        self.record.stage(b"retained")
        second = self.make_queue()
        self.assertTrue(second.pending())
        self.queue.shutdown()
        self.record.manifest.write_text('{"incomplete":true}')
        third = self.make_queue()
        self.assertTrue(third.pending())
        self.assertEqual(self.record.blob.read_bytes(), b"retained")

    def test_failed_manifest_commit_preserves_previous_revision(self):
        self.record.stage(b"previous")
        with patch("twodrive_office.local_save.durable_replace", side_effect=OSError("disk full")), self.assertRaises(OSError):
            self.record.stage(b"new")
        self.queue.shutdown()
        queue = self.make_queue()
        queue.allow_recovery()
        queue.tick()
        self.assertEqual(self.target.read_bytes(), b"previous")

    def test_new_edits_revalidate_replaced_directory(self):
        self.record.stage(b"retained")
        original = self.mount / "old.docx"
        self.target.rename(original)
        self.target.symlink_to(original)
        with self.assertRaises(SaveError):
            self.record.stage(b"must not save")
        self.assertEqual(self.record.blob.read_bytes(), b"retained")

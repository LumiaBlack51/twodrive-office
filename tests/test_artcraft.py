import io
import json
import tempfile
import unittest
import zipfile
from pathlib import Path
from unittest.mock import patch

from twodrive_office.artcraft import WorkingSession, private_environment, recover_session, verify_namespace
from twodrive_office.local_save import LocalSaveQueue
from twodrive_office.policy import Settings, SaveError, fingerprint
from scripts.install_artcraft import install


def document(extension="docx", text="original"):
    main = {"docx": "word/document.xml", "xlsx": "xl/workbook.xml", "pptx": "ppt/presentation.xml"}[extension]
    out = io.BytesIO()
    with zipfile.ZipFile(out, "w") as archive:
        archive.writestr("[Content_Types].xml", "<Types/>")
        archive.writestr(main, f"<document>{text}</document>")
    return out.getvalue()


class ArtcraftTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.mount = self.root / "mount"
        self.mount.mkdir()
        self.source = self.mount / "report.docx"
        self.original = document()
        self.source.write_bytes(self.original)
        self.settings = Settings(self.mount, self.mount / "Copies", require_mount=False)
        self.now = 0

    def session(self, source=None, editor="wordcraft", **kwargs):
        s = WorkingSession(self.settings, source or self.source, editor, root=self.root / "sessions",
                           queue_root=self.root / "pending", clock=lambda: self.now, **kwargs)
        self.addCleanup(s.queue.shutdown)
        return s

    def test_open_close_does_not_rewrite_source(self):
        before = fingerprint(self.source)
        s = self.session()
        self.assertTrue(s.finish(clean_exit=True))
        self.assertEqual(fingerprint(self.source), before)
        self.assertFalse(s.directory.exists())

    def test_native_save_is_local_then_bounded_publish(self):
        s = self.session()
        edited = document(text="edited")
        s.working.write_bytes(edited)
        s.tick()
        self.assertEqual(self.source.read_bytes(), self.original)
        self.assertEqual(s.record.blob.read_bytes(), edited)
        self.now = 180
        s.tick()
        self.assertEqual(self.source.read_bytes(), edited)
        self.assertFalse(s.record.blob)
        self.assertTrue(s.finish(clean_exit=True))

    def test_offline_revision_is_kept_in_working_file(self):
        s = self.session()
        edited = document(text="offline")
        s.working.write_bytes(edited)
        with patch.object(Settings, "check_mount", side_effect=SaveError("offline")):
            s.tick()
            self.assertIn("offline", s.error)
            self.assertFalse(s.finish(clean_exit=True))
        self.assertEqual(s.working.read_bytes(), edited)
        self.assertEqual(self.source.read_bytes(), self.original)

    def test_external_conflict_keeps_both_versions(self):
        s = self.session()
        s.working.write_bytes(document(text="local"))
        s.tick()
        external = document(text="external")
        self.source.write_bytes(external)
        self.assertFalse(s.finish(clean_exit=True))
        self.assertEqual(self.source.read_bytes(), external)
        self.assertTrue(s.record.manifest.exists())
        self.assertTrue(s.working.exists())

    def test_outside_file_needs_explicit_copy_and_never_overwrites_original(self):
        outside = self.root / "outside.docx"
        outside.write_bytes(self.original)
        with self.assertRaises(SaveError):
            self.session(outside)
        s = self.session(outside, copy_directory=self.settings.copy_directory)
        edited = document(text="copy")
        s.working.write_bytes(edited)
        s.finish(clean_exit=True)
        self.assertEqual(outside.read_bytes(), self.original)
        self.assertEqual(s.plan.target.read_bytes(), edited)

    def test_corrupt_or_symlink_native_export_never_publishes(self):
        s = self.session()
        s.working.write_bytes(b"partial zip")
        s.tick()
        self.assertTrue(s.error)
        self.assertEqual(self.source.read_bytes(), self.original)
        s.working.unlink()
        s.working.symlink_to(self.source)
        self.assertFalse(s.finish(clean_exit=True))

    def test_target_symlink_change_refused_on_next_revision(self):
        s = self.session()
        external = self.root / "external.docx"
        external.write_bytes(self.original)
        self.source.unlink()
        self.source.symlink_to(external)
        s.working.write_bytes(document(text="unsafe"))
        self.assertFalse(s.finish(clean_exit=True))
        self.assertEqual(external.read_bytes(), self.original)

    def test_crash_and_pending_revision_block_reopen(self):
        s = self.session()
        s.working.write_bytes(document(text="pending"))
        with patch.object(Settings, "check_mount", side_effect=SaveError("offline")):
            self.assertFalse(s.finish(clean_exit=False))
        # Crash before stage is recoverable from the retained native file.
        self.assertTrue(s.working.exists())
        self.assertTrue((s.directory / "session.json").exists())

    def test_profile_and_control_environment_are_private(self):
        s = self.session()
        with patch.dict("os.environ", {"GRIDCRAFT_NO_PREFS": "1", "DECKCRAFT_CONTROL_PORT": "1234",
                                       "XDG_RUNTIME_DIR": "/run/user/1000", "WAYLAND_DISPLAY": "wayland-0"}):
            env = private_environment(s.directory, "gridcraft")
        self.assertNotIn("GRIDCRAFT_NO_PREFS", env)
        self.assertNotIn("DECKCRAFT_CONTROL_PORT", env)
        self.assertEqual(env["WAYLAND_DISPLAY"], "/run/user/1000/wayland-0")
        self.assertTrue(Path(env["XDG_CONFIG_HOME"]).is_relative_to(s.directory))
        self.assertTrue(json.loads((s.directory / "config/gridcraft/ui.json").read_text())["autosave"])

    def test_network_isolation_is_mandatory(self):
        with patch("os.readlink", return_value="net:[same]"):
            with self.assertRaises(SaveError):
                verify_namespace()

    def test_deck_save_only_for_owned_document_without_dialog(self):
        source = self.mount / "slides.pptx"
        source.write_bytes(document("pptx"))
        s = self.session(source, "deckcraft")
        class Control:
            state = {"documents": [{"path": str(s.working), "dirty": True}], "dialog": None}
            saves = 0
            def call(self, method): return self.state
            def execute(self, command, params): self.saves += 1
        c = Control()
        for state in ({"documents": [], "dialog": None},
                      {"documents": [{"path": "/other.pptx", "dirty": True}], "dialog": None},
                      {"documents": [{"path": str(s.working), "dirty": True}], "dialog": "save"}):
            c.state = state
            self.now += 4
            s.deck_tick(c)
        self.assertEqual(c.saves, 0)
        c.state = {"documents": [{"path": str(s.working), "dirty": True}], "dialog": None}
        self.now += 4
        s.deck_tick(c)
        self.assertEqual(c.saves, 1)
        s.deck_save = False
        self.now += 4
        s.deck_tick(c)
        self.assertEqual(c.saves, 1)

    def test_explicit_recovery_publishes_retained_native_save(self):
        s = self.session()
        edited = document(text="recover me")
        s.working.write_bytes(edited)
        with patch.object(Settings, "check_mount", side_effect=SaveError("offline")):
            s.finish(clean_exit=False)
        with patch("twodrive_office.artcraft.artcraft_running", return_value=False), patch("twodrive_office.artcraft.LocalSaveQueue", side_effect=lambda settings: LocalSaveQueue(settings, self.root / "pending")):
            recover_session(s.directory, self.settings)
        self.assertEqual(self.source.read_bytes(), edited)
        self.assertFalse(s.directory.exists())

    def test_install_adds_separate_launchers_without_starting_editors(self):
        home = self.root / "home with spaces"
        executable = self.root / "wordcraft"
        executable.write_text("fixture executable, must never run")
        with patch("subprocess.Popen", side_effect=AssertionError("installer must not start editors")):
            launchers = install(home=home, data_home=home / "data", executables={"wordcraft": executable})
        self.assertEqual(len(launchers), 1)
        manager = home / ".local/bin/twodrive-artcraft"
        text = manager.read_text()
        self.assertIn("--unshare-net", text)
        self.assertIn("TWODRIVE_ARTCRAFT_HOST_NETNS", text)
        self.assertIn("-B -m", text)
        self.assertTrue((home / "data/applications/org.twodrive.wordcraft.desktop").exists())
        self.assertFalse((home / ".config/wordcraft").exists())

    def test_pending_conflict_blocks_new_session_and_recovery(self):
        s = self.session()
        edited = document(text="pending")
        s.working.write_bytes(edited)
        s.tick()
        s.queue.shutdown()
        with self.assertRaises(SaveError):
            self.session()
        self.source.write_bytes(document(text="external"))
        with patch("twodrive_office.artcraft.artcraft_running", return_value=False), patch("twodrive_office.artcraft.LocalSaveQueue", side_effect=lambda settings: LocalSaveQueue(settings, self.root / "pending")):
            with self.assertRaises(SaveError):
                recover_session(s.directory, self.settings)
        self.assertEqual(s.working.read_bytes(), edited)
        self.assertTrue(s.record.manifest.exists())


if __name__ == "__main__":
    unittest.main()

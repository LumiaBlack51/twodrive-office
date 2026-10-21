import io
import json
import os
import tempfile
import threading
import unittest
import urllib.error
import urllib.request
import zipfile
from dataclasses import replace
from pathlib import Path
from unittest.mock import patch

from twodrive_office.policy import Settings, SaveError, atomic_write, fingerprint, plan_save, prepare_target
from twodrive_office.scheduler import PendingSave
from twodrive_office.bridge import Bridge, make_server, validate_document, document_content_digest


def docx(text="test"):
    content = io.BytesIO()
    with zipfile.ZipFile(content, "w") as archive:
        archive.writestr("[Content_Types].xml", "<Types/>")
        archive.writestr("word/document.xml", "<document>" + text + "</document>")
    return content.getvalue()


class SavePolicyTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.mount = self.root / "mount"
        self.mount.mkdir()
        self.settings = Settings(self.mount, self.mount / "Office AutoSave", require_mount=False,
                                 bridge_token="t" * 48)

    def plan(self, source=None, title="report", identity="doc-a", extension="docx", directory=None):
        return plan_save(self.settings, source, title, identity, extension, directory)

    def test_in_place_and_sibling_prefix(self):
        source = self.mount / "report.docx"
        source.write_bytes(docx())
        self.assertTrue(self.plan(source).in_place)
        sibling = self.root / "mount-other" / "report.docx"
        self.assertFalse(self.plan(sibling).in_place)

    def test_same_name_different_sources_have_distinct_stable_copies(self):
        a = self.plan(self.root / "a/report.docx")
        b = self.plan(self.root / "b/report.docx")
        self.assertNotEqual(a.target, b.target)
        self.assertEqual(a.target, self.plan(self.root / "a/report.docx").target)

    def test_untitled_ids_and_unicode(self):
        a = self.plan(title="报告📄", identity="a")
        b = self.plan(title="报告📄", identity="b")
        self.assertIn("报告📄", a.target.name)
        self.assertNotEqual(a.target, b.target)

    def test_chosen_directory_boundary(self):
        self.assertEqual(self.plan(directory=self.mount / "selected").target.parent, self.mount / "selected")
        with self.assertRaises(SaveError):
            self.plan(directory=self.root / "outside")

    def test_symlink_cannot_escape_mount(self):
        outside = self.root / "outside"
        outside.mkdir()
        link = self.mount / "link"
        link.symlink_to(outside, target_is_directory=True)
        with self.assertRaises(SaveError):
            self.plan(directory=link)
        directory = self.mount / "selected"
        directory.mkdir()
        plan = self.plan(directory=directory)
        directory.rmdir()
        directory.symlink_to(outside, target_is_directory=True)
        with self.assertRaises(SaveError):
            prepare_target(self.settings, plan)
        self.assertEqual(list(outside.iterdir()), [])

    def test_disconnected_mount_refuses_before_creating_copy_directory(self):
        settings = replace(self.settings, require_mount=True)
        with patch("os.path.ismount", return_value=False), self.assertRaises(SaveError):
            plan_save(settings, None, "report", "id", "docx")
        self.assertFalse(settings.copy_directory.exists())

    def test_disconnect_after_enable_preserves_previous_data(self):
        plan = self.plan()
        expected = atomic_write(self.settings, plan, docx("first"), None)
        with patch("os.path.ismount", return_value=False), self.assertRaises(SaveError):
            atomic_write(replace(self.settings, require_mount=True), plan, docx("second"), expected)
        self.assertEqual(plan.target.read_bytes(), docx("first"))

    def test_external_change_blocks_overwrite(self):
        plan = self.plan()
        expected = atomic_write(self.settings, plan, docx("first"), None)
        plan.target.write_bytes(docx("external"))
        with self.assertRaises(SaveError):
            atomic_write(self.settings, plan, docx("second"), expected)
        self.assertEqual(plan.target.read_bytes(), docx("external"))

    def test_replace_failure_preserves_previous_data_and_cleans_temporary(self):
        plan = self.plan()
        expected = atomic_write(self.settings, plan, docx("first"), None)
        with patch("os.replace", side_effect=OSError("I/O failed")), self.assertRaises(OSError):
            atomic_write(self.settings, plan, docx("second"), expected)
        self.assertEqual(plan.target.read_bytes(), docx("first"))
        self.assertEqual(list(plan.target.parent.glob("*.tmp")), [])

    def test_symlink_target_and_format_mismatch(self):
        plan = self.plan()
        plan.target.parent.mkdir()
        external = self.root / "external.docx"
        external.write_bytes(docx())
        plan.target.symlink_to(external)
        with self.assertRaises(SaveError):
            self.plan()
        source = self.mount / "native.odt"
        source.write_bytes(b"sample")
        with self.assertRaises(SaveError):
            self.plan(source, extension="docx")

    def test_config_roundtrip_is_private_and_rejects_invalid_delays(self):
        path = self.root / "config/settings.json"
        self.settings.save(path)
        self.assertEqual(path.stat().st_mode & 0o777, 0o600)
        self.assertEqual(Settings.load(path).mount_root, self.mount)
        with self.assertRaises(SaveError):
            replace(self.settings, debounce_seconds=0).save(path)

    def test_source_outside_is_never_modified(self):
        source = self.root / "external.docx"
        source.write_bytes(docx("original"))
        plan = self.plan(source)
        atomic_write(self.settings, plan, docx("edited"), None)
        self.assertEqual(source.read_bytes(), docx("original"))
        self.assertEqual(plan.target.read_bytes(), docx("edited"))

    def test_encrypted_macro_and_legacy_sources_are_refused(self):
        encrypted = self.root / "encrypted.docx"
        encrypted.write_bytes(bytes.fromhex("d0cf11e0a1b11ae1") + b"encrypted")
        with self.assertRaises(SaveError):
            self.plan(encrypted)
        macro = self.root / "macro.docx"
        with zipfile.ZipFile(macro, "w") as archive:
            archive.writestr("word/vbaProject.bin", b"macro")
        with self.assertRaises(SaveError):
            self.plan(macro)
        with self.assertRaises(SaveError):
            self.plan(self.root / "legacy.docm")
        encrypted_odf = self.root / "encrypted.odt"
        with zipfile.ZipFile(encrypted_odf, "w") as archive:
            archive.writestr("META-INF/manifest.xml", '<manifest><encryption-data/></manifest>'.encode("utf-16"))
        with self.assertRaises(SaveError):
            self.plan(encrypted_odf, extension="odt")


class SchedulerTests(unittest.TestCase):
    def test_continuous_typing_has_bounded_wait(self):
        pending = PendingSave()
        for n in range(11):
            pending.changed(n / 2)
        self.assertEqual(pending.due_at, 5)
        self.assertTrue(pending.due(5))

    def test_retry_never_marks_unsaved_changes_saved(self):
        pending = PendingSave()
        pending.changed(0)
        pending.failed(1)
        self.assertFalse(pending.due(2))
        self.assertTrue(pending.due(3))
        self.assertEqual(pending.saved_revision, 0)
        pending.changed(3)
        pending.succeeded(1, 4)
        self.assertTrue(pending.due(5))
        pending.succeeded(2, 5)
        self.assertFalse(pending.due(100))


class BridgeTests(unittest.TestCase):
    def test_content_digest_preserves_media_and_non_timestamp_metadata(self):
        def package(author, media):
            stream = io.BytesIO()
            with zipfile.ZipFile(stream, "w") as archive:
                archive.writestr("word/document.xml", "<document>unchanged</document>")
                archive.writestr("word/media/image.png", media)
                archive.writestr("docProps/core.xml", '<cp:coreProperties xmlns:cp="http://schemas.openxmlformats.org/package/2006/metadata/core-properties"><cp:lastModifiedBy>' + author + '</cp:lastModifiedBy></cp:coreProperties>')
            return stream.getvalue()
        baseline = document_content_digest(package("one", b"image-one"), "docx")
        self.assertNotEqual(document_content_digest(package("two", b"image-one"), "docx"), baseline)
        self.assertNotEqual(document_content_digest(package("one", b"image-two"), "docx"), baseline)

    def test_export_timestamp_changes_do_not_replace_content_but_real_edits_do(self):
        bridge = Bridge(self.settings)
        session = bridge.create({"document_id": "clock", "extension": "docx"})
        def exported(text, timestamp):
            stream = io.BytesIO()
            with zipfile.ZipFile(io.BytesIO(docx(text))) as source, zipfile.ZipFile(stream, "w") as archive:
                for name in source.namelist():
                    archive.writestr(name, source.read(name))
                archive.writestr("docProps/core.xml", '<cp:coreProperties xmlns:cp="http://schemas.openxmlformats.org/package/2006/metadata/core-properties" xmlns:dcterms="http://purl.org/dc/terms/"><dcterms:modified>' + timestamp + '</dcterms:modified></cp:coreProperties>')
            return stream.getvalue()
        bridge.save(session["session_id"], exported("same", "2026-10-18T10:00:00Z"))
        target = Path(session["target"])
        before = target.read_bytes(), fingerprint(target)
        bridge.save(session["session_id"], exported("same", "2026-10-18T10:00:10Z"))
        self.assertEqual((target.read_bytes(), fingerprint(target)), before)
        bridge.save(session["session_id"], exported("edited", "2026-10-18T10:00:10Z"))
        self.assertNotEqual(target.read_bytes(), before[0])

    def test_identical_native_model_with_new_transport_revision_does_not_rewrite(self):
        bridge = Bridge(self.settings)
        session = bridge.create({"title": "No-op", "document_id": "noop", "extension": "docx"})
        def payload(revision, media=None):
            return json.dumps({"revision": revision, "binary": "same native model", "media": media or {}}).encode()
        with patch("twodrive_office.converter.convert_snapshot", return_value=docx("saved")) as convert:
            bridge.save_snapshot(session["session_id"], payload(1))
            before = fingerprint(Path(session["target"]))
            # Also models a committed request whose HTTP response was lost.
            result = bridge.save_snapshot(session["session_id"], payload(2))
            self.assertEqual(result["status"], "saved_local")
            self.assertEqual(convert.call_count, 1, "transport-only changes must not reconvert and atomically replace the file")
            self.assertEqual(fingerprint(Path(session["target"])), before)
            bridge.save_snapshot(session["session_id"], payload(3, {"image.png": "YQ=="}))
            self.assertEqual(convert.call_count, 2, "media changes are document changes")

    def test_identical_snapshot_revalidates_destination_and_mount(self):
        bridge = Bridge(self.settings)
        session = bridge.create({"title": "No-op", "document_id": "noop", "extension": "docx"})
        payload = lambda revision: json.dumps({"revision": revision, "binary": "same model"}).encode()
        with patch("twodrive_office.converter.convert_snapshot", return_value=docx("saved")):
            bridge.save_snapshot(session["session_id"], payload(1))
            bridge.settings = replace(self.settings, require_mount=True)
            with patch("os.path.ismount", return_value=False), self.assertRaises(SaveError):
                bridge.save_snapshot(session["session_id"], payload(2))
            bridge.settings = self.settings
            target = Path(session["target"])
            target.write_bytes(docx("external"))
            with self.assertRaises(SaveError):
                bridge.save_snapshot(session["session_id"], payload(3))
            self.assertEqual(target.read_bytes(), docx("external"))

    setUp = SavePolicyTests.setUp
    def test_bridge_document_validation(self):
        validate_document(docx(), "docx")
        for content, extension in ((b"<html>error</html>", "docx"), (docx(), "xlsx"), (b"", "docx")):
            with self.assertRaises(SaveError):
                validate_document(content, extension)

    def test_missing_media_is_rejected_before_overwrite(self):
        content = io.BytesIO()
        with zipfile.ZipFile(content, "w") as archive:
            archive.writestr("[Content_Types].xml", "<Types/>")
            archive.writestr("word/document.xml", "<document/>")
            archive.writestr("word/_rels/document.xml.rels", '<Relationships><Relationship Target="media/image1.png"/></Relationships>')
        with self.assertRaises(SaveError):
            validate_document(content.getvalue(), "docx")

    def test_native_origins_receive_exact_cors_header(self):
        server = make_server(Bridge(self.settings), port=0)
        threading.Thread(target=server.serve_forever, daemon=True).start()
        self.addCleanup(server.server_close)
        self.addCleanup(server.shutdown)
        for origin in ("file://", "onlyoffice://plugin"):
            request = urllib.request.Request(f"http://127.0.0.1:{server.server_port}/settings", headers={
                "Authorization": "Bearer " + self.settings.bridge_token, "Origin": origin})
            with urllib.request.urlopen(request) as response:
                self.assertEqual(response.headers["Access-Control-Allow-Origin"], origin)

    def test_late_old_snapshot_cannot_overwrite_newer_revision(self):
        bridge = Bridge(self.settings)
        session = bridge.create({"document_id": "version-test", "extension": "docx"})
        entered, release = threading.Event(), threading.Event()
        results = []
        def convert(snapshot, extension):
            if snapshot["revision"] == 1:
                entered.set()
                self.assertTrue(release.wait(5))
            return docx(snapshot["binary"])
        def old_save():
            results.append(bridge.save_snapshot(session["session_id"], json.dumps({"revision": 1, "binary": "old"}).encode()))
        with patch("twodrive_office.converter.convert_snapshot", side_effect=convert):
            worker = threading.Thread(target=old_save)
            worker.start()
            self.assertTrue(entered.wait(5))
            bridge.save_snapshot(session["session_id"], json.dumps({"revision": 2, "binary": "new"}).encode())
            release.set()
            worker.join(5)
        self.assertEqual(Path(session["target"]).read_bytes(), docx("new"))
        self.assertEqual(results[0]["status"], "already_saved_newer")

    def test_session_duplicate_close_and_in_place_confirmation(self):
        bridge = Bridge(self.settings)
        data = {"document_id": "test", "extension": "docx"}
        session = bridge.create(data)
        with self.assertRaises(SaveError):
            bridge.create(data)
        bridge.save(session["session_id"], docx("saved"))
        bridge.close(session["session_id"])
        with self.assertRaises(SaveError):
            bridge.save(session["session_id"], docx("later"))
        source = self.mount / "original.docx"
        source.write_bytes(docx())
        with self.assertRaises(SaveError):
            bridge.create({**data, "source_path": str(source)})
        self.assertTrue(bridge.create({**data, "source_path": str(source), "confirm_source": True})["in_place"])

    def test_http_auth_origin_and_upload(self):
        server = make_server(Bridge(self.settings), port=0)
        thread = threading.Thread(target=server.serve_forever, daemon=True)
        thread.start()
        self.addCleanup(server.server_close)
        self.addCleanup(server.shutdown)
        base = f"http://127.0.0.1:{server.server_port}"
        def request(path, data=None, method=None, headers=None):
            headers = {"Authorization": "Bearer " + self.settings.bridge_token, **(headers or {})}
            return urllib.request.urlopen(urllib.request.Request(base + path, data, headers, method=method), timeout=5)
        for headers in ({"Authorization": "Bearer wrong"}, {"Origin": "https://untrusted.example"}, {"Host": "evil.example"}):
            with self.assertRaises(urllib.error.HTTPError) as caught:
                request("/settings", headers=headers)
            self.assertEqual(caught.exception.code, 401)
        data = json.dumps({"document_id": "test", "extension": "docx"}).encode()
        with request("/sessions", data, headers={"Origin": "null"}) as response:
            session = json.load(response)
            self.assertEqual(response.headers["Access-Control-Allow-Origin"], "null")
        with request("/sessions/" + session["session_id"], docx("http")) as response:
            self.assertEqual(json.load(response)["status"], "saved_local")
        self.assertEqual(Path(session["target"]).read_bytes(), docx("http"))
        with self.assertRaises(urllib.error.HTTPError):
            request("/sessions/" + session["session_id"], b"bad export")
        self.assertEqual(Path(session["target"]).read_bytes(), docx("http"))


if __name__ == "__main__":
    unittest.main()

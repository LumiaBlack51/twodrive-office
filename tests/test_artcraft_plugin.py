import io
import json
import os
import subprocess
import sys
import tempfile
import unittest
import zipfile
from pathlib import Path
from unittest.mock import patch

from scripts.install_artcraft import check
from twodrive_office import artcraft_plugin as plugin
from twodrive_office.policy import SaveError, Settings, fingerprint

ROOT = Path(__file__).resolve().parents[1]


def package(extension="docx", text="saved"):
    output = io.BytesIO()
    with zipfile.ZipFile(output, "w") as archive:
        main = {"docx": "word/document.xml", "xlsx": "xl/workbook.xml", "pptx": "ppt/presentation.xml"}.get(extension)
        if main:
            archive.writestr(zipfile.ZipInfo("[Content_Types].xml"), "<Types/>")
            archive.writestr(zipfile.ZipInfo(main), "<document>" + text + "</document>")
        else:
            kind = {"odt": "text", "ods": "spreadsheet", "odp": "presentation"}[extension]
            archive.writestr(zipfile.ZipInfo("mimetype"), "application/vnd.oasis.opendocument." + kind)
            archive.writestr(zipfile.ZipInfo("content.xml"), "<document>" + text + "</document>")
    return output.getvalue()


def initialization(app="wordcraft"):
    return {"protocolVersion": 1, "host": {"id": app, "protocol": "2.2", "features": ["dialogs", "events", "document"]}}


class PluginTests(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory(prefix="twodrive-plugin-test-")
        self.addCleanup(self.temporary.cleanup)
        self.root = Path(self.temporary.name)
        self.mount = self.root / "mock-mount"
        self.mount.mkdir()
        self.source = self.root / "slides.docx"
        self.source.write_bytes(package())
        self.settings = Settings(self.mount, self.mount / "Copies", require_mount=False, sync_interval_seconds=5)
        self.now = 0
        self.runtime = plugin.Plugin(self.settings, root=self.root / "revisions", clock=lambda: self.now)
        self.runtime.handle("initialize", initialization())
        self.addCleanup(self.runtime.shutdown)

    def info(self, **fields):
        return {"id": "doc-1", "path": str(self.source), "name": self.source.name, **fields}

    def command(self, command, args=None, info=None):
        return self.runtime.handle("commands/run", {"command": command, "params": args or {},
                                   "context": {"document": info or self.info()}})

    def enable(self, **options):
        info = options.pop("info", self.info())
        form = self.command("settings", info=info)["dialog"]
        return self.command("apply", {**form["params"], "fields": {
            "enabled": True, "directory": str(self.settings.copy_directory), "interval": 5, **options}}, info=info)

    def state(self):
        return self.runtime.manager().active["doc-1"]

    def test_forms_bind_to_document_and_never_issue_editor_commands(self):
        before = fingerprint(self.source)
        form = self.command("settings", info=self.info(dirty=True))
        self.assertIn(plugin.SAVED_ONLY, form["dialog"]["prompt"])
        self.assertEqual(form["dialog"]["fields"][0]["kind"], "checkbox")
        with self.assertRaisesRegex(SaveError, "文档已切换"):
            self.command("apply", {**form["dialog"]["params"], "fields": {"enabled": True}}, self.info(id="doc-2"))
        results = [form, self.enable(), self.command("sync", info=self.info(dirty=True)),
                   self.command("status"), self.command("pending"), self.command("pause")]
        for result in results:
            self.assertFalse({"commands", "open", "host/sync"}.intersection(result))
        self.assertEqual(fingerprint(self.source), before)

    def test_all_six_formats_copy_without_writing_editor_source(self):
        self.runtime.shutdown()
        for app, extensions in plugin.APPS.items():
            for extension in extensions:
                with self.subTest(app=app, extension=extension):
                    self.source = self.root / (app + "." + extension)
                    self.source.write_bytes(package(extension))
                    self.runtime = plugin.Plugin(self.settings, root=self.root / extension, clock=lambda: self.now)
                    self.runtime.handle("initialize", initialization(app))
                    self.enable()
                    self.runtime.tick()
                    state = self.state()
                    self.assertFalse(state.plan.in_place)
                    self.assertNotEqual(state.plan.target, self.source)
                    self.now += 5
                    self.runtime.tick()
                    self.assertEqual(state.plan.target.read_bytes(), self.source.read_bytes())
                    self.assertFalse(state.manifest.exists())
                    self.runtime.shutdown()

    def test_source_on_mount_is_still_copy_only_and_dirty_not_saved(self):
        self.source = self.mount / "source.docx"
        self.source.write_bytes(package())
        before = fingerprint(self.source)
        self.enable(info=self.info(dirty=True))
        self.runtime.tick()
        self.command("sync", info=self.info(dirty=True))
        self.runtime.tick()
        self.assertEqual(fingerprint(self.source), before)
        self.assertNotEqual(self.state().plan.target, self.source)
        self.assertTrue(self.command("status")["result"]["saved_versions_only"])

    def test_timer_detects_native_autosave_without_saved_event(self):
        self.enable()
        self.runtime.tick()
        self.now = 5
        self.runtime.tick()
        state = self.state()
        before = fingerprint(state.plan.target)
        self.runtime.tick()
        self.assertEqual(fingerprint(state.plan.target), before)
        newer = package(text="native autosave changed disk, no event")
        self.source.write_bytes(newer)
        self.runtime.tick()
        self.assertEqual(state.record.blob.read_bytes(), newer)
        self.now = 10
        self.runtime.tick()
        self.assertEqual(state.plan.target.read_bytes(), newer)

    def test_offline_capture_retries_and_keeps_recovery_after_shutdown(self):
        self.enable()
        state = self.state()
        with patch.object(Settings, "check_mount", side_effect=SaveError("offline")):
            self.runtime.tick()
            self.assertTrue(state.manifest.exists())
            self.assertEqual(state.blob.read_bytes(), self.source.read_bytes())
            self.assertIsNone(state.record.blob)
            self.runtime.shutdown()
        other = plugin.Copies(self.settings, root=self.root / "revisions")
        self.addCleanup(other.shutdown)
        with self.assertRaisesRegex(SaveError, "保留修订"):
            other.enable(self.info(), "wordcraft", str(self.settings.copy_directory), 5)
        self.assertTrue(state.manifest.exists())

    def test_mount_and_target_are_revalidated_even_when_content_unchanged(self):
        self.enable()
        self.runtime.tick()
        state = self.state()
        with patch.object(Settings, "check_mount", side_effect=SaveError("offline")) as guard:
            self.runtime.tick()
            self.assertTrue(guard.called)
            self.assertIn("offline", state.error)
        external = package(text="external target")
        state.plan.target.write_bytes(external)
        self.now = 500
        self.runtime.tick()
        self.assertTrue(state.error)
        self.assertEqual(state.plan.target.read_bytes(), external)
        self.assertTrue(state.manifest.exists())

    def test_symlink_target_is_never_followed(self):
        self.enable()
        self.runtime.tick()
        state = self.state()
        outside = self.root / "untouched.docx"
        outside.write_bytes(package(text="outside"))
        state.plan.target.symlink_to(outside)
        self.now = 5
        self.runtime.tick()
        self.assertEqual(outside.read_bytes(), package(text="outside"))
        self.assertTrue(state.error)
        self.assertTrue(state.manifest.exists())

    def test_revert_to_published_content_supersedes_pending_newer_revision(self):
        self.enable()
        self.runtime.tick()
        self.now = 5
        self.runtime.tick()
        original = self.source.read_bytes()
        self.source.write_bytes(package(text="B"))
        self.runtime.tick()
        self.source.write_bytes(original)
        self.runtime.tick()
        self.now = 10
        self.runtime.tick()
        self.assertEqual(self.state().plan.target.read_bytes(), original)

    def test_local_persistence_failure_retries_same_source(self):
        self.enable()
        with patch.object(plugin, "durable_replace", side_effect=OSError("disk full")):
            self.runtime.tick()
        self.assertIsNone(self.state().last_source)
        self.runtime.tick()
        self.assertTrue(self.state().manifest.exists())
        self.assertEqual(self.state().record.blob.read_bytes(), self.source.read_bytes())

    def test_source_replaced_during_read_does_not_adopt_new_baseline(self):
        original = fingerprint(self.source)
        real = plugin.fingerprint
        def changing(path):
            if path == self.source and real(path) == original:
                self.source.write_bytes(package(text="changed during read registration"))
                return original
            return real(path)
        with patch.object(plugin, "fingerprint", side_effect=changing):
            with self.assertRaisesRegex(SaveError, "正在保存"):
                plugin.saved_snapshot(self.source)

    def test_target_change_during_source_read_keeps_external_and_pending_bytes(self):
        self.enable()
        state = self.state()
        real = plugin.saved_snapshot
        external = package(text="external copy changed while source was read")
        def race(path):
            content = real(path)
            state.plan.target.write_bytes(external)
            return content
        with patch.object(plugin, "saved_snapshot", side_effect=race):
            self.runtime.tick()
        self.assertEqual(state.plan.target.read_bytes(), external)
        self.assertEqual(state.blob.read_bytes(), package())
        self.assertIsNone(state.record.blob)

    def test_interruption_before_manifest_keeps_orphan_and_blocks_reopen(self):
        self.enable()
        state = self.state()
        real = plugin.durable_replace
        def interrupted(path, data):
            if path == state.manifest:
                raise OSError("manifest write interrupted")
            real(path, data)
        with patch.object(plugin, "durable_replace", side_effect=interrupted):
            self.runtime.tick()
            self.runtime.shutdown()
        blobs = list(state.directory.glob("*.document"))
        self.assertEqual(len(blobs), 1)
        self.assertEqual(blobs[0].read_bytes(), package())
        self.assertFalse(state.manifest.exists())
        other = plugin.Copies(self.settings, root=self.root / "revisions")
        self.addCleanup(other.shutdown)
        with self.assertRaisesRegex(SaveError, "保留修订"):
            other.enable(self.info(), "wordcraft", str(self.settings.copy_directory), 5)
        self.assertEqual(other.pending()[0]["label"], "修订写入中断")

    def test_corrupt_source_and_changed_recovery_digest_are_refused(self):
        self.enable()
        state = self.state()
        with patch.object(Settings, "check_mount", side_effect=SaveError("offline")):
            self.runtime.tick()
            self.runtime.shutdown()
        state.blob.write_bytes(b"damaged recovery")
        with self.assertRaisesRegex(SaveError, "校验失败"):
            plugin.export_revision(state.directory.name, self.root / "new.docx", root=self.root / "revisions")
        self.assertTrue(state.manifest.exists())
        self.assertFalse((self.root / "new.docx").exists())
        self.source.write_bytes(b"unfinished ZIP")
        with self.assertRaises(SaveError):
            plugin.saved_snapshot(self.source)

    def test_macro_and_encrypted_odf_disk_revisions_never_publish(self):
        self.enable()
        data = io.BytesIO(package())
        with zipfile.ZipFile(data, "a") as archive:
            archive.writestr("word/vbaProject.bin", b"macro")
        self.source.write_bytes(data.getvalue())
        self.runtime.tick()
        self.assertIn("宏", self.state().error)
        self.assertFalse(self.state().plan.target.exists())
        odf = self.root / "encrypted.odt"
        data = io.BytesIO(package("odt"))
        with zipfile.ZipFile(data, "a") as archive:
            archive.writestr("META-INF/manifest.xml", "<manifest><encryption-data/></manifest>")
        odf.write_bytes(data.getvalue())
        with self.assertRaisesRegex(SaveError, "加密"):
            plugin.saved_snapshot(odf)

    def test_paused_offline_status_still_reports_retained_data(self):
        self.enable()
        with patch.object(Settings, "check_mount", side_effect=SaveError("offline")):
            self.runtime.tick()
            self.command("pause")
            self.runtime.tick()
        status = self.command("status")["result"]
        self.assertFalse(status["enabled"])
        self.assertTrue(status["pending"])
        self.assertIn("offline", status["error"])
        self.assertIn("已暂停", status["status"])

    def test_save_as_pauses_without_retargeting_and_close_flushes(self):
        self.enable()
        self.runtime.tick()
        state = self.state()
        self.runtime.handle("events/documentSaved", self.info(path=str(self.root / "new.docx")))
        self.assertFalse(self.command("status", info=self.info(path=str(self.root / "new.docx")))["result"]["enabled"])
        self.runtime.tick()
        self.assertEqual(state.plan.target.read_bytes(), package())
        self.enable()
        newer = package(text="final disk version")
        self.source.write_bytes(newer)
        state = self.state()
        self.runtime.handle("events/documentClosed", self.info())
        self.runtime.tick()
        self.assertEqual(state.plan.target.read_bytes(), newer)
        self.assertFalse(self.runtime.manager().retired)

    def test_same_target_cannot_be_owned_by_another_process_session(self):
        self.enable()
        other = plugin.Copies(self.settings, root=self.root / "revisions")
        self.addCleanup(other.shutdown)
        with self.assertRaises(SaveError):
            other.enable(self.info(id="doc-2"), "wordcraft", str(self.settings.copy_directory), 5)

    def test_export_never_overwrites_or_discards_either_pending_revision(self):
        self.enable()
        state = self.state()
        with patch.object(Settings, "check_mount", side_effect=SaveError("offline")):
            self.runtime.tick()
            self.runtime.shutdown()
        content_a = state.blob.read_bytes()
        # A different/older state root can have another pending version for the same target.
        other = plugin.Copies(self.settings, root=self.root / "older-root", queue_root=self.root / "other-queue")
        self.addCleanup(other.shutdown)
        self.source.write_bytes(package(text="distinct pending B"))
        state_b = other.enable(self.info(), "wordcraft", str(self.settings.copy_directory), 5)
        other.tick()
        pending_b = state_b.record.blob.read_bytes()
        output = self.root / "recovered.docx"
        plugin.export_revision(state.directory.name, output, root=self.root / "revisions")
        self.assertEqual(output.read_bytes(), content_a)
        self.assertEqual(output.stat().st_mode & 0o777, 0o600)
        with self.assertRaises(FileExistsError):
            plugin.export_revision(state.directory.name, output, root=self.root / "revisions")
        with self.assertRaisesRegex(SaveError, "挂载目录外"):
            plugin.export_revision(state.directory.name, self.mount / "recovered.docx", root=self.root / "revisions")
        self.assertEqual(state_b.record.blob.read_bytes(), pending_b)
        self.assertEqual(state.blob.read_bytes(), content_a)

    def test_bad_host_and_unsaved_or_invalid_apply_fail_closed(self):
        for host in (None, [], {"features": {}}, {"id": "wordcraft", "protocol": "2.1", "features": []}):
            with self.subTest(host=host), self.assertRaises(SaveError):
                plugin.Plugin(self.settings).handle("initialize", {"protocolVersion": 1, "host": host})
        for interval in (True, 0, 3601, float("nan")):
            with self.subTest(interval=interval), self.assertRaises(SaveError):
                self.enable(interval=interval)
        with self.assertRaisesRegex(SaveError, "先在编辑器保存"):
            self.enable(info=self.info(path=None))
        self.assertFalse(self.runtime.manager().active)

    def test_jsonrpc_errors_notifications_shutdown_and_frame_limit(self):
        frames = [b"bad json\n", b"[]\n", b'{"jsonrpc":"2.0","id":[],"method":"status"}\n',
                  json.dumps({"jsonrpc": "2.0", "id": 1, "method": "unknown"}).encode() + b"\n",
                  b'{"jsonrpc":"2.0","method":"events/documentOpened","params":{}}\n',
                  b'{"jsonrpc":"2.0","id":8,"result":{}}\n',
                  b'{"jsonrpc":"2.0","method":"shutdown"}\n']
        output = io.BytesIO()
        plugin.serve(io.BytesIO(b"".join(frames)), output, self.runtime)
        responses = [json.loads(line) for line in output.getvalue().splitlines()]
        self.assertEqual([r["error"]["code"] for r in responses], [-32700, -32600, -32600, -32601])
        output = io.BytesIO()
        plugin.serve(io.BytesIO(b"x" * (plugin.MAX_LINE + 1)), output, self.runtime)
        self.assertEqual(output.getvalue(), b"")

    def test_host_checker_does_not_install_or_start_gui(self):
        before = set(self.root.rglob("*"))
        completed = subprocess.CompletedProcess([], 0, "plugins.list\nplugins.load\n")
        with patch("scripts.install_artcraft.subprocess.run", return_value=completed) as run:
            result = check({"wordcraft": "/mock/cli"})
        self.assertTrue(result["wordcraft"]["host_commands"])
        self.assertFalse(result["deckcraft"]["host_commands"])
        self.assertEqual(run.call_args.args[0], ["/mock/cli", "commands"])
        self.assertNotIn("DISPLAY", run.call_args.kwargs["env"])
        self.assertFalse(Path(run.call_args.kwargs["env"]["XDG_CONFIG_HOME"]).exists())
        self.assertEqual(set(self.root.rglob("*")), before)

    def test_real_subprocess_all_hosts_exit_on_eof_or_shutdown(self):
        for app in plugin.APPS:
            with self.subTest(app=app):
                env = {**os.environ, "PYTHONDONTWRITEBYTECODE": "1", "XDG_CONFIG_HOME": str(self.root / "config"),
                       "XDG_STATE_HOME": str(self.root / app)}
                self.settings.save(self.root / "config/twodrive-office/settings.json")
                for shutdown in (False, True):
                    frames = [{"jsonrpc": "2.0", "id": 1, "method": "initialize", "params": initialization(app)}]
                    if shutdown:
                        frames.append({"jsonrpc": "2.0", "method": "shutdown"})
                    completed = subprocess.run([sys.executable, "-B", str(ROOT / "artcraft/main.py")],
                        input=b"".join(json.dumps(f).encode() + b"\n" for f in frames), env=env,
                        capture_output=True, timeout=5, check=True)
                    response = json.loads(completed.stdout)
                    self.assertEqual(response["result"]["protocolVersion"], 1)
                    self.assertEqual(completed.stderr, b"")


if __name__ == "__main__":
    unittest.main()

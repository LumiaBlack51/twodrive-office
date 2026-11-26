import os
import io
import socket
import subprocess
import sys
import tempfile
import threading
import unittest
import urllib.error
import urllib.request
import zipfile
from pathlib import Path
from unittest.mock import Mock, patch

from twodrive_office.bridge import Bridge, make_server
from twodrive_office.lifecycle import Activity, OfficePresence, activation_socket, serve_on_demand
from twodrive_office.policy import SaveError, Settings


def document(text):
    output = io.BytesIO()
    with zipfile.ZipFile(output, "w") as archive:
        archive.writestr("[Content_Types].xml", "<Types/>")
        archive.writestr("word/document.xml", "<document>" + text + "</document>")
    return output.getvalue()


class LifecycleTests(unittest.TestCase):
    def test_idle_exit_ignores_orphaned_editor_and_preserves_native_pending(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            original = document("original")
            source = root / "source.docx"; source.write_bytes(original)
            settings = Settings(root, root / "copies", require_mount=False, bridge_token="t" * 48)
            bridge = Bridge(settings, state_root=root / "state")
            key = bridge.create({"source_path": str(source), "confirm_source": True,
                                 "native_publish": True})["session_id"]
            record = bridge.sessions[key].local
            content = document("unsynced native revision")
            record.stage(content)
            blob, manifest = record.blob, record.manifest
            server = Mock(bridge=bridge, activity=Activity(lambda: 100))
            server.activity.last_request = 0
            # The residual process must prevent unsafe native recovery, but
            # must never prevent the bridge itself from exiting.
            presence = Mock(); presence.running.return_value = True
            with patch("twodrive_office.local_save.atomic_write", side_effect=AssertionError("No native publication")):
                with patch("sys.stdout", new=io.StringIO()):
                    serve_on_demand(server, 30, all_office=presence)
            server.handle_request.assert_called_once()
            self.assertFalse(bridge.sessions)
            self.assertTrue(bridge.local_saves.pending())
            self.assertEqual(source.read_bytes(), original)
            bridge.local_saves.shutdown()
            self.assertEqual(blob.read_bytes(), content)
            self.assertTrue(manifest.exists())
            recovered = Bridge(settings, state_root=root / "state")
            try:
                self.assertTrue(recovered.local_saves.pending())
            finally:
                recovered.local_saves.shutdown()

    def test_idle_exit_attempts_safe_recovery_once_and_keeps_conflict(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            source = root / "source.docx"; source.write_bytes(document("original"))
            settings = Settings(root, root / "copies", require_mount=False, bridge_token="t" * 48)
            bridge = Bridge(settings, state_root=root / "state")
            self.addCleanup(bridge.local_saves.shutdown)
            key = bridge.create({"source_path": str(source), "confirm_source": True})["session_id"]
            record = bridge.sessions[key].local
            record.stage(b"pending revision")
            source.write_bytes(b"external change")
            server = Mock(bridge=bridge, activity=Activity(lambda: 100))
            server.activity.last_request = 0
            presence = Mock(); presence.running.return_value = False
            with patch("sys.stdout", new=io.StringIO()):
                serve_on_demand(server, 30, all_office=presence)
            server.handle_request.assert_called_once()
            self.assertTrue(bridge.local_saves.pending())
            self.assertEqual(record.blob.read_bytes(), b"pending revision")
            self.assertEqual(source.read_bytes(), b"external change")

    def test_running_request_finishes_before_idle_exit(self):
        bridge = Mock(sessions={})
        bridge.local_saves.pending.return_value = False
        server = Mock(bridge=bridge, activity=Activity(lambda: 100))
        server.activity.last_request = 0
        server.activity.begin()
        calls = []
        def request():
            calls.append(True)
            if len(calls) == 2:
                server.activity.end()
        server.handle_request.side_effect = request
        presence = Mock(); presence.running.return_value = True
        with patch("sys.stdout", new=io.StringIO()):
            serve_on_demand(server, 30, all_office=presence)
        self.assertEqual(len(calls), 2)

    def test_real_cli_exits_without_waiting_for_editor_pids(self):
        with tempfile.TemporaryDirectory(prefix="twodrive-lifetime-test-") as directory:
            root = Path(directory)
            with socket.socket() as listener:
                listener.bind(("127.0.0.1", 0))
                port = listener.getsockname()[1]
            config = root / "config.json"
            Settings(root, root / "copies", require_mount=False,
                     bridge_port=port, bridge_token="t" * 48).save(config)
            environment = dict(os.environ, PYTHONDONTWRITEBYTECODE="1", XDG_STATE_HOME=str(root / "state"))
            command = [sys.executable, "-m", "twodrive_office.cli", "--config", str(config), "serve"]
            child = subprocess.Popen(command + ["--idle-timeout", "0.1"], env=environment,
                                     stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True,
                                     start_new_session=True)
            try:
                output, errors = child.communicate(timeout=5)
                self.assertEqual(child.returncode, 0, errors)
                self.assertIn("桥接空闲，按需退出", output)
            finally:
                if child.poll() is None:
                    import signal
                    os.killpg(child.pid, signal.SIGTERM); child.wait(timeout=5)
                child.stdout.close(); child.stderr.close()
            for invalid in ("0", "-1", "nan", "inf"):
                result = subprocess.run(command + ["--idle-timeout", invalid], env=environment,
                                        capture_output=True, text=True, timeout=5)
                self.assertNotEqual(result.returncode, 0)
                self.assertIn("有限秒数", result.stderr)

    def test_busy_save_cannot_expire_and_authenticated_activity_resets_deadline(self):
        now = [0]
        activity = Activity(lambda: now[0])
        now[0] = 31
        self.assertTrue(activity.idle(30))
        activity.begin()
        activity.touch()
        now[0] = 90
        self.assertFalse(activity.idle(30), "a long converter request must finish before exit")
        activity.end()
        self.assertTrue(activity.idle(30))
        activity.touch()
        self.assertFalse(activity.idle(30))

    def test_recovery_presence_recognizes_owned_editors_and_caches_scan(self):
        now = [0]
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            process = root / "123"
            process.mkdir()
            (process / "comm").write_text("soffice.bin\n")
            presence = OfficePresence(root, lambda: now[0])
            self.assertFalse(presence.running(), "LibreOffice needs no bridge")
            (process / "comm").write_text("DesktopEditors\n")
            self.assertFalse(presence.running(), "no needless repeated /proc scan")
            now[0] = 5
            self.assertTrue(presence.running())
            now[0] = 10
            with patch("twodrive_office.lifecycle.os.getuid", return_value=-1):
                self.assertFalse(presence.running(), "another user's editor must not retain our bridge")

    def test_inherited_socket_serves_native_requests_without_rebinding(self):
        with tempfile.TemporaryDirectory() as directory:
            mount = Path(directory)
            settings = Settings(mount, mount / "copies", require_mount=False, bridge_token="t" * 48)
            listener = socket.socket()
            listener.bind(("127.0.0.1", 0))
            listener.listen()
            port = listener.getsockname()[1]
            bridge = Bridge(settings, state_root=mount / "state")
            self.addCleanup(bridge.local_saves.shutdown)
            server = make_server(bridge, listener=listener)
            self.addCleanup(server.server_close)
            self.addCleanup(server.shutdown)
            threading.Thread(target=server.serve_forever, daemon=True).start()
            self.assertEqual(server.server_port, port)
            server.activity.clock = lambda: 100
            server.activity.last_request = 0
            url = f"http://127.0.0.1:{port}/settings"
            with self.assertRaises(urllib.error.HTTPError) as error:
                urllib.request.urlopen(url, timeout=2)
            error.exception.close()
            self.assertEqual(server.activity.last_request, 0, "unauthenticated traffic cannot reset idle time")
            request = urllib.request.Request(url, headers={
                "Authorization": "Bearer " + settings.bridge_token, "Origin": "onlyoffice://plugin"})
            with urllib.request.urlopen(request, timeout=2) as response:
                self.assertEqual(response.status, 200)
                self.assertEqual(response.headers["Access-Control-Allow-Origin"], "onlyoffice://plugin")
            self.assertEqual(server.activity.last_request, 100)

    def test_activation_rejects_wrong_process_or_multiple_listeners(self):
        for count, pid in (("2", str(os.getpid())), ("1", "-1")):
            with patch.dict(os.environ, {"LISTEN_FDS": count, "LISTEN_PID": pid}):
                with self.assertRaises(SaveError):
                    activation_socket(19836)


if __name__ == "__main__":
    unittest.main()


class WinePresenceTests(unittest.TestCase):
    def test_wine_office_presence_blocks_recovery_until_their_process_exits(self):
        from twodrive_office.lifecycle import OfficePresence
        import tempfile
        for name in ('WINWORD.EXE', 'EXCEL.EXE', 'POWERPNT.EXE'):
            with self.subTest(host=name), tempfile.TemporaryDirectory() as temporary:
                proc = Path(temporary); process = proc / '123'; process.mkdir()
                (process / 'comm').write_text(name + '\n')
                now = [0]
                presence = OfficePresence(proc=proc, clock=lambda: now[0])
                self.assertTrue(presence.running())
                (process / 'comm').unlink(); process.rmdir(); now[0] = 6
                self.assertFalse(presence.running())

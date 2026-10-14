import os
import socket
import tempfile
import threading
import unittest
import urllib.error
import urllib.request
from pathlib import Path
from unittest.mock import patch

from twodrive_office.bridge import Bridge, make_server
from twodrive_office.lifecycle import Activity, OfficePresence, activation_socket
from twodrive_office.policy import SaveError, Settings


class LifecycleTests(unittest.TestCase):
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

    def test_presence_only_retains_our_onlyoffice_and_caches_scan(self):
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
            server = make_server(Bridge(settings), listener=listener)
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

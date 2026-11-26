"""Socket activation and bounded lifetime; never starts an office application."""
from __future__ import annotations

import os
import socket
import threading
import time
from pathlib import Path

from .policy import SaveError

BRIDGE_EDITORS = ("DesktopEditors", "desktopeditors", "WINWORD.EXE", "EXCEL.EXE", "POWERPNT.EXE")
ALL_EDITORS = (*BRIDGE_EDITORS, "soffice.bin")


class Activity:
    def __init__(self, clock=time.monotonic):
        self.clock = clock
        self.last_request = clock()
        self.connections = 0
        self.lock = threading.Lock()

    def begin(self):
        with self.lock:
            self.connections += 1

    def end(self):
        with self.lock:
            self.connections -= 1

    def touch(self):
        with self.lock:
            self.last_request = self.clock()

    def idle(self, seconds):
        with self.lock:
            return self.connections == 0 and self.clock() - self.last_request >= seconds


class OfficePresence:
    """Check only while the bridge runs; no separate process or login poller."""
    def __init__(self, proc=Path("/proc"), clock=time.monotonic, names=BRIDGE_EDITORS):
        self.proc, self.clock = proc, clock
        self.names = names
        self.checked_at = float("-inf")
        self.present = False

    def running(self):
        now = self.clock()
        if now - self.checked_at < 5:
            return self.present
        self.checked_at = now
        self.present = False
        for process in self.proc.iterdir():
            if not process.name.isdigit():
                continue
            try:
                if process.stat().st_uid != os.getuid():
                    continue
                if (process / "comm").read_text().strip() in self.names:
                    self.present = True
                    break
            except (FileNotFoundError, PermissionError, ProcessLookupError):
                continue
        return self.present


def serve_on_demand(server, idle_timeout, *, all_office=None):
    """Only active requests/heartbeats retain the bridge, never editor PIDs."""
    all_office = all_office if all_office is not None else OfficePresence(names=ALL_EDITORS)
    bridge = server.bridge
    server.timeout = 1
    while True:
        server.handle_request()
        bridge.local_saves.tick()
        if not server.activity.idle(idle_timeout):
            continue
        for key in list(bridge.sessions):
            bridge.close(key)
        if not all_office.running():
            bridge.local_saves.allow_recovery()
            bridge.local_saves.tick()
        # Pending data is already durable. Offline/conflicted/damaged revisions
        # must survive exit, rather than keeping a retry loop alive indefinitely.
        if bridge.local_saves.pending():
            print("本地待同步版本已保留；下次连接或 recover 时重试", flush=True)
        print("桥接空闲，按需退出", flush=True)
        return


def activation_socket(port: int):
    """Use the single loopback listener passed by systemd, or bind normally."""
    count = os.environ.pop("LISTEN_FDS", None)
    pid = os.environ.pop("LISTEN_PID", None)
    os.environ.pop("LISTEN_FDNAMES", None)
    if count is None:
        return None
    if count != "1" or pid != str(os.getpid()):
        raise SaveError("桥接服务收到无效的 socket activation 配置")
    listener = socket.socket(fileno=3)
    if (listener.family != socket.AF_INET or listener.type != socket.SOCK_STREAM
            or not listener.getsockopt(socket.SOL_SOCKET, socket.SO_ACCEPTCONN)
            or listener.getsockname() != ("127.0.0.1", port)):
        listener.close()
        raise SaveError("桥接服务收到不符合设置的监听接口")
    listener.set_inheritable(False)
    return listener

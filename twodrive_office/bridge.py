"""Authenticated loopback bridge for the ONLYOFFICE plugin. Never fetches URLs."""
from __future__ import annotations

import io
import json
import posixpath
import secrets
import subprocess
import threading
import time
import zipfile
from dataclasses import dataclass, field
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
import xml.etree.ElementTree as ET

from .policy import Settings, SaveError, SavePlan, atomic_write, check_source, fingerprint, normalized, plan_save
from .lifecycle import Activity

MAX_CONTENT = 128 * 1024 * 1024
PLUGIN_ORIGINS = ("null", "file://", "onlyoffice://plugin")


def validate_document(content: bytes, extension: str):
    """Reject HTML error pages, empty exports, and mismatched document containers."""
    if not content or len(content) > MAX_CONTENT:
        raise SaveError("导出文件为空或超过 128 MiB")
    try:
        with zipfile.ZipFile(io.BytesIO(content)) as archive:
            names = set(archive.namelist())
            main = {"docx": "word/document.xml", "xlsx": "xl/workbook.xml",
                    "pptx": "ppt/presentation.xml"}.get(extension)
            if main:
                if main not in names or "[Content_Types].xml" not in names:
                    raise SaveError("导出格式不符合保存目标")
                for name in names:
                    if not name.endswith(".rels"):
                        continue
                    if archive.getinfo(name).file_size > 8 * 1024 * 1024:
                        raise SaveError("文档关系文件过大")
                    base = posixpath.dirname(posixpath.dirname(name))
                    for relation in ET.fromstring(archive.read(name)):
                        if relation.get("TargetMode") == "External":
                            continue
                        target = relation.get("Target", "")
                        resolved = posixpath.normpath(target.lstrip("/") if target.startswith("/") else posixpath.join(base, target))
                        if resolved.startswith("../") or resolved not in names:
                            raise SaveError("导出文件缺少图片或关联内容，已停止保存")
            else:
                types = {"odt": "text", "ods": "spreadsheet", "odp": "presentation"}
                if archive.getinfo("mimetype").file_size > 200:
                    raise SaveError("ODF 文件类型无效")
                if archive.read("mimetype").decode() != "application/vnd.oasis.opendocument." + types[extension]:
                    raise SaveError("ODF 导出格式不匹配")
                if "content.xml" not in names:
                    raise SaveError("ODF 文档缺少内容")
    except (zipfile.BadZipFile, KeyError, UnicodeError, ET.ParseError) as exc:
        raise SaveError("编辑器没有返回完整的办公文档") from exc


@dataclass
class Session:
    plan: SavePlan
    expected: tuple | None
    source: Path | None = None
    last_revision: int = 0
    touched: float = field(default_factory=time.monotonic)
    lock: threading.Lock = field(default_factory=threading.Lock)


class Bridge:
    def __init__(self, settings: Settings):
        settings.validate()
        if len(settings.bridge_token) < 32:
            raise SaveError("请先执行 configure，生成本地服务凭据")
        self.settings = settings
        self.sessions: dict[str, Session] = {}
        self.lock = threading.Lock()

    def create(self, data):
        plan = plan_save(self.settings, data.get("source_path"), data.get("title", "Untitled"),
                         data.get("document_id", ""), data.get("extension", "docx"),
                         data.get("copy_directory"))
        if plan.in_place and data.get("confirm_source") is not True:
            raise SaveError("请确认选择的原文件就是当前打开的文档")
        with self.lock:
            self.prune()
            if any(s.plan.target == plan.target for s in self.sessions.values()):
                raise SaveError("另一文档会话正在保存此目标")
            if len(self.sessions) >= 100:
                raise SaveError("打开的自动保存会话过多")
            key = secrets.token_urlsafe(24)
            self.sessions[key] = Session(plan, fingerprint(plan.target), normalized(data["source_path"]) if data.get("source_path") else None)
        return {"session_id": key, "target": str(plan.target), "in_place": plan.in_place,
                "extension": plan.extension}

    def prune(self):
        now = time.monotonic()
        for key, session in list(self.sessions.items()):
            if now - session.touched > 3600 and not session.lock.locked():
                del self.sessions[key]

    def save(self, key: str, content: bytes, revision: int | None = None):
        with self.lock:
            session = self.sessions.get(key)
            if not session:
                raise SaveError("自动保存会话已结束，请重新开启")
            session.touched = time.monotonic()
        with session.lock:
            if revision is not None and revision <= session.last_revision:
                return {"status": "already_saved_newer", "target": str(session.plan.target)}
            check_source(session.source)
            validate_document(content, session.plan.extension)
            session.expected = atomic_write(self.settings, session.plan, content, session.expected)
            if revision is not None:
                session.last_revision = revision
        return {"status": "saved_local", "target": str(session.plan.target), "cloud_status": "managed_by_twodrive"}

    def save_snapshot(self, key: str, payload: bytes):
        from .converter import convert_snapshot
        with self.lock:
            session = self.sessions.get(key)
            if not session:
                raise SaveError("自动保存会话已结束，请重新开启")
        snapshot = json.loads(payload)
        if not isinstance(snapshot, dict):
            raise SaveError("编辑器快照无效")
        revision = snapshot.get("revision")
        if type(revision) is not int or not 1 <= revision <= 2**53:
            raise SaveError("快照版本号无效")
        source = normalized(snapshot["source_path"]) if snapshot.get("source_path") else None
        if source != session.source:
            raise SaveError("快照与当前保存会话的源文件不一致")
        check_source(source)
        return self.save(key, convert_snapshot(snapshot, session.plan.extension), revision=revision)

    def close(self, key):
        with self.lock:
            session = self.sessions.get(key)
            if session:
                with session.lock:
                    self.sessions.pop(key, None)


class BridgeServer(ThreadingHTTPServer):
    def __init__(self, *args, listener=None, **kwargs):
        self.activity = Activity()
        super().__init__(*args, bind_and_activate=listener is None, **kwargs)
        if listener is not None:
            self.socket.close()
            self.socket = listener
            self.server_address = listener.getsockname()
            self.server_name, self.server_port = "localhost", self.server_address[1]

    def get_request(self):
        connection, address = super().get_request()
        connection.settimeout(30)
        return connection, address

    def process_request(self, request, client_address):
        # Count before spawning the handler so shutdown cannot race an accepted request.
        self.activity.begin()
        try:
            super().process_request(request, client_address)
        except Exception:
            self.activity.end()
            raise

    def process_request_thread(self, request, client_address):
        try:
            super().process_request_thread(request, client_address)
        finally:
            self.activity.end()


def make_server(bridge: Bridge, port: int | None = None, *, listener=None):
    class Handler(BaseHTTPRequestHandler):
        protocol_version = "HTTP/1.1"

        def log_message(self, *args):
            # Do not put document names, tokens or session IDs in server access logs.
            pass

        def valid_origin(self):
            return self.headers.get("Origin") in (None, *PLUGIN_ORIGINS)

        def respond(self, status, data):
            payload = json.dumps(data, ensure_ascii=False).encode()
            self.send_response(status)
            self.send_header("Content-Type", "application/json; charset=utf-8")
            self.send_header("Content-Length", str(len(payload)))
            self.send_header("Cache-Control", "no-store")
            self.send_header("Connection", "close")
            if self.headers.get("Origin") in PLUGIN_ORIGINS:
                self.send_header("Access-Control-Allow-Origin", self.headers["Origin"])
                self.send_header("Vary", "Origin")
            self.end_headers()
            self.wfile.write(payload)
            self.close_connection = True

        def trusted_host(self):
            return self.headers.get("Host") == f"127.0.0.1:{self.server.server_port}"

        def authorized(self):
            expected = "Bearer " + bridge.settings.bridge_token
            allowed = self.valid_origin() and self.trusted_host() and secrets.compare_digest(
                self.headers.get("Authorization", ""), expected)
            if allowed:
                self.server.activity.touch()
            return allowed

        def do_OPTIONS(self):
            if not self.valid_origin() or not self.trusted_host():
                return self.respond(403, {"error": "origin_or_host_rejected"})
            self.send_response(204)
            self.send_header("Access-Control-Allow-Origin", self.headers.get("Origin", "null"))
            self.send_header("Access-Control-Allow-Methods", "GET, POST, DELETE, OPTIONS")
            self.send_header("Access-Control-Allow-Headers", "Authorization, Content-Type")
            self.send_header("Access-Control-Allow-Private-Network", "true")
            self.send_header("Content-Length", "0")
            self.end_headers()

        def do_GET(self):
            if not self.authorized():
                return self.respond(401, {"error": "unauthorized"})
            if self.path != "/settings":
                return self.respond(404, {"error": "not_found"})
            return self.respond(200, {"mount_root": str(bridge.settings.mount_root),
                                      "copy_directory": str(bridge.settings.copy_directory)})

        def read_body(self, limit):
            if self.headers.get("Transfer-Encoding"):
                raise SaveError("不支持分块请求")
            size = int(self.headers.get("Content-Length", "-1"))
            if size < 0 or size > limit:
                raise SaveError("请求大小无效")
            self.connection.settimeout(30)
            data = self.rfile.read(size)
            if len(data) != size:
                raise SaveError("请求内容不完整")
            return data

        def do_POST(self):
            if not self.authorized():
                return self.respond(401, {"error": "unauthorized"})
            try:
                if self.path.startswith("/sessions/") and self.path.endswith("/snapshot"):
                    key = self.path.removeprefix("/sessions/").removesuffix("/snapshot")
                    result = bridge.save_snapshot(key, self.read_body(MAX_CONTENT))
                elif self.path.startswith("/sessions/"):
                    result = bridge.save(self.path.removeprefix("/sessions/"), self.read_body(MAX_CONTENT))
                else:
                    data = json.loads(self.read_body(16384))
                    if not isinstance(data, dict):
                        raise SaveError("请求格式无效")
                    if self.path == "/sessions":
                        result = bridge.create(data)
                    elif self.path == "/pick-directory":
                        result = {"path": pick_path(bridge.settings.copy_directory, True)}
                    elif self.path == "/pick-source":
                        result = {"path": pick_path(bridge.settings.mount_root, False)}
                    else:
                        return self.respond(404, {"error": "not_found"})
                self.respond(200, result)
            except (SaveError, ValueError, OSError, subprocess.SubprocessError) as exc:
                # Return an actionable error without leaking file content or system traces.
                message = str(exc) if isinstance(exc, SaveError) else "本地保存失败，请检查目录权限或服务状态"
                self.respond(409, {"error": message})

        def do_DELETE(self):
            if not self.authorized():
                return self.respond(401, {"error": "unauthorized"})
            if not self.path.startswith("/sessions/"):
                return self.respond(404, {"error": "not_found"})
            bridge.close(self.path.removeprefix("/sessions/"))
            self.respond(200, {"status": "disabled"})

    server = BridgeServer(("127.0.0.1", bridge.settings.bridge_port if port is None else port), Handler, listener=listener)
    server.daemon_threads = True
    return server


def pick_path(start: Path, directory: bool):
    # Called ONLY by an explicit Choose button, never by an autosave timer.
    args = ["zenity", "--file-selection", "--title=TwoDrive：选择副本目录" if directory
            else "--title=TwoDrive：选择当前打开文档的原文件", "--filename=" + str(start) + "/"]
    if directory:
        args.append("--directory")
    result = subprocess.run(args, capture_output=True, text=True, timeout=180)
    if result.returncode == 1:
        return None
    if result.returncode != 0:
        raise SaveError("无法打开目录选择器，可直接输入完整路径")
    return result.stdout.strip() or None

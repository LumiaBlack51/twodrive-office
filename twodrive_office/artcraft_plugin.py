"""Craft plugin protocol 2.2: copy saved disk revisions through TwoDrive.

This plugin never issues editor commands, edits native AutoSave preferences, or
claims to export unsaved document models. It runs only when started by a plugin
host. The current official Craft releases do not yet contain that host.
"""
from __future__ import annotations

import hashlib
import argparse
import io
import json
import os
import re
import secrets
import shutil
import sys
import threading
import time
import zipfile
import xml.etree.ElementTree as ET
from dataclasses import dataclass, replace
from pathlib import Path

from .bridge import MAX_CONTENT, validate_document
from .local_save import LocalSaveQueue, durable_replace, state_directory
from .policy import Settings, SaveError, SavePlan, check_source, fingerprint, normalized, plan_save, within

VERSION = "0.2.0-preview.1"
APPS = {"wordcraft": {"docx", "odt"}, "gridcraft": {"xlsx", "ods"}, "deckcraft": {"pptx", "odp"}}
MAX_LINE = 2 * 1024 * 1024
MAX_DOCUMENTS = 100
SAVED_ONLY = "仅同步编辑器已保存到磁盘的版本；未保存的修改请先使用原生保存。"


def recovery_directory():
    return state_directory().parent / "artcraft-plugin"


def document(data):
    if not isinstance(data, dict):
        raise SaveError("宿主没有提供文档信息")
    key, path = data.get("id"), data.get("path")
    if not isinstance(key, str) or not 1 <= len(key) <= 128 or any(ord(c) < 32 for c in key):
        raise SaveError("需要协议 2.2 的稳定文档标识")
    if path is not None and (not isinstance(path, str) or len(path) > 4096 or "\0" in path or not Path(path).is_absolute()):
        raise SaveError("宿主文档路径无效")
    return {"id": key, "path": path, "dirty": data.get("dirty") is True,
            "name": str(data.get("name", ""))[:256]}


def saved_snapshot(path):
    before = fingerprint(path)
    def identity(stat):
        return (stat.st_dev, stat.st_ino, stat.st_size, stat.st_mtime_ns, stat.st_ctime_ns)
    with path.open("rb") as stream:
        opened = identity(os.fstat(stream.fileno()))
        data = stream.read(MAX_CONTENT + 1)
        finished = identity(os.fstat(stream.fileno()))
    if before is None or not (before == opened == finished == fingerprint(path)):
        raise SaveError("源文件正在保存，稍后重试")
    if len(data) > MAX_CONTENT:
        raise SaveError("文档超过 128 MiB")
    extension = path.suffix.lower().lstrip(".")
    validate_document(data, extension)
    # Check the captured bytes, not a second path read that might see a newer
    # file after an atomic native save. The published package is these bytes.
    with zipfile.ZipFile(io.BytesIO(data)) as archive:
        if any(name.lower().endswith("vbaproject.bin") for name in archive.namelist()):
            raise SaveError("包含宏的已保存版本暂不支持同步")
        if extension in {"odt", "ods", "odp"} and "META-INF/manifest.xml" in archive.namelist():
            if archive.getinfo("META-INF/manifest.xml").file_size > 1024 * 1024:
                raise SaveError("ODF 清单过大，不能确认加密状态")
            try:
                manifest = ET.fromstring(archive.read("META-INF/manifest.xml"))
            except ET.ParseError as exc:
                raise SaveError("ODF 清单无效") from exc
            if any(item.tag.rsplit("}", 1)[-1] == "encryption-data" for item in manifest.iter()):
                raise SaveError("加密 ODF 已保存版本暂不支持同步")
    return data, before


@dataclass
class CopySession:
    info: dict
    source: Path
    plan: SavePlan
    record: object
    directory: Path
    last_source: tuple | None = None
    captured: str = ""
    published: str = ""
    error: str = ""
    closed: bool = False
    final_capture: bool = False
    force: bool = False

    @property
    def manifest(self):
        return self.directory / "revision.json"

    @property
    def blob(self):
        return self.directory / (self.captured + ".document") if self.captured else None

    def capture(self):
        raw = Path(self.info["path"])
        if raw.is_symlink() or normalized(raw) != self.source or not self.source.is_file():
            raise SaveError("源文件已移动、删除或改变链接；已保留本地修订")
        current = fingerprint(self.source)
        if current == self.last_source:
            return
        content, current = saved_snapshot(self.source)
        digest = hashlib.sha256(content).hexdigest()
        if digest == self.captured or (digest == self.published and not self.captured):
            self.last_source = current
            return
        old = self.blob
        self.directory.mkdir(parents=True, mode=0o700, exist_ok=True)
        blob = self.directory / (digest + ".document")
        # Persist first, including while the mount is offline. Never acknowledge
        # or discard this revision merely because it could not enter pending/.
        data = {"schema": 1, "source": str(self.source), "target": str(self.plan.target),
                "extension": self.plan.extension, "expected": self.record.expected,
                "blob": blob.name, "mount_root": str(self.record.settings.mount_root)}
        binding = self.directory / "session.json"
        if not binding.exists():
            durable_replace(binding, json.dumps(data, ensure_ascii=False).encode())
        durable_replace(blob, content)
        durable_replace(self.manifest, json.dumps(data, ensure_ascii=False).encode())
        self.captured = digest
        self.last_source = current
        if old and old != blob:
            old.unlink(missing_ok=True)

    def tick(self):
        capture_error = ""
        if not self.closed or self.final_capture:
            try:
                self.capture()
            except (SaveError, OSError, ValueError) as exc:
                capture_error = str(exc)
            self.final_capture = False
        try:
            self.record.validate()  # Mount, destination and conflicts on every attempt.
            if self.blob and self.captured != self.published:
                queued = self.record.blob
                if queued is None or queued.stem != self.captured:
                    self.record.stage(self.blob.read_bytes())
                self.record.publish(force=self.force or self.closed)
                if self.record.error:
                    raise SaveError(self.record.error)
                if self.record.blob is None:
                    self.published = self.captured
                    self.manifest.unlink(missing_ok=True)
                    self.blob.unlink(missing_ok=True)
                    self.captured = ""
            self.error = capture_error
        except (SaveError, OSError, ValueError) as exc:
            self.error = str(exc)
        self.force = False


class Copies:
    def __init__(self, settings, *, queue_root=None, root=None, clock=time.monotonic):
        settings.validate()
        self.settings, self.clock = settings, clock
        self.root = Path(root) if root is not None else recovery_directory()
        self.root.mkdir(parents=True, exist_ok=True, mode=0o700)
        self.queue = LocalSaveQueue(settings, queue_root or self.root / "pending", clock)
        self.active, self.retired = {}, []
        self.closed = False
        self.lock = threading.RLock()

    def enable(self, info, app, directory, interval):
        if not info["path"]:
            raise SaveError("请先在编辑器保存一次文档，再启用 TwoDrive 同步")
        source = normalized(info["path"])
        extension = source.suffix.lower().lstrip(".")
        if extension not in APPS[app] or Path(info["path"]).is_symlink():
            raise SaveError("此文档格式或链接暂不支持；请使用已保存的 DOCX/XLSX/PPTX 或对应 ODF")
        check_source(source)
        settings = replace(self.settings, sync_interval_seconds=interval)
        # source=None deliberately forces copy mode even for a file on the mount.
        # This plugin never overwrites the editor's source or changes its path.
        plan = plan_save(settings, None, source.stem, str(source), extension, directory)
        if plan.target == source:
            raise SaveError("TwoDrive 副本目标不能是源文件")
        with self.lock:
            if self.closed:
                raise SaveError("插件已关闭")
            previous = self.active.get(info["id"])
            if previous and previous.source == source and previous.plan == plan:
                previous.record.settings = settings
                previous.info = info
                return previous
            if len(self.active) + len(self.retired) >= MAX_DOCUMENTS:
                raise SaveError("TwoDrive 文档会话过多，请先处理保留修订")
            record = self.queue.open(plan, fingerprint(plan.target))
            try:
                for metadata in self.root.glob("*/revision.json"):
                    if json.loads(metadata.read_text()).get("target") == str(plan.target):
                        raise SaveError("此目标仍有保留修订，请先处理恢复数据")
                for binding in self.root.glob("*/session.json"):
                    if (any(binding.parent.glob("*.document"))
                            and json.loads(binding.read_text()).get("target") == str(plan.target)):
                        raise SaveError("此目标仍有保留修订，请先处理恢复数据")
                record.settings = settings
                record.validate()
                if previous:
                    self._retire(info["id"], capture=False)
                state = CopySession(info, source, plan, record, self.root / secrets.token_hex(16))
                self.active[info["id"]] = state
                return state
            except Exception:
                record.release()
                self.queue.records.remove(record)
                raise

    def _retire(self, key, *, capture):
        state = self.active.pop(key, None)
        if state:
            state.closed, state.final_capture = True, capture
            self.retired.append(state)

    def observe(self, info, *, closed=False):
        with self.lock:
            state = self.active.get(info["id"])
            if state and state.info["path"] != info["path"]:
                self._retire(info["id"], capture=False)  # Save As never silently retargets.
            elif state and closed:
                self._retire(info["id"], capture=True)
            elif state:
                state.info = info

    def disable(self, key):
        with self.lock:
            self._retire(key, capture=True)

    def tick(self):
        with self.lock:
            if self.closed:
                return
            for state in [*self.active.values(), *self.retired]:
                state.tick()
                if state.closed and not state.error and state.record.blob is None and not state.manifest.exists():
                    state.record.release()
                    self.queue.records.remove(state.record)
                    self.retired.remove(state)
                    if state.directory.exists():
                        shutil.rmtree(state.directory)

    def status(self, key):
        with self.lock:
            state = self.active.get(key)
            enabled = state is not None
            if state is None:
                state = next((s for s in reversed(self.retired) if s.info["id"] == key), None)
            if state is None:
                return {"enabled": False, "status": "未启用", "saved_versions_only": True}
            pending = state.manifest.exists() or state.record.blob is not None
            label = "需要处理" if state.error else ("已本地暂存，等待提交" if pending else ("已提交挂载目录" if state.published else "等待读取已保存版本"))
            return {"enabled": enabled, "status": label + ("（已暂停）" if not enabled else ""),
                    "error": state.error, "target": str(state.plan.target), "pending": pending,
                    "interval": state.record.settings.sync_interval_seconds, "saved_versions_only": True}

    def pending(self):
        items = []
        with self.lock:
            for path in self.root.glob("*/revision.json"):
                try:
                    data = json.loads(path.read_text())
                    items.append({"id": path.parent.name, "label": Path(data["source"]).name[:256],
                                  "detail": str(data["target"])[:256], "manifest": str(path)})
                except (ValueError, KeyError, OSError):
                    items.append({"id": path.parent.name, "label": "无法识别的保留修订", "detail": "数据已保留，请人工检查"})
            for path in self.root.glob("*/session.json"):
                if not (path.parent / "revision.json").exists() and any(path.parent.glob("*.document")):
                    items.append({"id": path.parent.name, "label": "修订写入中断", "detail": "完整文件已保留，请人工检查",
                                  "manifest": str(path)})
        return items[:1000]

    def shutdown(self):
        with self.lock:
            if self.closed:
                return
            try:
                for key in list(self.active):
                    self._retire(key, capture=True)
                self.tick()
            finally:
                self.closed = True
                self.queue.shutdown()  # Failed revisions remain durable, without an idle daemon.


def export_revision(identifier, destination, *, root=None):
    """Explicit recovery to a NEW local file; never rebase or publish an old queue."""
    if not re.fullmatch(r"[a-f0-9]{32}", identifier):
        raise SaveError("保留修订标识无效")
    directory = (Path(root) if root is not None else recovery_directory()) / identifier
    metadata = json.loads((directory / "revision.json").read_text())
    name, extension = metadata.get("blob", ""), metadata.get("extension", "")
    if not isinstance(name, str) or not re.fullmatch(r"[a-f0-9]{64}\.document", name):
        raise SaveError("保留修订清单无效")
    blob = directory / name
    if directory.is_symlink() or blob.is_symlink():
        raise SaveError("拒绝链接形式的恢复数据")
    with blob.open("rb") as stream:
        content = stream.read(MAX_CONTENT + 1)
    if len(content) > MAX_CONTENT or hashlib.sha256(content).hexdigest() != name[:64]:
        raise SaveError("保留修订校验失败，数据未删除")
    validate_document(content, extension)
    destination = Path(destination).expanduser().absolute()
    if destination.suffix.lower() != "." + extension:
        raise SaveError("恢复文件的扩展名必须与修订格式一致")
    if within(normalized(destination), normalized(metadata["mount_root"])):
        raise SaveError("请先导出到 TwoDrive 挂载目录外的本地新文件")
    # O_EXCL refuses existing files and symlinks, including a concurrent exporter.
    with os.fdopen(os.open(destination, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600), "wb") as stream:
        stream.write(content)
        stream.flush()
        os.fsync(stream.fileno())
    return destination


class Plugin:
    def __init__(self, settings=None, **options):
        self.settings = settings or Settings.load()
        self.options, self.copies = options, None
        self.app = None

    def manager(self):
        if self.copies is None:
            self.copies = Copies(self.settings, **self.options)
        return self.copies

    def handle(self, method, params):
        if not isinstance(params, dict):
            raise SaveError("请求参数必须为对象")
        if method == "initialize":
            if self.app is not None:
                raise SaveError("插件已经初始化")
            host = params.get("host", {})
            if not isinstance(host, dict) or not isinstance(host.get("features"), list):
                raise SaveError("无效的宿主能力信息")
            protocol = host.get("protocol", "")
            if (type(params.get("protocolVersion")) is not int or params["protocolVersion"] != 1
                    or host.get("id") not in APPS
                    or not isinstance(protocol, str) or not re.fullmatch(r"2\.(?:[2-9]|[1-9][0-9]+)(?:\.[0-9]+)?", protocol)
                    or not all(isinstance(feature, str) for feature in host["features"])
                    or not {"dialogs", "document", "events"}.issubset(host["features"])):
                raise SaveError("需要 Craft 插件协议 2.2、文档标识、事件和原生对话框支持")
            self.app = host["id"]
            return {"protocolVersion": 1, "version": VERSION, "savedVersionsOnly": True}
        if self.app is None:
            raise SaveError("插件尚未初始化")
        if method == "shutdown":
            return {}
        if method.startswith("events/"):
            if method not in {"events/documentOpened", "events/documentSaved", "events/documentClosed"}:
                raise KeyError(method)
            info = document(params)
            if self.copies:
                self.copies.observe(info, closed=method == "events/documentClosed")
            return {}  # Never request an editor operation from document events.
        if method != "commands/run":
            raise KeyError(method)
        command = params.get("command")
        args = params.get("params", {})
        if not isinstance(args, dict):
            raise SaveError("命令参数无效")
        if command == "pending":
            return {"dialog": {"title": "TwoDrive 保留修订", "kind": "message",
                               "prompt": json.dumps(self.manager().pending(), ensure_ascii=False)[:3800] + "\n恢复数据不会自动覆盖其他版本。"}}
        if command not in {"settings", "apply", "sync", "pause", "status"}:
            raise KeyError(command)
        context = params.get("context", {})
        info = document(context.get("document") if isinstance(context, dict) else None)
        manager = self.manager()
        manager.observe(info)
        if command == "apply":
            if args.get("documentId") != info["id"] or args.get("sourcePath") != info["path"]:
                raise SaveError("文档已切换或另存，请重新打开 TwoDrive 设置")
            fields = args.get("fields", {})
            if not isinstance(fields, dict) or type(fields.get("enabled")) is not bool:
                raise SaveError("请选择是否启用自动同步")
            if fields["enabled"]:
                directory = fields.get("directory")
                interval = fields.get("interval")
                if not isinstance(directory, str) or not directory or len(directory) > 4096 or "\0" in directory:
                    raise SaveError("请选择有效的 TwoDrive 副本目录")
                if type(interval) not in (int, float) or not 1 <= interval <= 3600:
                    raise SaveError("提交间隔必须为 1–3600 秒")
                manager.enable(info, self.app, directory, interval)
            else:
                manager.disable(info["id"])
        elif command == "pause":
            manager.disable(info["id"])
        elif command == "sync":
            with manager.lock:
                state = manager.active.get(info["id"])
                if state is None:
                    raise SaveError("请先在 TwoDrive 设置中启用同步")
                state.force = True
                state.last_source = None
            return {"message": "已请求同步磁盘上已保存的版本。" + (SAVED_ONLY if info["dirty"] else ""),
                    "result": manager.status(info["id"])}
        status = manager.status(info["id"])
        if command == "settings":
            with manager.lock:
                state = manager.active.get(info["id"])
            return {"dialog": {"kind": "form", "title": "TwoDrive 自动同步",
                               "prompt": SAVED_ONLY + "\n" + status["status"], "command": "apply", "okLabel": "应用",
                               "params": {"documentId": info["id"], "sourcePath": info["path"]},
                               "fields": [{"id": "enabled", "label": "自动同步已保存版本", "kind": "checkbox", "value": status["enabled"]},
                                          {"id": "directory", "label": "TwoDrive 副本目录", "value": str(state.plan.target.parent if state else self.settings.copy_directory)},
                                          {"id": "interval", "label": "提交间隔（秒）", "kind": "number", "value": status.get("interval", self.settings.sync_interval_seconds)}]},
                    "result": status}
        prompt = status["status"] + "\n" + str(status.get("target", "")) + "\n" + str(status.get("error", ""))
        return {"dialog": {"kind": "message", "title": "TwoDrive 状态", "prompt": (prompt + "\n" + SAVED_ONLY)[:4000]},
                "result": status}

    def tick(self):
        if self.copies:
            self.copies.tick()

    def shutdown(self):
        if self.copies:
            self.copies.shutdown()


def serve(stream, output, plugin):
    stop = threading.Event()
    def timer():
        while not stop.wait(1):
            try:
                plugin.tick()
            except (SaveError, OSError, ValueError) as exc:
                print("TwoDrive: " + str(exc), file=sys.stderr)
    worker = threading.Thread(target=timer, name="twodrive-plugin-timer", daemon=True)
    worker.start()
    try:
        while True:
            line = stream.readline(MAX_LINE + 1)
            if not line:
                break
            if len(line) > MAX_LINE or not line.endswith(b"\n"):
                break  # Refuse an unbounded frame; never parse or replay its prefix.
            request, key, code = None, None, -32700
            try:
                request = json.loads(line)
                code = -32600
                if isinstance(request, dict) and "method" not in request and ("result" in request or "error" in request):
                    continue  # Responses are not new host requests.
                if (not isinstance(request, dict) or request.get("jsonrpc") != "2.0"
                        or not isinstance(request.get("method"), str)
                        or type(request.get("id")) not in (str, int, type(None))):
                    raise SaveError("无效的 JSON-RPC 请求")
                key = request.get("id")
                code = -32602
                result = plugin.handle(request["method"], request.get("params", {}))
                response = {"jsonrpc": "2.0", "id": key, "result": result}
            except KeyError:
                response = {"jsonrpc": "2.0", "id": key, "error": {"code": -32601, "message": "未知方法或命令"}}
            except (SaveError, ValueError, OSError, TypeError) as exc:
                response = {"jsonrpc": "2.0", "id": key, "error": {"code": code, "message": str(exc)[:1000]}}
            if code in (-32700, -32600) or "id" in request:
                output.write(json.dumps(response, ensure_ascii=False).encode() + b"\n")
                output.flush()
            if isinstance(request, dict) and request.get("method") == "shutdown":
                break
    finally:
        stop.set()
        worker.join(timeout=2)
        plugin.shutdown()


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--list-revisions", action="store_true")
    parser.add_argument("--export-revision", metavar="ID")
    parser.add_argument("--output", type=Path)
    args = parser.parse_args()
    try:
        if args.list_revisions:
            for metadata in recovery_directory().glob("*/revision.json"):
                print(metadata)
        elif args.export_revision:
            if args.output is None:
                parser.error("--export-revision 需要 --output 本地新文件")
            print(export_revision(args.export_revision, args.output))
        elif args.output:
            parser.error("--output 需要 --export-revision")
        else:
            serve(sys.stdin.buffer, sys.stdout.buffer, Plugin())
    except (SaveError, OSError, ValueError, KeyError, TypeError) as exc:
        raise SystemExit(str(exc))


if __name__ == "__main__":
    main()

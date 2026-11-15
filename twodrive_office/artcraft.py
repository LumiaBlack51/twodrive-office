"""Artcraft native AutoSave companion. Linux, one private working file per launch.

The editors have no loadable Office add-in API. Word/Grid keep their own AutoSave
switch; Deck uses a guarded native Save command. No editor ever saves to the mount.
All control sockets live in a private network namespace shared with this process.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import os
import secrets
import shutil
import socket
import subprocess
import sys
import time
from pathlib import Path

from .bridge import MAX_CONTENT, validate_document
from .local_save import LocalSaveQueue, durable_replace, state_directory
from .policy import Settings, SaveError, check_source, fingerprint, normalized, plan_save, within

FORMATS = {"wordcraft": "docx", "gridcraft": "xlsx", "deckcraft": "pptx"}


def sessions_directory():
    return state_directory().parent / "artcraft"


def private_environment(directory: Path, editor: str):
    env = dict(os.environ)
    # Use absolute Wayland socket paths before changing XDG_RUNTIME_DIR.
    wayland = env.get("WAYLAND_DISPLAY")
    if wayland and not os.path.isabs(wayland) and env.get("XDG_RUNTIME_DIR"):
        env["WAYLAND_DISPLAY"] = str(Path(env["XDG_RUNTIME_DIR"]) / wayland)
    for name, leaf in (("XDG_CONFIG_HOME", "config"), ("XDG_DATA_HOME", "data"),
                       ("XDG_CACHE_HOME", "cache"), ("XDG_STATE_HOME", "state"),
                       ("XDG_RUNTIME_DIR", "run")):
        path = directory / leaf
        path.mkdir(mode=0o700, exist_ok=True)
        env[name] = str(path)
    for name in tuple(env):
        if name.startswith(("WORDCRAFT_", "GRIDCRAFT_", "DECKCRAFT_")):
            env.pop(name)
    env["WORDCRAFT_PROFILE_DIR"] = str(directory / "config/wordcraft")
    env["GRIDCRAFT_CONTROL_TOKEN"] = secrets.token_hex(32)
    if editor == "gridcraft":
        config = directory / "config/gridcraft"
        config.mkdir(mode=0o700, exist_ok=True)
        durable_replace(config / "ui.json", b'{"autosave":true}')
    return env


class Control:
    """Bounded JSON-lines client. A mutation with uncertain delivery is never replayed."""
    def __init__(self, editor, port, env):
        self.editor, self.port, self.env = editor, port, env

    def call(self, method, params=None):
        request = {"id": 1, "method": method, "params": params or {}}
        if self.editor == "wordcraft":
            key = Path(self.env["WORDCRAFT_PROFILE_DIR"]) / f"control-key.local-{self.port}"
            request["key"] = key.read_text().strip()
        with socket.create_connection(("127.0.0.1", self.port), timeout=5) as sock:
            sock.settimeout(15)
            with sock.makefile("rwb") as stream:
                def exchange(data):
                    stream.write(json.dumps(data).encode() + b"\n")
                    stream.flush()
                    line = stream.readline(2 * MAX_CONTENT + 1)
                    if not line.endswith(b"\n") or len(line) > 2 * MAX_CONTENT:
                        raise SaveError("Artcraft 控制响应不完整或过大；不会重放保存命令")
                    reply = json.loads(line)
                    if reply.get("id") != data["id"] or reply.get("ok") is not True:
                        raise SaveError(str(reply.get("error", "Artcraft 控制响应无效")))
                    return reply.get("result")
                if self.editor == "gridcraft":
                    exchange({"id": 0, "method": "auth", "params": {"token": self.env["GRIDCRAFT_CONTROL_TOKEN"]}})
                return exchange(request)

    def execute(self, command, params=None):
        return self.call("engine.execute", {"command": command, "params": params or {}})


class WorkingSession:
    def __init__(self, settings, source, editor, *, copy_directory=None, root=None,
                 queue_root=None, clock=time.monotonic):
        self.settings, self.editor, self.clock = settings, editor, clock
        self.source = normalized(source)
        extension = FORMATS[editor]
        if self.source.suffix.lower() != "." + extension:
            raise SaveError(f"{editor} 当前只验证了 {extension.upper()}，请先另存为该格式")
        check_source(self.source)
        if not self.source.is_file() or self.source.stat().st_size > MAX_CONTENT:
            raise SaveError("源文件不存在或超过 128 MiB")
        content = self.source.read_bytes()
        validate_document(content, extension)
        if not within(self.source, normalized(settings.mount_root)) and copy_directory is None:
            raise SaveError("目录外文档需要显式指定 --copy-directory；原文件保持不变")
        self.plan = plan_save(settings, self.source, self.source.name, "", extension, copy_directory)
        self.queue = LocalSaveQueue(settings, queue_root, clock)
        try:
            self.record = self.queue.open(self.plan, fingerprint(self.plan.target))
        except Exception:
            self.queue.shutdown()
            raise
        self.directory = (root or sessions_directory()) / secrets.token_hex(16)
        self.directory.mkdir(parents=True, mode=0o700)
        self.working = self.directory / self.source.name
        durable_replace(self.working, content)
        self.seen = hashlib.sha256(content).hexdigest()
        self.error = ""
        self.deck_save = True
        self.last_deck_save = clock()
        self.status("准备就绪")

    def status(self, message):
        durable_replace(self.directory / "session.json", json.dumps({
            "editor": self.editor, "source": str(self.source), "target": str(self.plan.target),
            "working": str(self.working), "message": message, "error": self.error,
            "pending": bool(self.record.blob), "deck_autosave": self.deck_save,
            "expected": self.record.expected, "in_place": self.plan.in_place,
        }, ensure_ascii=False, indent=2).encode())

    def capture(self):
        """Only native file saves count. Failed mount validation leaves the working file intact."""
        if self.working.is_symlink() or self.working.stat().st_size > MAX_CONTENT:
            raise SaveError("本地工作文件被替换或过大")
        before = fingerprint(self.working)
        content = self.working.read_bytes()
        if before != fingerprint(self.working):
            return  # A native save is still replacing/writing the file.
        digest = hashlib.sha256(content).hexdigest()
        if digest == self.seen:
            return
        validate_document(content, self.plan.extension)
        self.record.stage(content)  # Revalidates mount, target, conflicts on every revision.
        self.seen = digest

    def tick(self):
        try:
            self.capture()
            self.record.publish()
            self.error = self.record.error
        except (SaveError, OSError, ValueError) as exc:
            self.error = str(exc)
        self.status("已保存本地待同步版本" if self.record.blob else "本地保存监测中")

    def deck_tick(self, control):
        """Deck has AutoRecover only. Save only our sole document, without a dialog."""
        if not self.deck_save or self.clock() - self.last_deck_save < 3:
            return
        self.last_deck_save = self.clock()
        state = control.call("ui.inspect")
        docs = state.get("documents", [])
        if (state.get("dialog") is not None or len(docs) != 1
                or docs[0].get("path") != str(self.working) or not docs[0].get("dirty")):
            return
        # An explicit path prevents any Save As dialog; this path is private local storage.
        control.execute("file.save", {"path": str(self.working), "format": "pptx"})

    def finish(self, *, clean_exit):
        try:
            self.capture()
            self.error = ""
        except (SaveError, OSError, ValueError) as exc:
            self.error = str(exc)
        try:
            self.record.publish(force=True)
        except (SaveError, OSError) as exc:
            self.error = str(exc)
        # Never remove an un-staged/offline save, failed publication, or crash recovery data.
        removable = clean_exit and not self.error and not self.record.blob
        self.status("已提交 TwoDrive 挂载目录" if removable else "本地工作副本已保留，请恢复")
        self.queue.shutdown()
        if removable:
            shutil.rmtree(self.directory)
        else:
            # Keep user recovery material, not caches or diagnostic logs.
            shutil.rmtree(self.directory / "cache", ignore_errors=True)
            (self.directory / "editor.log").unlink(missing_ok=True)
            for editor in FORMATS:
                shutil.rmtree(self.directory / "config" / editor / "logs", ignore_errors=True)
        return removable


def verify_namespace():
    """Fail closed: Deck's unauthenticated port must not be reachable on the host."""
    host = os.environ.get("TWODRIVE_ARTCRAFT_HOST_NETNS", "")
    interfaces = [line.split(":", 1)[0].strip() for line in Path("/proc/self/net/dev").read_text().splitlines()[2:]]
    if not host.startswith("net:[") or os.readlink("/proc/self/ns/net") == host or interfaces != ["lo"]:
        raise SaveError("Artcraft 需要独立网络命名空间；请通过已安装的启动器运行")


def artcraft_running(proc=Path("/proc")):
    for process in proc.iterdir():
        if not process.name.isdigit():
            continue
        try:
            if process.stat().st_uid != os.getuid():
                continue
            # This is a conservative recovery gate, never a claim about a window/document.
            status = (process / "stat").read_text().rsplit(")", 1)[1].split()[0]
            if status != "Z" and (process / "comm").read_text().strip() in FORMATS:
                return True
        except (FileNotFoundError, PermissionError, ProcessLookupError):
            continue
    return False


def recover_session(directory, settings):
    """Explicit recovery of retained native saves; refuse while any Artcraft editor lives."""
    from .policy import SavePlan
    if artcraft_running():
        raise SaveError("请先正常关闭所有 Artcraft 编辑器，再恢复本地工作副本")
    data = json.loads((directory / "session.json").read_text())
    working = Path(data["working"])
    if working.parent != directory or working.is_symlink():
        raise SaveError("恢复文件路径无效")
    extension = FORMATS[data["editor"]]
    if working.stat().st_size > MAX_CONTENT:
        raise SaveError("恢复文件过大")
    content = working.read_bytes()
    validate_document(content, extension)
    plan = SavePlan(Path(data["target"]), data["in_place"], extension)
    expected = tuple(data["expected"]) if data["expected"] is not None else None
    queue = LocalSaveQueue(settings)
    try:
        record = next((r for r in queue.records if r.plan == plan), None)
        if record is None:
            record = queue.open(plan, expected)
        record.stage(content)
        record.publish(force=True)
    finally:
        queue.shutdown()
    shutil.rmtree(directory)


def run_session(session, executable, *, on_ready=None):
    verify_namespace()
    with socket.socket() as listener:
        listener.bind(("127.0.0.1", 0))
        port = listener.getsockname()[1]
    env = private_environment(session.directory, session.editor)
    control = Control(session.editor, port, env)
    process = None
    clean = False
    try:
        with (session.directory / "editor.log").open("wb") as log:
            process = subprocess.Popen([str(executable), "--control", str(port), str(session.working)],
                                       env=env, stdout=log, stderr=log, start_new_session=True)
            deadline = time.monotonic() + 45
            while True:
                if process.poll() is not None:
                    raise SaveError("Artcraft 启动失败；工作副本已保留")
                try:
                    state = control.call("ui.inspect")
                    break
                except (OSError, SaveError, ValueError):
                    if time.monotonic() > deadline:
                        raise SaveError("Artcraft 控制通道未就绪；工作副本已保留")
                    time.sleep(0.25)
            if session.editor == "wordcraft":
                docs = state.get("documents", [])
                if len(docs) != 1 or docs[0].get("path") != str(session.working):
                    raise SaveError("WordCraft 未打开指定的私有工作文件")
                # Native WordCraft requires an explicit save in this session before AutoSave.
                control.execute("file.save", {"path": str(session.working)})
                control.execute("file.autosave", {"value": True})
                # Initialization alone must never rewrite an unchanged source on the mount.
                session.seen = hashlib.sha256(session.working.read_bytes()).hexdigest()
            elif session.editor == "gridcraft" and state.get("ui", {}).get("autosave") is not True:
                raise SaveError("GridCraft 原生 AutoSave 未开启；版本不兼容")
            if on_ready:
                on_ready(control, process)
            while process.poll() is None:
                command = session.directory / "command.json"
                try:
                    if command.exists():
                        request = json.loads(command.read_text())
                        command.unlink()
                        session.deck_save = request.get("deck_autosave", session.deck_save)
                        if request.get("flush"):
                            session.tick()
                            session.record.publish(force=True)
                    if session.editor == "deckcraft":
                        session.deck_tick(control)
                except (SaveError, OSError, ValueError) as exc:
                    session.error = str(exc)
                    session.status("本地工作副本已保留；保存请求未确认")
                session.tick()
                time.sleep(0.5)
            clean = process.returncode == 0
            result = process.returncode
    finally:
        # On errors do not kill the editor or delete its open files. No global process cleanup.
        if process is not None and process.poll() is None:
            session.error = "编辑器仍在运行；工作副本已保留。正常关闭后再恢复。"
            session.status(session.error)
            session.queue.shutdown()
        else:
            finished = session.finish(clean_exit=clean)
    return result if result else (0 if finished else 2)


def main(argv=None):
    parser = argparse.ArgumentParser(description="Artcraft TwoDrive AutoSave companion (Linux)")
    sub = parser.add_subparsers(dest="action", required=True)
    launch = sub.add_parser("open")
    launch.add_argument("editor", choices=FORMATS)
    launch.add_argument("source", type=Path)
    launch.add_argument("--copy-directory", type=Path)
    launch.add_argument("--executable", type=Path)
    sub.add_parser("sessions")
    for action in ("pause", "resume", "flush", "recover"):
        command = sub.add_parser(action)
        command.add_argument("session")
    options = parser.parse_args(argv)
    try:
        if options.action == "sessions":
            for path in sessions_directory().glob("*/session.json"):
                print(path.parent.name, path.read_text())
            return 0
        if options.action in ("pause", "resume", "flush", "recover"):
            if len(options.session) != 32 or any(c not in "0123456789abcdef" for c in options.session):
                raise SaveError("会话标识无效")
            directory = sessions_directory() / options.session
            if options.action == "recover":
                recover_session(directory, Settings.load())
                print("已恢复并提交 TwoDrive 挂载目录")
                return 0
            data = {"flush": True} if options.action == "flush" else {"deck_autosave": options.action == "resume"}
            durable_replace(directory / "command.json", json.dumps(data).encode())
            return 0
        executable = options.executable or shutil.which(options.editor)
        if not executable or not Path(executable).is_file():
            raise SaveError(f"请等待 {options.editor} 安装完成后重试")
        verify_namespace()  # Do not create a work copy before isolation is established.
        settings = Settings.load()
        session = WorkingSession(settings, options.source, options.editor, copy_directory=options.copy_directory)
        print(f"TwoDrive 会话 {session.directory.name}：{session.plan.target}", flush=True)
        return run_session(session, executable)
    except (SaveError, OSError, ValueError) as exc:
        print(str(exc), file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())

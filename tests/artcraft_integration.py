#!/usr/bin/env python3
"""Real native AutoSave on temporary documents, private profiles and authenticated Xvfb."""
import argparse
import hashlib
import json
import os
import secrets
import shutil
import signal
import subprocess
import sys
import tempfile
import threading
import time
from dataclasses import replace
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from twodrive_office.artcraft import WorkingSession, run_session
from twodrive_office.policy import Settings, fingerprint


def until(test, seconds=25):
    deadline = time.monotonic() + seconds
    while time.monotonic() < deadline:
        if test():
            return
        time.sleep(0.25)
    raise AssertionError("timed out waiting for native editor state")


def close_document(control, editor, working, process):
    if editor == "gridcraft":
        windows = control.call("window.list")
        doc = next(w["doc"] for w in windows if w["active"])
        result = control.execute("file.close", {"document": True})
        try:
            until(lambda: all(w["doc"] != doc for w in control.call("window.list")))
        except OSError:
            # GridCraft's acknowledged last-document close also closes the native window.
            assert process.wait(timeout=5) == 0, result
            return
    else:
        control.execute("file.close")
        # WordCraft may create a new blank document after closing its last file.
        until(lambda: all(d.get("path") != str(working) for d in control.call("ui.inspect").get("documents", [])))


def worker(root):
    mount = root / "mock-mount"
    mount.mkdir()
    settings = Settings(mount, mount / "Copies", require_mount=False, sync_interval_seconds=3600)
    results = []
    for editor, ext in (("wordcraft", "docx"), ("gridcraft", "xlsx"), ("deckcraft", "pptx")):
        executable = shutil.which(editor)
        cli = shutil.which(editor + "-cli")
        source = mount / (editor + "." + ext)
        create = {
            "wordcraft": ["run", "--cmd", 'text.insert={"text":"Original fixture"}', "--save", str(source)],
            "gridcraft": ["run", "--cmd", 'cell.set={"cell":"A1","input":"Original fixture"}', "--out", str(source)],
            "deckcraft": ["run", "--cmd", 'slide.new={"title":"Original fixture"}', "--save", str(source)],
        }[editor]
        subprocess.run([cli, *create], check=True, capture_output=True)
        before = source.read_bytes()
        version = subprocess.check_output([cli, "version" if editor == "gridcraft" else "--version"], text=True).strip()
        s = WorkingSession(settings, source, editor, root=root / "sessions", queue_root=root / "pending")
        errors, threads = [], []
        marker = "TwoDrive native autosave " + editor

        def ready(control, process):
            actual = dict(item.split(b"=", 1) for item in Path(f"/proc/{process.pid}/environ").read_bytes().split(b"\0") if b"=" in item)
            assert actual[b"DISPLAY"] == os.environ["DISPLAY"].encode()
            assert actual[b"XDG_CONFIG_HOME"] == str(s.directory / "config").encode()
            assert actual[b"XDG_DATA_HOME"] == str(s.directory / "data").encode()
            assert os.readlink(f"/proc/{process.pid}/ns/net") == os.readlink("/proc/self/ns/net")
            assert os.readlink("/proc/self/ns/net") != os.environ["TWODRIVE_ARTCRAFT_HOST_NETNS"]

            def drive():
                try:
                    if editor == "wordcraft":
                        control.execute("caret.docEnd")
                        control.execute("text.insert", {"text": marker})
                    elif editor == "gridcraft":
                        control.execute("cell.set", {"cell": "B2", "input": marker})
                    else:
                        control.execute("slide.new", {"title": marker})
                    until(lambda: marker.encode() in native_content(s.working))
                    until(lambda: s.record.blob is not None)
                    assert source.read_bytes() == before, "native Save must not reach the mount directly"
                    if editor == "wordcraft":
                        control.execute("file.autosave", {"value": False})
                        snapshot = s.working.read_bytes()
                        control.execute("text.insert", {"text": " paused"})
                        time.sleep(4)
                        assert s.working.read_bytes() == snapshot, "native pause must stop autosaving"
                        control.execute("file.autosave", {"value": True})
                        until(lambda: b" paused" in native_content(s.working))
                        until(lambda: not control.call("ui.inspect").get("dirty"))
                    close_document(control, editor, s.working, process)
                    if process.poll() is None:
                        control.call("app.quit")
                except BaseException as exc:
                    errors.append(exc)
                    # Dedicated test group only. Failure cleanup is never reported as natural exit.
                    os.killpg(process.pid, signal.SIGTERM)
            thread = threading.Thread(target=drive)
            threads.append(thread)
            thread.start()

        code = run_session(s, executable, on_ready=ready)
        for thread in threads:
            thread.join(3)
        if errors:
            raise errors[0]
        assert code == 0, (editor, code)
        assert marker.encode() in native_content(source)
        assert not s.directory.exists(), "clean, published session must be removed"
        # Opening/closing unchanged must not publish WordCraft's initialization reserialization.
        before_stat = fingerprint(source)
        s2 = WorkingSession(settings, source, editor, root=root / "sessions", queue_root=root / "pending")
        def close_unchanged(control, process):
            close_document(control, editor, s2.working, process)
            if process.poll() is None:
                control.call("app.quit")
        assert run_session(s2, executable, on_ready=close_unchanged) == 0
        assert fingerprint(source) == before_stat
        results.append({"editor": editor, "version": version, "native_save": True,
                        "unchanged_no_write": True, "closed_documents": True, "natural_exit": True})
        print(json.dumps(results[-1]), flush=True)
    (root / "results.json").write_text(json.dumps(results, indent=2))


def native_content(path):
    import zipfile
    try:
        with zipfile.ZipFile(path) as archive:
            return b"".join(archive.read(name) for name in archive.namelist() if name.endswith(".xml"))
    except (OSError, zipfile.BadZipFile):
        return b""


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--xvfb", default=shutil.which("Xvfb"))
    parser.add_argument("--worker", type=Path)
    args = parser.parse_args()
    if args.worker:
        worker(args.worker)
        return
    if not args.xvfb:
        parser.error("--xvfb is required; the normal display is never used")
    with tempfile.TemporaryDirectory(prefix="twodrive-artcraft-test-") as name:
        root = Path(name)
        number = next(n for n in range(220, 600) if not Path(f"/tmp/.X{n}-lock").exists())
        display = f":{number}"
        auth = root / "Xauthority"
        auth.touch(mode=0o600)
        subprocess.run(["xauth", "-f", str(auth), "add", display, ".", secrets.token_hex(16)], check=True)
        env = dict(os.environ, DISPLAY=display, XAUTHORITY=str(auth), LIBGL_ALWAYS_SOFTWARE="1",
                   PYTHONDONTWRITEBYTECODE="1", PYTHONPATH=str(ROOT),
                   TWODRIVE_ARTCRAFT_HOST_NETNS=os.readlink("/proc/self/ns/net"))
        for key in ("WAYLAND_DISPLAY", "DBUS_SESSION_BUS_ADDRESS", "SESSION_MANAGER"):
            env.pop(key, None)
        with (root / "xvfb.log").open("wb") as log:
            xvfb = subprocess.Popen([args.xvfb, display, "-screen", "0", "1280x900x24", "-nolisten", "tcp", "-auth", str(auth)],
                                    stdout=log, stderr=log, start_new_session=True)
            try:
                until(lambda: subprocess.run(["xdpyinfo"], env=env, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL).returncode == 0, 10)
                command = ["bwrap", "--die-with-parent", "--unshare-net", "--bind", "/", "/", "--proc", "/proc",
                           "--dev-bind", "/dev", "/dev", "--", sys.executable, "-B", str(Path(__file__).resolve()), "--worker", str(root)]
                completed = subprocess.run(command, env=env, timeout=180)
                if completed.returncode:
                    for path in (root / "sessions").glob("*/editor.log"):
                        print(path.read_text(errors="replace")[-4000:], file=sys.stderr)
                    raise SystemExit(completed.returncode)
            finally:
                # Xvfb is test infrastructure: explicitly terminated, never called an editor exit.
                os.killpg(xvfb.pid, signal.SIGTERM)
                xvfb.wait(timeout=10)
                # Only processes whose explicit cmdline contains this unique temporary root.
                for entry in Path("/proc").iterdir():
                    if not entry.name.isdigit():
                        continue
                    try:
                        args = (entry / "cmdline").read_bytes().split(b"\0")
                        if any(arg.startswith((str(root) + "/").encode()) for arg in args):
                            if int(entry.name) != os.getpid():
                                raise AssertionError(f"dedicated test residue: {entry.name}")
                    except (FileNotFoundError, ProcessLookupError):
                        pass


if __name__ == "__main__":
    main()

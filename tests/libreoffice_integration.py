#!/usr/bin/python3
"""Real UNO/headless tests with a unique profile, pipe, config and temporary documents."""
import importlib.util
import os
import signal
import subprocess
import sys
import tempfile
import time
import uuid
import zipfile
from pathlib import Path
from unittest.mock import patch

import uno
import unohelper
from com.sun.star.frame import XStatusListener

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from twodrive_office import __version__
from twodrive_office.policy import Settings


def prop(name, value):
    result = uno.createUnoStruct("com.sun.star.beans.PropertyValue")
    result.Name, result.Value = name, value
    return result


def wait_until(predicate, message, timeout=15):
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        if predicate():
            return
        time.sleep(0.1)
    raise AssertionError(message)


def contains(path, text):
    if not path.exists():
        return False
    try:
        with zipfile.ZipFile(path) as archive:
            return text.encode() in archive.read("content.xml")
    except (zipfile.BadZipFile, KeyError):
        return False


def main():
    with tempfile.TemporaryDirectory(prefix="twodrive-office-test-") as directory:
        temp = Path(directory)
        config_root = temp / "config"
        mount = temp / "mount"
        mount.mkdir()
        settings = Settings(mount, mount / "Office AutoSave", debounce_seconds=0.25,
                            max_wait_seconds=1, require_mount=False)
        settings.save(config_root / "twodrive-office/settings.json")
        profile = temp / "lo-profile"
        env = {**os.environ, "XDG_CONFIG_HOME": str(config_root), "SAL_USE_VCLPLUGIN": "svp", "TWODRIVE_OFFICE_DEBUG": "1"}
        package = ROOT / "dist" / f"twodrive-autosave-{__version__}.oxt"
        subprocess.run(["unopkg", "-env:UserInstallation=" + profile.as_uri(), "add", "--force", str(package)],
                       env=env, check=True, capture_output=True, timeout=30)
        print("PASS extension registered in isolated profile", flush=True)
        pipe = "twodrive_office_" + uuid.uuid4().hex
        with (temp / "soffice.log").open("w") as log:
            process = subprocess.Popen([
                "nice", "-n", "10", "libreoffice", "-env:UserInstallation=" + profile.as_uri(),
                "--headless", "--norestore", "--nodefault", "--nofirststartwizard", "--nologo",
                "--accept=pipe,name=" + pipe + ";urp;StarOffice.ServiceManager",
            ], env=env, stdout=log, stderr=log, start_new_session=True)
            desktop = None
            try:
                local = uno.getComponentContext()
                resolver = local.ServiceManager.createInstanceWithContext("com.sun.star.bridge.UnoUrlResolver", local)
                context = None
                deadline = time.monotonic() + 15
                while time.monotonic() < deadline:
                    try:
                        context = resolver.resolve("uno:pipe,name=" + pipe + ";urp;StarOffice.ComponentContext")
                        break
                    except Exception:
                        time.sleep(0.1)
                assert context is not None, "isolated headless office did not start"
                desktop = context.ServiceManager.createInstanceWithContext("com.sun.star.frame.Desktop", context)
                extension = context.ServiceManager.createInstanceWithContext("org.twodrive.office.AutoSave", context)
                assert extension is not None, "packaged Python UNO component failed to load"
                print("PASS installed UNO component loads", flush=True)
                doc = desktop.loadComponentFromURL("private:factory/swriter", "_blank", 0, (prop("Hidden", True),))
                source = mount / "inplace.odt"
                doc.Text.String = "initial"
                doc.storeAsURL(source.as_uri(), (prop("FilterName", "writer8"),))
                doc.dispose()
                untouched = (source.read_bytes(), source.stat().st_mtime_ns)
                doc = desktop.loadComponentFromURL(source.as_uri(), "_blank", 0, (prop("Hidden", True),))
                extension.initialize((doc.getCurrentController().getFrame(),))
                extension.trigger("startup")
                url = uno.createUnoStruct("com.sun.star.util.URL")
                url.Complete, url.Protocol, url.Path = "twodrive:Toggle", "twodrive:", "Toggle"
                dispatcher = extension.queryDispatch(url, "", 0)
                assert dispatcher is not None
                class Status(unohelper.Base, XStatusListener):
                    latest = None
                    def statusChanged(self, event):
                        self.latest = event
                    def disposing(self, event):
                        pass
                status = Status()
                dispatcher.addStatusListener(status, url)
                if not status.latest or not status.latest.State:
                    print((temp / "soffice.log").read_text(), flush=True)
                assert status.latest and status.latest.State, "mount document did not auto-enable: " + str(status.latest)
                wait_until(lambda: not doc.isModified(), "initial native autosave failed")
                time.sleep(0.6)
                assert (source.read_bytes(), source.stat().st_mtime_ns) == untouched, "opening an unchanged document rewrote it"
                doc.Text.String = "automatic-inplace-test"
                try:
                    wait_until(lambda: contains(source, "automatic-inplace-test") and not doc.isModified(),
                               "installed extension did not autosave a real edit")
                except AssertionError:
                    print("Save status:", status.latest.FeatureDescriptor, flush=True)
                    print((temp / "soffice.log").read_text(), flush=True)
                    raise
                print("PASS automatic enable + real modify event + UI callback + in-place save", flush=True)
                dispatcher.dispatch(url, ())
                doc.Text.String = "disabled-edit"
                time.sleep(1.5)
                assert not contains(source, "disabled-edit"), "disabled autosave wrote changes"
                print("PASS disabled document is not saved", flush=True)
                doc.store()
                assert status.latest.State is False, "a manual save re-enabled the paused document"
                doc.Text.String = "still-disabled-after-manual-save"
                time.sleep(0.6)
                assert not contains(source, "still-disabled-after-manual-save")
                print("PASS manual pause survives native save events", flush=True)

                # Exercise external/untitled export adapter without invoking a foreground FolderPicker.
                spec = importlib.util.spec_from_file_location("autosave_integration", ROOT / "libreoffice/AutoSave.py")
                module = importlib.util.module_from_spec(spec)
                spec.loader.exec_module(module)
                class TestManager:
                    def __init__(self):
                        self.settings, self.states = settings, []
                    def refresh(self):
                        pass
                test_manager = TestManager()
                outside = temp / "outside.odt"
                copy_doc = desktop.loadComponentFromURL("private:factory/swriter", "_blank", 0, (prop("Hidden", True),))
                copy_doc.Text.String = "original-outside"
                copy_doc.storeAsURL(outside.as_uri(), (prop("FilterName", "writer8"),))
                extension.initialize((copy_doc.getCurrentController().getFrame(),))
                dispatcher.addStatusListener(status, url)
                assert status.latest.State is False, "an outside document auto-enabled"
                before = outside.read_bytes()
                with patch.dict(os.environ, {"XDG_CONFIG_HOME": str(config_root)}):
                    state = module.DocumentState(test_manager, copy_doc)
                    state.enable(mount / "Chosen folder")
                    copy_doc.Text.String = "exported-current-edits"
                    state.save()
                    assert not state.error, state.error
                    assert contains(state.plan.target, "exported-current-edits")
                    assert outside.read_bytes() == before
                    assert copy_doc.getURL() == outside.as_uri()
                    assert copy_doc.isModified(), "export must preserve original modified flag"
                    print("PASS chosen copy folder saves current edits, preserves source bytes, URL and modified flag", flush=True)
                    state.plan.target.write_bytes(b"external change")
                    copy_doc.Text.String = "must-not-overwrite"
                    state.save()
                    assert state.error and state.plan.target.read_bytes() == b"external change"
                    print("PASS external copy conflict is preserved", flush=True)
                    state.enabled = False
                    copy_doc.dispose()

                    blank = desktop.loadComponentFromURL("private:factory/swriter", "_blank", 0, (prop("Hidden", True),))
                    extension.initialize((blank.getCurrentController().getFrame(),))
                    dispatcher.addStatusListener(status, url)
                    assert status.latest.State is False, "an untitled document auto-enabled"
                    blank.Text.String = "untitled-current-edits"
                    state = module.DocumentState(test_manager, blank)
                    state.enable()
                    state.save()
                    assert not state.error, state.error
                    assert contains(state.plan.target, "untitled-current-edits")
                    assert blank.getURL() == "" and blank.isModified()
                    print("PASS untitled document exports without changing identity", flush=True)
                    state.enabled = False
                    blank.dispose()
                    for factory, extension_name, filter_name in (
                        ("scalc", "xlsx", "Calc MS Excel 2007 XML"),
                        ("simpress", "pptx", "Impress MS PowerPoint 2007 XML"),
                    ):
                        other = desktop.loadComponentFromURL("private:factory/" + factory, "_blank", 0, (prop("Hidden", True),))
                        if factory == "scalc":
                            cell = other.Sheets.getByIndex(0).getCellRangeByName("A1")
                            cell.String = "original"
                            def edit(value):
                                cell.String = value
                        else:
                            shape = other.createInstance("com.sun.star.drawing.TextShape")
                            size = uno.createUnoStruct("com.sun.star.awt.Size")
                            size.Width, size.Height = 16000, 2000
                            shape.Size = size
                            other.getDrawPages().getByIndex(0).add(shape)
                            shape.String = "original"
                            def edit(value):
                                shape.String = value
                                # UNO TextShape assignment does not mark the headless Impress model;
                                # GUI text editing does. Explicitly model that user edit here.
                                other.setModified(True)
                        original = temp / ("outside." + extension_name)
                        other.storeAsURL(original.as_uri(), (prop("FilterName", filter_name),))
                        original_bytes = original.read_bytes()
                        state = module.DocumentState(test_manager, other)
                        state.enable()
                        edit("current-" + extension_name + "-edits")
                        before_modified = other.isModified()
                        state.save()
                        assert not state.error, state.error
                        with zipfile.ZipFile(state.plan.target) as archive:
                            assert any(("current-" + extension_name + "-edits").encode() in archive.read(name)
                                       for name in archive.namelist() if name.endswith(".xml"))
                        assert original.read_bytes() == original_bytes
                        assert other.getURL() == original.as_uri() and other.isModified(), (
                            factory, "modified before save", before_modified,
                            "modified after save", other.isModified(), "source URL preserved", other.getURL() == original.as_uri())
                        print("PASS real " + factory + " current edits export as " + extension_name + ", preserving the original", flush=True)
                        state.enabled = False
                        other.dispose()
                doc.dispose()
            finally:
                if desktop:
                    try:
                        desktop.terminate()
                    except Exception:
                        pass
                try:
                    process.wait(timeout=10)
                except subprocess.TimeoutExpired:
                    os.killpg(process.pid, signal.SIGTERM)
                    try:
                        process.wait(timeout=5)
                    except subprocess.TimeoutExpired:
                        os.killpg(process.pid, signal.SIGKILL)
                        process.wait(timeout=5)
        print("All test state removed; normal profiles and real TwoDrive mount untouched.", flush=True)


if __name__ == "__main__":
    main()

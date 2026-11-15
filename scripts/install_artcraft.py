#!/usr/bin/env python3
"""Install separate Artcraft companions; never starts or alters an existing editor."""
import argparse
import os
import shlex
import shutil
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


def install(*, home=None, data_home=None, executables=None):
    home = Path(home or Path.home())
    data = Path(data_home or os.environ.get("XDG_DATA_HOME", home / ".local/share"))
    runtime = home / ".local/lib/twodrive-artcraft"
    binary = home / ".local/bin"
    applications = data / "applications"
    if not shutil.which("bwrap"):
        raise RuntimeError("需要 bubblewrap (bwrap)，用于隔离 Artcraft 控制端口")
    found = executables or {name: shutil.which(name) for name in ("wordcraft", "gridcraft", "deckcraft")}
    found = {name: str(Path(path).resolve()) for name, path in found.items() if path and Path(path).is_file()}
    if not found:
        raise RuntimeError("Artcraft 尚未安装就绪；代码已准备好，可在安装完成后再次运行本脚本")
    runtime.mkdir(parents=True, exist_ok=True)
    package = runtime / "twodrive_office"
    package.mkdir(exist_ok=True)
    # Only this companion's runtime; no dependency on uncommitted Wine changes.
    for name in ("__init__.py", "artcraft.py", "policy.py", "local_save.py", "bridge.py", "lifecycle.py"):
        shutil.copy2(ROOT / "twodrive_office" / name, package / name)
    binary.mkdir(parents=True, exist_ok=True)
    applications.mkdir(parents=True, exist_ok=True)
    python = shlex.quote(sys.executable)
    command = (f"exec bwrap --die-with-parent --unshare-net --bind / / --proc /proc --dev-bind /dev /dev "
               f"-- {python} -B -m twodrive_office.artcraft \"$@\"\n")
    manager = binary / "twodrive-artcraft"
    manager.write_text("#!/bin/sh\n" + f"cd {shlex.quote(str(runtime))} || exit 1\n"
                       + 'TWODRIVE_ARTCRAFT_HOST_NETNS=$(readlink /proc/self/ns/net) || exit 1\n'
                       + 'export TWODRIVE_ARTCRAFT_HOST_NETNS\n' + command)
    manager.chmod(0o755)
    installed = []
    for name, executable in found.items():
        launcher = binary / ("twodrive-" + name)
        launcher.write_text("#!/bin/sh\n" + f"exec {shlex.quote(str(manager))} open {name} --executable {shlex.quote(executable)} \"$@\"\n")
        launcher.chmod(0o755)
        extension = {"wordcraft": "docx", "gridcraft": "xlsx", "deckcraft": "pptx"}[name]
        mime = {"docx": "wordprocessingml.document", "xlsx": "spreadsheetml.sheet", "pptx": "presentationml.presentation"}[extension]
        title = {"wordcraft": "WordCraft", "gridcraft": "GridCraft", "deckcraft": "DeckCraft"}[name]
        desktop = applications / f"org.twodrive.{name}.desktop"
        escaped = str(launcher).replace("\\", "\\\\").replace('"', '\\"').replace("`", "\\`").replace("$", "\\$").replace("%", "%%")
        desktop.write_text(f"[Desktop Entry]\nType=Application\nName={title} — TwoDrive AutoSave\n"
                           f"Exec=\"{escaped}\" %f\nTerminal=false\nCategories=Office;\n"
                           f"Icon=ai.storyteller.{name}\nMimeType=application/vnd.openxmlformats-officedocument.{mime};\n")
        installed.append(str(launcher))
    return installed


if __name__ == "__main__":
    argparse.ArgumentParser(description=__doc__).parse_args()
    try:
        for path in install():
            print(path)
        print("安装完成；没有启动编辑器。请从新增的 TwoDrive AutoSave 入口打开文档。")
    except (OSError, RuntimeError) as exc:
        raise SystemExit(str(exc))

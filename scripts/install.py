#!/usr/bin/env python3
"""Install for the next office launch without opening or changing any document."""
import argparse
import json
import os
import shutil
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from twodrive_office.policy import Settings, config_path
from build import build


def onlyoffice_directories(data_root: Path, override: Path | None = None):
    if override is not None:
        return [override]
    root = data_root / "onlyoffice/desktopeditors"
    guid = "{F2C67A12-E94D-4EF6-8C32-20D71F3B0294}"
    # Native 9.4 and 10.0 enumerate different user roots. Installing the same
    # GUID into both layouts works before the first launch and after upgrades;
    # each native version reads its own registry, without launching an editor.
    return [root / "sdkjs-plugins" / guid, root / "data/sdkjs-plugins" / guid]


def main():
    parser = argparse.ArgumentParser(description="Install TwoDrive AutoSave without opening office windows")
    parser.add_argument("--libreoffice", action="store_true")
    parser.add_argument("--onlyoffice", action="store_true")
    parser.add_argument("--bridge", action="store_true", help="Install socket-activated bridge; no resident process")
    parser.add_argument("--lo-profile", type=Path, help="Use an isolated test profile instead of the normal profile")
    parser.add_argument("--oo-directory", type=Path, help="Override ONLYOFFICE user plugin directory")
    parser.add_argument("--oo-flatpak", action="store_true", help="Install into the Flatpak user plugin directory")
    args = parser.parse_args()
    if not any((args.libreoffice, args.onlyoffice, args.bridge)):
        parser.error("choose --libreoffice, --onlyoffice and/or --bridge")
    lo, _ = build()
    settings = Settings.load()
    if args.onlyoffice or args.bridge:
        settings = settings.save()
    if args.libreoffice:
        command = ["unopkg"]
        if args.lo_profile:
            command.append("-env:UserInstallation=" + args.lo_profile.resolve().as_uri())
        command += ["add", "--force", str(lo)]
        subprocess.run(command, check=True)
        print("LibreOffice extension installed; TwoDrive documents auto-enable on their next open.")
    if args.onlyoffice:
        data_root = Path(os.environ.get("XDG_DATA_HOME", str(Path.home() / ".local/share")))
        flatpak_installed = subprocess.run(["flatpak", "info", "org.onlyoffice.desktopeditors"],
            stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL).returncode == 0 if shutil.which("flatpak") else False
        if args.oo_flatpak or (flatpak_installed and not shutil.which("onlyoffice-desktopeditors")):
            data_root = Path.home() / ".var/app/org.onlyoffice.desktopeditors/data"
        for directory in onlyoffice_directories(data_root, args.oo_directory):
            directory.mkdir(parents=True, exist_ok=True)
            shutil.copytree(ROOT / "onlyoffice", directory, dirs_exist_ok=True)
            shutil.copyfile(ROOT / "LICENSE", directory / "LICENSE")
            path = directory / "settings.js"
            path.write_text("window.TWODRIVE_BRIDGE = " + json.dumps({
                "url": f"http://127.0.0.1:{settings.bridge_port}", "token": settings.bridge_token}) + ";\n")
            path.chmod(0o600)
            print("ONLYOFFICE plugin installed:", directory)
    if args.bridge:
        from bridge_install import install_bridge
        install_bridge(settings)


if __name__ == "__main__":
    main()

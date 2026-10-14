#!/usr/bin/env python3
"""Install for the next office launch. Never launches an editor or enables AutoSave."""
import argparse
import json
import os
import shutil
import subprocess
import sys
import time
import urllib.error
import urllib.request
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from twodrive_office.policy import Settings, config_path
from build import build


def main():
    parser = argparse.ArgumentParser(description="Install TwoDrive AutoSave without opening office windows")
    parser.add_argument("--libreoffice", action="store_true")
    parser.add_argument("--onlyoffice", action="store_true")
    parser.add_argument("--bridge", action="store_true", help="Install and start user bridge service")
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
        print("LibreOffice extension installed; AutoSave remains off until selected.")
    if args.onlyoffice:
        data_root = Path(os.environ.get("XDG_DATA_HOME", str(Path.home() / ".local/share")))
        flatpak_installed = subprocess.run(["flatpak", "info", "org.onlyoffice.desktopeditors"],
            stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL).returncode == 0 if shutil.which("flatpak") else False
        if args.oo_flatpak or (flatpak_installed and not shutil.which("onlyoffice-desktopeditors")):
            data_root = Path.home() / ".var/app/org.onlyoffice.desktopeditors/data"
        directory = args.oo_directory or data_root / "onlyoffice/desktopeditors/sdkjs-plugins/{F2C67A12-E94D-4EF6-8C32-20D71F3B0294}"
        directory.mkdir(parents=True, exist_ok=True)
        shutil.copytree(ROOT / "onlyoffice", directory, dirs_exist_ok=True)
        shutil.copyfile(ROOT / "LICENSE", directory / "LICENSE")
        path = directory / "settings.js"
        path.write_text("window.TWODRIVE_BRIDGE = " + json.dumps({
            "url": f"http://127.0.0.1:{settings.bridge_port}", "token": settings.bridge_token}) + ";\n")
        path.chmod(0o600)
        print("ONLYOFFICE plugin installed:", directory)
    if args.bridge:
        # Copy the runtime out of the development workspace, so moving source doesn't break the service.
        runtime = Path.home() / ".local/lib/twodrive-office"
        shutil.copytree(ROOT / "twodrive_office", runtime / "twodrive_office", dirs_exist_ok=True,
                        ignore=shutil.ignore_patterns("__pycache__"))
        shutil.copyfile(ROOT / "LICENSE", runtime / "LICENSE")
        user_units = Path(os.environ.get("XDG_CONFIG_HOME", str(Path.home() / ".config"))) / "systemd/user"
        user_units.mkdir(parents=True, exist_ok=True)
        unit = "\n".join([
            "[Unit]", "Description=TwoDrive Office local save bridge", "After=graphical-session.target", "",
            "[Service]", "Type=simple", "ExecStart=/usr/bin/python3 -m twodrive_office.cli serve",
            "WorkingDirectory=%h/.local/lib/twodrive-office", "Restart=on-failure", "RestartSec=5",
            "Nice=10", "IOSchedulingClass=best-effort", "IOSchedulingPriority=7", "UMask=0077", "",
            "[Install]", "WantedBy=default.target", "",
        ])
        unit_path = user_units / "twodrive-office-bridge.service"
        unit_path.write_text(unit)
        subprocess.run(["systemd-analyze", "--user", "verify", str(unit_path)], check=True)
        subprocess.run(["systemctl", "--user", "daemon-reload"], check=True)
        subprocess.run(["systemctl", "--user", "enable", "--now", "twodrive-office-bridge.service"], check=True)
        request = urllib.request.Request(f"http://127.0.0.1:{settings.bridge_port}/settings",
            headers={"Authorization": "Bearer " + settings.bridge_token})
        deadline = time.monotonic() + 10
        while True:
            try:
                with urllib.request.urlopen(request, timeout=1) as response:
                    actual = json.load(response)
                if actual != {"mount_root": str(settings.mount_root), "copy_directory": str(settings.copy_directory)}:
                    raise RuntimeError("Bridge returned settings different from the installed configuration")
                break
            except (urllib.error.URLError, OSError):
                if time.monotonic() >= deadline:
                    raise RuntimeError("Bridge did not become ready; check systemctl --user status twodrive-office-bridge.service") from None
                time.sleep(0.1)
        subprocess.run(["systemctl", "--user", "is-active", "--quiet", "twodrive-office-bridge.service"], check=True)
        print("Authenticated bridge running on loopback. No documents are saved until enabled in the plugin.")


if __name__ == "__main__":
    main()

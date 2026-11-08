"""Shared socket-activation installer. Never launches an office editor."""
import os
import shutil
import subprocess
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]

def install_bridge(settings):
    # Copy the runtime out of the development workspace, so moving source doesn't break the service.
    runtime = Path.home() / ".local/lib/twodrive-office"
    shutil.copytree(ROOT / "twodrive_office", runtime / "twodrive_office", dirs_exist_ok=True,
                    ignore=shutil.ignore_patterns("__pycache__"))
    shutil.copyfile(ROOT / "LICENSE", runtime / "LICENSE")
    user_units = Path(os.environ.get("XDG_CONFIG_HOME", str(Path.home() / ".config"))) / "systemd/user"
    user_units.mkdir(parents=True, exist_ok=True)
    unit_path = user_units / "twodrive-office-bridge.service"
    if unit_path.exists():
        # Remove the legacy login-started service before handing its port to systemd.
        subprocess.run(["systemctl", "--user", "disable", "--now", "twodrive-office-bridge.service"], check=True)
    unit = "\n".join([
        "[Unit]", "Description=TwoDrive Office local save bridge (on demand)",
        "Requires=twodrive-office-bridge.socket", "After=twodrive-office-bridge.socket", "",
        "[Service]", "Type=simple", "ExecStart=/usr/bin/python3 -m twodrive_office.cli serve --idle-timeout 30",
        "WorkingDirectory=%h/.local/lib/twodrive-office", "Restart=on-failure", "RestartSec=5",
        "Nice=10", "IOSchedulingClass=best-effort", "IOSchedulingPriority=7", "UMask=0077", "",
    ])
    unit_path.write_text(unit)
    socket_path = user_units / "twodrive-office-bridge.socket"
    socket_path.write_text("\n".join([
        "[Unit]", "Description=TwoDrive Office bridge activation socket", "",
        "[Socket]", f"ListenStream=127.0.0.1:{settings.bridge_port}", "Accept=no", "NoDelay=true", "",
        "[Install]", "WantedBy=sockets.target", "",
    ]))
    subprocess.run(["systemd-analyze", "--user", "verify", str(unit_path), str(socket_path)], check=True)
    subprocess.run(["systemctl", "--user", "daemon-reload"], check=True)
    subprocess.run(["systemctl", "--user", "enable", "twodrive-office-bridge.socket"], check=True)
    subprocess.run(["systemctl", "--user", "restart", "twodrive-office-bridge.socket"], check=True)
    subprocess.run(["systemctl", "--user", "is-active", "--quiet", "twodrive-office-bridge.socket"], check=True)
    print("Bridge configured on demand. Python starts only on connection; no editor is launched.")

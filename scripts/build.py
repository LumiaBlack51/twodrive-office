#!/usr/bin/env python3
"""Build installable archives without changing any office profile."""
import json
import hashlib
import urllib.request
import zipfile
from pathlib import Path
import xml.etree.ElementTree as ET

ROOT = Path(__file__).resolve().parents[1]
SDK_URL = "https://onlyoffice.github.io/sdkjs-plugins/v1/plugins.js"


def build():
    dist = ROOT / "dist"
    dist.mkdir(exist_ok=True)
    vendor = ROOT / "onlyoffice/vendor/plugins.js"
    if not vendor.exists():
        vendor.parent.mkdir(parents=True, exist_ok=True)
        with urllib.request.urlopen(SDK_URL, timeout=30) as response:
            data = response.read()
        if b"startPluginApi" not in data:
            raise RuntimeError("ONLYOFFICE SDK download is invalid")
        vendor.write_bytes(data)
    for path in (ROOT / "libreoffice").rglob("*"):
        if path.suffix in (".xcu", ".xml"):
            ET.parse(path)
    json.loads((ROOT / "onlyoffice/config.json").read_text())
    lo = dist / "twodrive-autosave-0.1.0.oxt"
    with zipfile.ZipFile(lo, "w", zipfile.ZIP_DEFLATED) as archive:
        archive.write(ROOT / "LICENSE", "LICENSE")
        for base in (ROOT / "libreoffice", ROOT / "twodrive_office"):
            for path in sorted(base.rglob("*")):
                if not path.is_file() or "__pycache__" in path.parts:
                    continue
                name = path.relative_to(ROOT / "libreoffice") if base.name == "libreoffice" else path.relative_to(ROOT)
                archive.write(path, str(name))
    oo = dist / "twodrive-autosave-0.1.0.plugin"
    with zipfile.ZipFile(oo, "w", zipfile.ZIP_DEFLATED) as archive:
        archive.write(ROOT / "LICENSE", "LICENSE")
        for path in sorted((ROOT / "onlyoffice").rglob("*")):
            if path.is_file():
                archive.write(path, str(path.relative_to(ROOT / "onlyoffice")))
    (dist / "SHA256SUMS").write_text("".join(
        hashlib.sha256(path.read_bytes()).hexdigest() + "  " + path.name + "\n"
        for path in (lo, oo)), encoding="utf-8")
    for path in (lo, oo):
        print(path)
    return lo, oo


if __name__ == "__main__":
    build()

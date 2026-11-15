#!/usr/bin/env python3
"""Build a deterministic ordinary Craft plugin ZIP (not an Office rebuild)."""
import hashlib
import json
import zipfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


def build(root=ROOT):
    dist = root / "dist"
    dist.mkdir(exist_ok=True)
    version = json.loads((root / "artcraft/plugin.json").read_text())["version"]
    target = dist / f"twodrive-artcraft-plugin-{version}-linux.zip"
    files = {name: name for name in ("LICENSE", "docs/artcraft.md", "docs/verification.md", "scripts/install_artcraft.py")}
    files.update({f"artcraft/{name}": name for name in ("plugin.json", "main.py", "protocol-sources.json")})
    files.update({f"twodrive_office/{name}.py": f"twodrive_office/{name}.py"
                  for name in ("__init__", "artcraft_plugin", "policy", "local_save", "bridge", "lifecycle")})
    with zipfile.ZipFile(target, "w", zipfile.ZIP_DEFLATED) as archive:
        for source, name in sorted(files.items(), key=lambda item: item[1]):
            info = zipfile.ZipInfo(name, date_time=(2026, 11, 15, 0, 0, 0))
            info.compress_type = zipfile.ZIP_DEFLATED
            info.external_attr = 0o100644 << 16
            archive.writestr(info, (root / source).read_bytes())
    target.with_suffix(".sha256").write_text(hashlib.sha256(target.read_bytes()).hexdigest() + "  " + target.name + "\n")
    return target


if __name__ == "__main__":
    print(build())

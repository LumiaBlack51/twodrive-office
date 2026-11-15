#!/usr/bin/env python3
"""Build the standalone Artcraft companion from committed-compatible core files."""
import hashlib
import zipfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


def build(root=ROOT):
    dist = root / "dist"
    dist.mkdir(exist_ok=True)
    target = dist / "twodrive-artcraft-0.1.1-linux.zip"
    files = ["LICENSE", "docs/artcraft.md", "docs/verification.md", "scripts/install_artcraft.py", "scripts/build_artcraft.py"]
    files += [f"twodrive_office/{name}.py" for name in ("__init__", "artcraft", "policy", "local_save", "bridge", "lifecycle")]
    with zipfile.ZipFile(target, "w", zipfile.ZIP_DEFLATED) as archive:
        for name in files:
            archive.write(root / name, name)
    target.with_suffix(".sha256").write_text(hashlib.sha256(target.read_bytes()).hexdigest() + "  " + target.name + "\n")
    return target


if __name__ == "__main__":
    print(build())

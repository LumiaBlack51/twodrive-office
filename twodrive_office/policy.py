from __future__ import annotations

import hashlib
import json
import os
import re
import secrets
import tempfile
import zipfile
import xml.etree.ElementTree as ET
from dataclasses import dataclass
from pathlib import Path


class SaveError(Exception):
    """A save was refused or failed; the caller must keep changes pending."""


def config_path() -> Path:
    base = Path(os.environ.get("XDG_CONFIG_HOME", str(Path.home() / ".config")))
    return base / "twodrive-office" / "settings.json"


def normalized(path: str | Path) -> Path:
    return Path(path).expanduser().resolve()


def within(path: Path, root: Path) -> bool:
    return path == root or root in path.parents


@dataclass(frozen=True)
class Settings:
    mount_root: Path
    copy_directory: Path
    debounce_seconds: float = 1.0
    max_wait_seconds: float = 5.0
    require_mount: bool = True
    bridge_port: int = 19836
    bridge_token: str = ""

    @classmethod
    def defaults(cls):
        root = normalized(os.environ.get("TWODRIVE_MOUNT_DIR", "~/TwoDrive/OneDrive"))
        return cls(root, root / "Office AutoSave")

    @classmethod
    def load(cls, path: Path | None = None):
        path = path or config_path()
        if not path.exists():
            return cls.defaults()
        data = json.loads(path.read_text(encoding="utf-8"))
        settings = cls(
            normalized(data["mount_root"]), normalized(data["copy_directory"]),
            float(data.get("debounce_seconds", 1)), float(data.get("max_wait_seconds", 5)),
            bool(data.get("require_mount", True)), int(data.get("bridge_port", 19836)),
            data.get("bridge_token", ""),
        )
        settings.validate()
        return settings

    def validate(self):
        if not within(normalized(self.copy_directory), normalized(self.mount_root)):
            raise SaveError("副本目录必须位于 TwoDrive 挂载目录内")
        if not 0.25 <= self.debounce_seconds <= self.max_wait_seconds <= 300:
            raise SaveError("保存延迟必须满足 0.25 ≤ debounce ≤ max_wait ≤ 300 秒")
        if not 1024 <= self.bridge_port <= 65535:
            raise SaveError("本地服务端口无效")

    def check_mount(self):
        self.validate()
        if not self.mount_root.is_dir():
            raise SaveError("TwoDrive 目录不存在，请先启动 TwoDrive")
        if self.require_mount and not os.path.ismount(self.mount_root):
            raise SaveError("TwoDrive 挂载已断开，修改仍待保存")

    def save(self, path: Path | None = None):
        self.validate()
        path = path or config_path()
        data = {
            "mount_root": str(self.mount_root), "copy_directory": str(self.copy_directory),
            "debounce_seconds": self.debounce_seconds, "max_wait_seconds": self.max_wait_seconds,
            "require_mount": self.require_mount, "bridge_port": self.bridge_port,
            "bridge_token": self.bridge_token or secrets.token_urlsafe(32),
        }
        path.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
        fd, temporary = tempfile.mkstemp(dir=path.parent, prefix=".settings-")
        try:
            with os.fdopen(fd, "w", encoding="utf-8") as stream:
                json.dump(data, stream, ensure_ascii=False, indent=2)
                stream.write("\n")
                stream.flush()
                os.fsync(stream.fileno())
            os.replace(temporary, path)
        finally:
            if os.path.exists(temporary):
                os.unlink(temporary)
        return self.load(path)


EDITABLE_FORMATS = {"docx", "xlsx", "pptx", "odt", "ods", "odp"}


def check_source(source: Path | None):
    if source is None:
        return
    if source.suffix.lower().lstrip(".") not in EDITABLE_FORMATS:
        raise SaveError("源文件格式暂不支持自动保存，请使用 DOCX/XLSX/PPTX 或 ODF")
    if not source.is_file():
        return
    with source.open("rb") as stream:
        if stream.read(8) == bytes.fromhex("d0cf11e0a1b11ae1"):
            raise SaveError("加密文档暂不支持自动保存，请手动保存")
    try:
        with zipfile.ZipFile(source) as archive:
            if any(name.lower().endswith("vbaproject.bin") for name in archive.namelist()):
                raise SaveError("包含宏的文档暂不支持自动保存，请手动保存")
            if source.suffix.lower() in (".odt", ".ods", ".odp") and "META-INF/manifest.xml" in archive.namelist():
                if archive.getinfo("META-INF/manifest.xml").file_size > 1024 * 1024:
                    raise SaveError("ODF 清单过大，不能确认加密状态")
                manifest = ET.fromstring(archive.read("META-INF/manifest.xml"))
                if any(element.tag.rsplit("}", 1)[-1] == "encryption-data" for element in manifest.iter()):
                    raise SaveError("加密 ODF 文档暂不支持自动保存，请手动保存")
    except (zipfile.BadZipFile, ET.ParseError) as exc:
        raise SaveError("源文件不是完整的办公文档，请先手动保存") from exc


@dataclass(frozen=True)
class SavePlan:
    target: Path
    in_place: bool
    extension: str


def plan_save(settings: Settings, source: str | Path | None, title: str,
              document_id: str, extension: str, copy_directory: str | Path | None = None) -> SavePlan:
    settings.check_mount()
    extension = extension.lower().lstrip(".")
    if extension not in EDITABLE_FORMATS:
        raise SaveError("此格式暂不支持自动保存，请使用 DOCX/XLSX/PPTX 或 ODF")
    source_path = normalized(source) if source else None
    check_source(source_path)
    root = normalized(settings.mount_root)
    if source_path and within(source_path, root):
        if source_path.suffix.lower() != "." + extension:
            raise SaveError("导出格式与原文件不同，不能原位覆盖")
        if not source_path.is_file():
            raise SaveError("原文件不存在，请先正常保存或另存副本")
        if not os.access(source_path, os.W_OK):
            raise SaveError("原文件只读，不能自动覆盖")
        return SavePlan(source_path, True, extension)
    directory = normalized(copy_directory or settings.copy_directory)
    if not within(directory, root):
        raise SaveError("请选择 TwoDrive 挂载目录内的副本目录")
    name = source_path.stem if source_path else Path(title or "Untitled").stem
    name = re.sub(r'[\x00-\x1f<>:"/\\|?*]', "_", name).strip(" .") or "Untitled"
    # Stable per source, unique per untitled document; never overwrite another source's namesake.
    identity = str(source_path) if source_path else document_id
    if not identity:
        raise SaveError("未命名文档缺少唯一标识")
    digest = hashlib.sha256(identity.encode()).hexdigest()[:12]
    target = directory / f"{name[:120]}--{digest}.{extension}"
    if target.is_symlink():
        raise SaveError("副本目标是符号链接，保存已停止")
    return SavePlan(target, False, extension)


def prepare_target(settings: Settings, plan: SavePlan):
    """Revalidate on EVERY save, including mount and symlink changes since enabling."""
    settings.check_mount()
    root = normalized(settings.mount_root)
    if not within(normalized(plan.target), root) or plan.target.is_symlink():
        raise SaveError("保存目标已移动到 TwoDrive 外或变成符号链接")
    if plan.in_place and not plan.target.is_file():
        raise SaveError("原文件已移动或删除，保存已停止")
    plan.target.parent.mkdir(parents=True, exist_ok=True)


def fingerprint(path: Path):
    if not path.exists():
        return None
    stat = path.stat()
    return (stat.st_dev, stat.st_ino, stat.st_size, stat.st_mtime_ns, stat.st_ctime_ns)


def atomic_write(settings: Settings, plan: SavePlan, content: bytes, expected):
    prepare_target(settings, plan)
    if fingerprint(plan.target) != expected:
        raise SaveError("目标文件被其他程序修改，请检查后重新开启自动保存")
    fd, temporary = tempfile.mkstemp(dir=plan.target.parent, prefix=".twodrive-office-", suffix=".tmp")
    try:
        if expected is not None:
            os.fchmod(fd, plan.target.stat().st_mode & 0o777)
        with os.fdopen(fd, "wb") as stream:
            stream.write(content)
            stream.flush()
            os.fsync(stream.fileno())
        prepare_target(settings, plan)
        if fingerprint(plan.target) != expected:
            raise SaveError("保存时检测到目标冲突，原文件已保留")
        os.replace(temporary, plan.target)
    finally:
        if os.path.exists(temporary):
            os.unlink(temporary)
    return fingerprint(plan.target)

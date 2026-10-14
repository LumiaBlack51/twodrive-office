"""Convert a current ONLYOFFICE binary snapshot, including its embedded media."""
import base64
import os
import shutil
import subprocess
import tempfile
from pathlib import Path, PurePosixPath
import xml.etree.ElementTree as ET

from .policy import SaveError

FORMAT_CODES = {"docx": 65, "odt": 67, "xlsx": 257, "ods": 259, "pptx": 129, "odp": 131}
SIGNATURES = {"docx": "DOCY", "odt": "DOCY", "xlsx": "XLSY", "ods": "XLSY", "pptx": "PPTY", "odp": "PPTY"}


def converter_command(parameters: Path):
    configured = os.environ.get("TWODRIVE_ONLYOFFICE_X2T")
    candidates = [Path(configured)] if configured else [
        Path("/opt/onlyoffice/desktopeditors/converter/x2t"),
        Path("/usr/share/onlyoffice/desktopeditors/converter/x2t"),
    ]
    for path in candidates:
        if path.is_file():
            return [str(path), str(parameters)], path.parent, {**os.environ, "LD_LIBRARY_PATH": str(path.parent)}
    if shutil.which("flatpak"):
        installed = subprocess.run(["flatpak", "info", "org.onlyoffice.desktopeditors"],
                                   capture_output=True, timeout=10)
        if installed.returncode == 0:
            # This invokes only x2t; no Qt/CEF editor, GUI, profile, or single-instance settings.
            script = 'cd /app/bin/opt/onlyoffice/desktopeditors/converter; export LD_LIBRARY_PATH=.:..; exec ./x2t "$1"'
            return ["flatpak", "run", "--command=sh", "org.onlyoffice.desktopeditors", "-c", script, "--", str(parameters)], None, None
    raise SaveError("找不到 ONLYOFFICE 转换器，请安装 Desktop Editors 或设置 TWODRIVE_ONLYOFFICE_X2T")


def convert_snapshot(snapshot: dict, extension: str) -> bytes:
    binary = snapshot.get("binary", "")
    if not isinstance(binary, str) or not binary.startswith(SIGNATURES[extension] + ";"):
        raise SaveError("当前编辑器的文档类型与保存格式不一致")
    media = snapshot.get("media", {})
    if not isinstance(media, dict) or len(media) > 5000:
        raise SaveError("文档媒体列表无效或过大")
    with tempfile.TemporaryDirectory(prefix="twodrive-office-convert-") as temporary:
        root = Path(temporary)
        (root / "temp").mkdir()
        (root / "Editor.bin").write_text(binary, encoding="ascii")
        (root / "media").mkdir()
        for key, value in media.items():
            path = PurePosixPath(key)
            if path.is_absolute() or ".." in path.parts or "\\" in key:
                raise SaveError("文档媒体路径无效")
            if path.parts and path.parts[0] == "media":
                path = PurePosixPath(*path.parts[1:])
            if len(path.parts) != 1 or not path.name:
                raise SaveError("不支持此文档媒体路径")
            try:
                content = base64.b64decode(value, validate=True)
            except (ValueError, TypeError) as exc:
                raise SaveError("文档媒体内容不完整") from exc
            (root / "media" / path.name).write_bytes(content)
        result = root / ("result." + extension)
        task = ET.Element("TaskQueueDataConvert")
        fields = {"m_sFileFrom": str(root / "Editor.bin"), "m_sFileTo": str(result),
                  "m_nFormatTo": str(FORMAT_CODES[extension]), "m_bFromChanges": "false",
                  "m_bDontSaveAdditional": "true", "m_sTempDir": str(root / "temp")}
        for name, value in fields.items():
            ET.SubElement(task, name).text = value
        parameters = root / "parameters.xml"
        ET.ElementTree(task).write(parameters, encoding="utf-8", xml_declaration=True)
        command, cwd, env = converter_command(parameters)
        completed = subprocess.run(command, cwd=cwd, env=env, capture_output=True, timeout=45)
        if completed.returncode != 0 or not result.is_file():
            if os.environ.get("TWODRIVE_OFFICE_DEBUG") == "1":
                print("Converter failed:", completed.returncode, completed.stderr.decode(errors="replace")[-1000:], flush=True)
            raise SaveError(f"ONLYOFFICE 当前内容转换失败（{completed.returncode}），修改仍待保存")
        return result.read_bytes()

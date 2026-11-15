#!/usr/bin/env python3
"""Read-only Craft host check. Installation is deferred until official support."""
import json
import os
import shutil
import subprocess
import tempfile
from pathlib import Path

APPS = ("wordcraft", "gridcraft", "deckcraft")


def check(executables=None):
    results = {}
    with tempfile.TemporaryDirectory(prefix="twodrive-host-check-") as temporary:
        env = dict(os.environ)
        for key in ("DISPLAY", "WAYLAND_DISPLAY"):
            env.pop(key, None)
        for key in ("XDG_CONFIG_HOME", "XDG_DATA_HOME", "XDG_STATE_HOME", "XDG_CACHE_HOME"):
            env[key] = str(Path(temporary) / key.lower())
        for app in APPS:
            executable = (executables or {}).get(app) if executables is not None else shutil.which(app + "-cli")
            if not executable:
                results[app] = {"host_commands": False, "reason": "CLI 未安装"}
                continue
            try:
                run = subprocess.run([str(executable), "commands"], env=env, capture_output=True,
                                     text=True, timeout=15, check=True)
                names = {line.split()[0] for line in run.stdout.splitlines() if line.strip()}
                present = {"plugins.load", "plugins.list"}.issubset(names)
                results[app] = {"host_commands": present, "reason": (
                    "检测到插件命令；仍须核对官方发布的协议 2.2 和功能支持" if present else "当前版本没有公共插件宿主命令")}
            except (OSError, subprocess.SubprocessError) as exc:
                results[app] = {"host_commands": False, "reason": str(exc)[:256]}
    return results


if __name__ == "__main__":
    print(json.dumps(check(), ensure_ascii=False, indent=2))
    print("本脚本只读检查，不安装插件，不创建额外启动器。请等待官方支持后从应用内安装。")

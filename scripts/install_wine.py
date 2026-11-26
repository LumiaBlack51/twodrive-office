#!/usr/bin/env python3
"""Install into one explicit, stopped Wine prefix. Does not run Wine or Office."""
from __future__ import annotations
import argparse
import os
from pathlib import Path
import shutil
import struct
import sys
import tempfile

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from twodrive_office.policy import Settings, SaveError

CLSID = '{C53A8F6A-D977-4D1F-A832-6C40203D6419}'
PROGID = 'TwoDrive.Office.WineAutoSave'


def prefix_processes(prefix, proc=Path('/proc')):
    """Report unreleased Wine processes, without claiming a window is open."""
    prefix = str(prefix.resolve())
    result = []
    for process in proc.glob('[0-9]*'):
        try:
            if process.stat().st_uid != os.getuid():
                continue
            env = dict(item.split(b'=', 1) for item in (process / 'environ').read_bytes().split(b'\0') if b'=' in item)
            value = env.get(b'WINEPREFIX', b'').decode()
            if value and str(Path(value).resolve()) == prefix:
                state = (process / 'stat').read_text().rsplit(') ', 1)[1].split()[0]
                if state not in ('Z', 'X'):
                    result.append({'pid': int(process.name), 'name': (process / 'comm').read_text().strip(),
                                   'state': state})
        except (OSError, UnicodeError):
            pass
    return result


def prefix_running(prefix):
    return bool(prefix_processes(prefix))


def reg_string(value):
    return '"' + value.replace('\\', '\\\\').replace('"', '\\"') + '"'


def registry_sections(windows_directory):
    base = 'Software\\Classes\\'
    result = {
        base + PROGID: {'@': reg_string('TwoDrive Wine AutoSave')},
        base + PROGID + '\\CLSID': {'@': reg_string(CLSID)},
        base + 'CLSID\\' + CLSID: {'@': reg_string('TwoDrive Wine AutoSave')},
        base + 'CLSID\\' + CLSID + '\\InprocServer32': {
            '@': reg_string(windows_directory + '\\TwoDrive.Wine.dll'), '"ThreadingModel"': '"Apartment"'},
        base + 'CLSID\\' + CLSID + '\\ProgID': {'@': reg_string(PROGID)},
    }
    for host in ('Word', 'Excel', 'PowerPoint'):
        result[f'Software\\Microsoft\\Office\\{host}\\Addins\\{PROGID}'] = {
            '"FriendlyName"': '"TwoDrive AutoSave"',
            '"Description"': '"Local durable snapshots and guarded TwoDrive saving"',
            '"LoadBehavior"': 'dword:00000003',
        }
        # Office C2R/AppV can retain a disconnected value in its virtual
        # machine registry, overriding the ordinary HKLM registration.
        result[f'Software\\Microsoft\\Office\\ClickToRun\\REGISTRY\\Machine\\Software\\Microsoft\\Office\\{host}\\Addins\\{PROGID}'] = {
            '"LoadBehavior"': 'dword:00000003',
        }
    return result


def patch_registry(text, sections, uninstall=False):
    """Only own sections are replaced; no import subprocess or global profile."""
    lines = text.splitlines(keepends=True)
    output, i = [], 0
    encoded = {key.replace('\\', '\\\\').casefold() for key in sections}
    while i < len(lines):
        line = lines[i]
        if line.startswith('[') and ']' in line and line[1:line.index(']')].casefold() in encoded:
            i += 1
            while i < len(lines) and not lines[i].startswith('['):
                i += 1
        else:
            output.append(line)
            i += 1
    if not uninstall:
        output.append('\n')
        for key, values in sections.items():
            output.append('[' + key.replace('\\', '\\\\') + ']\n')
            output.extend(name + '=' + value + '\n' for name, value in values.items())
            output.append('\n')
    return ''.join(output)


def install(prefix, dll, settings, *, uninstall=False, sandbox_wakeup=False):
    prefix = prefix.expanduser().resolve()
    registry = prefix / 'system.reg'
    if not registry.is_file() or not (prefix / 'user.reg').is_file() or not (prefix / 'drive_c').is_dir():
        raise SaveError('不是已有 Wine prefix')
    if prefix_running(prefix):
        raise SaveError('此 bottle 的 Wine/Office 进程尚未释放，可能是退出残留；不能据此认定窗口仍打开。请先停止该 bottle 再安装')
    if registry.is_symlink():
        raise SaveError('拒绝符号链接注册表')
    users = prefix / 'drive_c/users'
    candidates = [p for p in users.iterdir() if p.name.lower() not in ('public', 'default', 'default user') and not p.is_symlink() and (p / 'AppData').is_dir()]
    if len(candidates) != 1:
        raise SaveError('Wine 用户目录不唯一，不能推断安装目标')
    destination = candidates[0] / 'AppData/Local/TwoDrive-Office-Wine'
    # Refuse a path which follows an inherited link outside the chosen prefix.
    if not destination.resolve().is_relative_to(prefix):
        raise SaveError('插件目录指向所选 Wine prefix 外部')
    if any((destination / name).is_symlink() for name in ('TwoDrive.Wine.dll', 'TwoDrive.Wine.dll.previous', 'bridge.ini', 'LICENSE', 'relay.request')):
        raise SaveError('插件文件是符号链接，安装已停止')
    windows_directory = 'C:\\' + str(destination.relative_to(prefix / 'drive_c')).replace('/', '\\')
    sections = registry_sections(windows_directory)
    original = registry.read_bytes()
    text = original.decode('utf-8')
    if not text.startswith('WINE REGISTRY Version 2'):
        raise SaveError('未知 Wine 注册表格式')
    updated = patch_registry(text, sections, uninstall)
    if not uninstall:
        if len(settings.bridge_token) < 32 or '\n' in settings.bridge_token or '\r' in settings.bridge_token:
            raise SaveError('请先 configure 生成 bridge 凭据')
        binary = Path(dll).read_bytes()
        offset = struct.unpack_from('<I', binary, 0x3c)[0]
        if binary[:2] != b'MZ' or binary[offset:offset+4] != b'PE\0\0' or struct.unpack_from('<H', binary, offset+4)[0] != 0x8664:
            raise SaveError('需要 x64 原生 COM DLL')
        destination.mkdir(parents=True, exist_ok=True, mode=0o700)
        # Keep exactly the previous program/own keys for rollback, never Office data.
        if (destination / 'TwoDrive.Wine.dll').exists():
            shutil.copyfile(destination / 'TwoDrive.Wine.dll', destination / 'TwoDrive.Wine.dll.previous')
        (destination / 'TwoDrive.Wine.dll').write_bytes(binary)
        (destination / 'LICENSE').write_bytes((ROOT / 'LICENSE').read_bytes())
        config = destination / 'bridge.ini'
        config.write_text(f'[bridge]\nport={settings.bridge_port}\ntoken={settings.bridge_token}\nprefix={prefix}\n'
                          + ('sandbox_wakeup=1\n' if sandbox_wakeup else ''), encoding='utf-16')
        config.chmod(0o600)
    fd, name = tempfile.mkstemp(prefix='.twodrive-reg-', dir=prefix)
    try:
        with os.fdopen(fd, 'wb') as stream:
            stream.write(updated.encode('utf-8')); stream.flush(); os.fsync(stream.fileno())
        if prefix_running(prefix) or registry.read_bytes() != original:
            raise SaveError('安装时 Wine 状态或注册表发生改变，已停止注册')
        os.chmod(name, registry.stat().st_mode & 0o777)
        os.replace(name, registry)
    finally:
        Path(name).unlink(missing_ok=True)
    return destination


def sandbox_units(prefix, destination, runtime, config=None):
    import hashlib
    name = 'twodrive-wine-' + hashlib.sha256(str(prefix.resolve()).encode()).hexdigest()[:16]
    def escaped(value):
        if any(c in str(value) for c in ('\n', '\r', '\x00')):
            raise SaveError('systemd 路径包含控制字符')
        return '"' + str(value).replace('\\', '\\\\').replace('"', '\\"').replace('%', '%%').replace('$', '$$') + '"'
    def path_value(value):
        if any(c in str(value) for c in ('\n', '\r', '\x00', '\\')):
            raise SaveError('systemd 路径包含不支持的字符')
        return str(value).replace('%', '%%')
    request = destination / 'relay.request'
    service = '\n'.join([
        '[Unit]', 'Description=TwoDrive Wine sandbox relay (on demand)', '',
        '[Service]', 'Type=simple', 'Restart=no', 'UMask=0077', 'Nice=10',
        'Environment=PYTHONDONTWRITEBYTECODE=1',
        'WorkingDirectory=' + path_value(runtime),
        'ExecStart=/usr/bin/python3 -m twodrive_office.wine_namespace --request-prefix ' + escaped(prefix)
        + (' --config ' + escaped(config) if config else ''), '',
    ])
    path = '\n'.join([
        '[Unit]', 'Description=TwoDrive Wine sandbox wakeup', '',
        '[Path]', 'PathExists=' + path_value(request), 'Unit=' + name + '.service', '',
        '[Install]', 'WantedBy=paths.target', '',
    ])
    return name, {name + '.service': service, name + '.path': path}


def install_sandbox_wakeup(prefix, destination):
    import subprocess
    runtime = Path.home() / '.local/lib/twodrive-office'
    units = Path(os.environ.get('XDG_CONFIG_HOME', str(Path.home() / '.config'))) / 'systemd/user'
    name, files = sandbox_units(prefix, destination, runtime)
    units.mkdir(parents=True, exist_ok=True)
    for filename, content in files.items():
        (units / filename).write_text(content)
    subprocess.run(['systemd-analyze', '--user', 'verify', *[str(units / f) for f in files]], check=True)
    subprocess.run(['systemctl', '--user', 'daemon-reload'], check=True)
    subprocess.run(['systemctl', '--user', 'enable', '--now', name + '.path'], check=True)
    return name


def uninstall_sandbox_wakeup(prefix, destination):
    import subprocess
    units = Path(os.environ.get('XDG_CONFIG_HOME', str(Path.home() / '.config'))) / 'systemd/user'
    name, files = sandbox_units(prefix, destination, Path.home() / '.local/lib/twodrive-office')
    if not any((units / filename).exists() for filename in files):
        return
    subprocess.run(['systemctl', '--user', 'disable', '--now', name + '.path'], check=True)
    subprocess.run(['systemctl', '--user', 'stop', name + '.service'], check=True)
    for filename in files:
        (units / filename).unlink(missing_ok=True)
    subprocess.run(['systemctl', '--user', 'daemon-reload'], check=True)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--prefix', type=Path, required=True)
    parser.add_argument('--dll', type=Path, default=(ROOT / 'dist/wine/TwoDrive.Wine.dll' if (ROOT / 'dist/wine/TwoDrive.Wine.dll').exists() else ROOT / 'wine/TwoDrive.Wine.dll'))
    parser.add_argument('--bridge', action='store_true', help='Install the Linux socket-activated bridge')
    parser.add_argument('--sandbox-wakeup', action='store_true', help='Install event-driven Wine sandbox relay activation (requires --bridge)')
    parser.add_argument('--uninstall', action='store_true')
    args = parser.parse_args()
    try:
        settings = Settings.load()
        if args.sandbox_wakeup and (not args.bridge or args.uninstall):
            raise SaveError('--sandbox-wakeup 需要与 --bridge 一起安装')
        if args.bridge and not args.uninstall:
            if prefix_running(args.prefix):
                raise SaveError('Wine/Office 进程尚未释放（可能是退出残留）；请先停止该 bottle')
            if not settings.bridge_token:
                settings = settings.save()
        destination = install(args.prefix, args.dll, settings, uninstall=args.uninstall,
                              sandbox_wakeup=args.sandbox_wakeup)
        if args.uninstall:
            uninstall_sandbox_wakeup(args.prefix, destination)
        if args.bridge and not args.uninstall:
            from bridge_install import install_bridge
            install_bridge(settings)
            if args.sandbox_wakeup:
                install_sandbox_wakeup(args.prefix, destination)
        print('已移除 COM 注册；设置和待同步版本保留' if args.uninstall else '已安装，下次启动 Office 生效：' + str(destination))
    except (SaveError, OSError, ValueError, struct.error) as exc:
        parser.exit(1, str(exc) + '\n')

if __name__ == '__main__':
    main()

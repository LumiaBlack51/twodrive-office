#!/usr/bin/env python3
"""Real Wine Office tests on a copied prefix and authenticated private Xvfb.

No editor is launched on the user's display, and no save uses a real mount.
Only process groups verified to belong to this test prefix may be terminated.
"""
from __future__ import annotations
import argparse
from dataclasses import replace
import hashlib
import json
import os
from pathlib import Path
import shutil
import signal
import subprocess
import sys
import tempfile
import threading
import time
import zipfile

PROJECT = Path(__file__).resolve().parents[1]
sys.path[:0] = [str(PROJECT), str(PROJECT / 'scripts')]
from install_wine import install, prefix_running
from twodrive_office.policy import Settings
from twodrive_office.bridge import Bridge, make_server


def process_info(process):
    try:
        state = (process / 'stat').read_text().rsplit(') ', 1)[1].split()
        env = dict(part.split(b'=', 1) for part in (process / 'environ').read_bytes().split(b'\0') if b'=' in part)
        return int(state[2]), state[0], env
    except (OSError, ValueError):
        return None


def stop_prefix_groups(prefix):
    expected = str(prefix).encode()
    groups = set()
    for process in Path('/proc').glob('[0-9]*'):
        info = process_info(process)
        if info and info[1] != 'Z' and info[2].get(b'WINEPREFIX') == expected:
            groups.add(info[0])
    for sig in (signal.SIGTERM, signal.SIGKILL):
        for group in groups:
            for process in Path('/proc').glob('[0-9]*'):
                info = process_info(process)
                if info and info[0] == group and info[1] != 'Z' and info[2].get(b'WINEPREFIX') != expected:
                    raise RuntimeError('Refusing to terminate a mixed/non-test process group')
            try:
                os.killpg(group, sig)
            except ProcessLookupError:
                pass
        time.sleep(0.8)
    if prefix_running(prefix):
        raise RuntimeError('Private Wine process cleanup did not finish')


def verify_editor_environment(prefix, display, config):
    found = []
    for process in Path('/proc').glob('[0-9]*'):
        try:
            if (process / 'comm').read_text().strip() not in ('WINWORD.EXE', 'EXCEL.EXE', 'POWERPNT.EXE'):
                continue
            info = process_info(process)
            if not info or info[2].get(b'WINEPREFIX') != str(prefix).encode():
                continue
            assert info[2].get(b'DISPLAY') == display.encode()
            assert not info[2].get(b'WAYLAND_DISPLAY')
            assert info[2].get(b'XDG_CONFIG_HOME') == str(config).encode()
            found.append(process.name)
        except FileNotFoundError:
            pass
    assert found, 'No private Office process found'


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--prefix', type=Path, required=True, help='Existing Office prefix, copied read-only')
    parser.add_argument('--runner', type=Path, required=True, help='Directory containing wine and wineserver')
    parser.add_argument('--xvfb', type=Path, required=True)
    parser.add_argument('--compiler', default=shutil.which('x86_64-w64-mingw32-g++'))
    parser.add_argument('--dll', type=Path, default=PROJECT / 'dist/wine/TwoDrive.Wine.dll')
    parser.add_argument('--host', choices=('Word', 'Excel', 'PowerPoint'), action='append')
    parser.add_argument('--report', type=Path)
    args = parser.parse_args()
    source = args.prefix.expanduser().resolve()
    if prefix_running(source):
        parser.error('Close the source bottle normally before copying it')
    baseline = {name: hashlib.sha256((source / name).read_bytes()).hexdigest() for name in ('system.reg', 'user.reg', 'userdef.reg')}
    results = []
    with tempfile.TemporaryDirectory(prefix='twodrive-wine-') as temporary:
        root = Path(temporary)
        prefix, mount = root / 'prefix', root / 'mock-mount'
        subprocess.run(['cp', '-a', '--reflink=auto', str(source), str(prefix)], check=True)
        for path in (prefix / 'drive_c/users').rglob('*'):
            if path.is_symlink() and not path.resolve().is_relative_to(prefix):
                path.unlink(); path.mkdir()
        mount.mkdir()
        executable = root / 'HostSmoke.exe'
        if not args.compiler:
            parser.error('Provide --compiler x86_64-w64-mingw32-g++')
        subprocess.run([str(args.compiler), '-std=c++17', '-O2', '-static', '-municode', '-I' + str(PROJECT / 'wine'),
                        str(PROJECT / 'tests/wine/HostSmoke.cpp'), '-o', str(executable),
                        '-lole32', '-loleaut32', '-luuid'], check=True)
        settings = Settings(mount, mount / 'copies', require_mount=False,
                            bridge_token='isolated-wine-test-' + os.urandom(24).hex(), sync_interval_seconds=8)
        server = make_server(Bridge(settings, state_root=root / 'state'), 0)
        settings = replace(settings, bridge_port=server.server_port)
        server.bridge.settings = settings
        install(prefix, args.dll, settings)
        threading.Thread(target=server.serve_forever, daemon=True).start()
        auth = root / 'xauthority'; auth.touch(mode=0o600)
        number = next(n for n in range(200, 500) if not Path(f'/tmp/.X{n}-lock').exists()
                      and not Path(f'/tmp/.X11-unix/X{n}').exists())
        display = ':' + str(number)
        subprocess.run(['xauth', '-f', str(auth), 'add', display, '.', os.urandom(16).hex()], check=True)
        env = dict(os.environ, WINEPREFIX=str(prefix), WINEDEBUG='-all', DXVK_LOG_LEVEL='none',
                   DISPLAY=display, XAUTHORITY=str(auth), WINEDLLOVERRIDES='winewayland.drv=d;winemenubuilder.exe=d',
                   XDG_CONFIG_HOME=str(root / 'config'), XDG_DATA_HOME=str(root / 'data'), XDG_CACHE_HOME=str(root / 'cache'),
                   WINESERVER=str(args.runner.resolve() / 'wineserver'), WINELOADER=str(args.runner.resolve() / 'wine'))
        for key in ('WAYLAND_DISPLAY', 'DBUS_SESSION_BUS_ADDRESS', 'SESSION_MANAGER'):
            env.pop(key, None)
        with (root / 'xvfb.log').open('w') as log:
            xvfb = subprocess.Popen([str(args.xvfb.resolve()), display, '-screen', '0', '1280x800x24',
                                     '-nolisten', 'tcp', '-auth', str(auth)], start_new_session=True, stdout=log, stderr=subprocess.STDOUT)
        try:
            time.sleep(0.4)
            assert xvfb.poll() is None, 'Private Xvfb failed to start'
            subprocess.run(['xdpyinfo', '-display', display], env=env, check=True, stdout=subprocess.DEVNULL, stderr=subprocess.PIPE)
            for host in args.host or ('Word', 'Excel', 'PowerPoint'):
                directory = mount / host; directory.mkdir()
                with (root / (host + '.log')).open('w') as log:
                    process = subprocess.Popen([str(args.runner.resolve() / 'wine'), str(executable), host,
                                                'Z:' + str(directory).replace('/', '\\')], env=env,
                                               start_new_session=True, stdout=log, stderr=subprocess.STDOUT)
                    deadline, verified = time.monotonic() + 110, False
                    while process.poll() is None and time.monotonic() < deadline:
                        if (directory / 'host.ready').exists() and not (directory / 'allow-host').exists():
                            verify_editor_environment(prefix, display, root / 'config')
                            (directory / 'allow-host').touch()
                        if (directory / 'staged.ready').exists() and not verified:
                            manifests = list((root / 'state').glob('*/pending.json'))
                            assert len(manifests) == 1, 'Expected one durable local revision'
                            data = json.loads(manifests[0].read_text())
                            with zipfile.ZipFile(manifests[0].parent / data['blob']) as archive:
                                xml = b' '.join(archive.read(name) for name in archive.namelist() if name.endswith('.xml'))
                                assert b'Wine actual unsaved edits' in xml
                            (directory / 'continue').touch(); verified = True
                        time.sleep(0.1)
                    if process.poll() is None:
                        raise TimeoutError(host + ' timed out')
                text = (root / (host + '.log')).read_text()
                print(text, flush=True)
                assert process.returncode == 0 and verified, host + ' real-host smoke failed'
                extension = {'Word': 'docx', 'Excel': 'xlsx', 'PowerPoint': 'pptx'}[host]
                with zipfile.ZipFile(directory / ('source.' + extension)) as archive:
                    xml = b' '.join(archive.read(name) for name in archive.namelist() if name.endswith('.xml'))
                    assert b'Wine final close edits' in xml
                assert not server.bridge.local_saves.pending(), 'Successful close left pending content'
                stop_prefix_groups(prefix)
                results.append({'host': host, 'passed': True, 'isolated_environment_verified': True})
        finally:
            try:
                stop_prefix_groups(prefix)
            finally:
                os.killpg(xvfb.pid, signal.SIGTERM); xvfb.wait(timeout=5)
                server.shutdown(); server.server_close(); server.bridge.local_saves.shutdown()
    assert baseline == {name: hashlib.sha256((source / name).read_bytes()).hexdigest() for name in baseline}, 'Original Wine registry changed'
    report = {'hosts': results, 'source_registry_unchanged': True, 'mock_mount_only': True,
              'interval_seconds': 8, 'private_prefix_and_process_groups_removed': True}
    if args.report:
        args.report.parent.mkdir(parents=True, exist_ok=True)
        args.report.write_text(json.dumps(report, indent=2) + '\n')
    print(json.dumps(report), flush=True)

if __name__ == '__main__':
    main()

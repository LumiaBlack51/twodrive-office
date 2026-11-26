#!/usr/bin/env python3
"""Real Wine Office tests on a copied prefix and authenticated private Xvfb.

No editor is launched on the user's display, and no save uses a real mount.
Only process groups verified to belong to this test prefix may be terminated.
"""
from __future__ import annotations
import argparse
from dataclasses import replace
import fcntl
import hashlib
import io
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
import xml.etree.ElementTree as ET

PROJECT = Path(__file__).resolve().parents[1]
sys.path[:0] = [str(PROJECT), str(PROJECT / 'scripts')]
from install_wine import install, prefix_running, sandbox_units
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


def verify_editor_environment(prefix, display, config, namespace=None, wayland=False):
    found = []
    for process in Path('/proc').glob('[0-9]*'):
        try:
            if (process / 'comm').read_text().strip() not in ('WINWORD.EXE', 'EXCEL.EXE', 'POWERPNT.EXE'):
                continue
            info = process_info(process)
            if not info or info[2].get(b'WINEPREFIX') != str(prefix).encode():
                continue
            if wayland:
                assert not info[2].get(b'DISPLAY')
                assert info[2].get(b'WAYLAND_DISPLAY') == b'wayland-office-test'
                assert info[2].get(b'XDG_RUNTIME_DIR') == os.fsencode(prefix.parent / 'wayland')
            else:
                assert info[2].get(b'DISPLAY') == display.encode()
                assert not info[2].get(b'WAYLAND_DISPLAY')
            assert info[2].get(b'XDG_CONFIG_HOME') == str(config).encode()
            if namespace is not None:
                for kind in ('net', 'mnt'):
                    assert os.readlink(process / 'ns' / kind) == os.readlink(Path('/proc') / str(namespace.pid) / 'ns' / kind)
                with (process / 'ns/net').open('rb') as network:
                    owner = fcntl.ioctl(network, 0xb701)  # NS_GET_USERNS
                try:
                    assert os.path.samestat(os.fstat(owner), os.stat(f'/proc/{namespace.pid}/ns/user'))
                    assert not os.path.samestat(os.fstat(owner), (process / 'ns/user').stat())
                finally:
                    os.close(owner)
            found.append(process.name)
        except FileNotFoundError:
            pass
    assert found, 'No private Office process found'


def wait_editor_exit(prefix):
    deadline = time.monotonic() + 12
    while time.monotonic() < deadline:
        remaining = []
        for process in Path('/proc').glob('[0-9]*'):
            try:
                info = process_info(process)
                if (info and info[1] not in ('Z', 'X') and info[2].get(b'WINEPREFIX') == str(prefix).encode()
                        and (process / 'comm').read_text().strip() in ('WINWORD.EXE', 'EXCEL.EXE', 'POWERPNT.EXE')):
                    remaining.append(process.name)
            except FileNotFoundError:
                pass
        if not remaining:
            return
        time.sleep(0.1)
    raise RuntimeError('Private Office process did not release after verified Quit/zero documents: ' + ', '.join(remaining))


def verify_input_snapshot(original, snapshot):
    """Check content preservation without logging any authorized document text."""
    with zipfile.ZipFile(io.BytesIO(original)) as source:
        media = {hashlib.sha256(snapshot.read(n)).digest() for n in snapshot.namelist() if n.startswith('word/media/')}
        for name in source.namelist():
            if name.startswith('word/media/'):
                assert hashlib.sha256(source.read(name)).digest() in media, 'Word snapshot lost or changed image data'
        for name in ('word/document.xml', 'word/footnotes.xml', 'word/endnotes.xml'):
            if name not in source.namelist():
                continue
            assert name in snapshot.namelist(), 'Word snapshot lost a document part'
            def text(content):
                return ''.join(e.text or '' for e in ET.fromstring(content).iter()
                               if e.tag == '{http://schemas.openxmlformats.org/wordprocessingml/2006/main}t')
            before, after = text(source.read(name)), text(snapshot.read(name))
            if before not in after:
                mismatch = next((i for i, (a, b) in enumerate(zip(before, after)) if a != b), min(len(before), len(after)))
                raise AssertionError(f'Word import/export changed existing text: {name}, first difference {mismatch}, '
                                     f'input length {len(before)}, snapshot length {len(after)}')


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--prefix', type=Path, required=True, help='Existing Office prefix, copied read-only')
    parser.add_argument('--runner', type=Path, required=True, help='Directory containing wine and wineserver')
    parser.add_argument('--xvfb', type=Path, required=True)
    parser.add_argument('--compiler', default=shutil.which('x86_64-w64-mingw32-g++'))
    parser.add_argument('--dll', type=Path, default=PROJECT / 'dist/wine/TwoDrive.Wine.dll')
    parser.add_argument('--host', choices=('Word', 'Excel', 'PowerPoint'), action='append')
    parser.add_argument('--report', type=Path)
    parser.add_argument('--sandbox', action='store_true', help='Real isolated net/mount namespace, directory grants and systemd path activation')
    parser.add_argument('--window-close', action='store_true', help='Close the verified private native window instead of calling Quit')
    parser.add_argument('--no-idle-reopen', action='store_true', help='Skip the separate empty-editor 100-second idle phase')
    parser.add_argument('--ribbon-image', type=Path, help='Capture the verified private display for startup ribbon inspection')
    parser.add_argument('--first-quit', action='store_true', help='Word exits on the first Quit request with unsaved edits and no second close')
    parser.add_argument('--word-compatibility', type=int, choices=(12, 14, 15),
                        help='Synthetic 17-page Word document with headers, footers and a floating textbox')
    parser.add_argument('--word-document', type=Path,
                        help='Authorized DOCX input; opens only a private copy (requires --host Word --sandbox)')
    parser.add_argument('--word-visible', action='store_true', help='Show authorized Word copy on the verified private display')
    parser.add_argument('--mixed-edits', action='store_true',
                        help='Edit multiple Office objects while the first snapshot reply is held')
    parser.add_argument('--large-deck', action='store_true',
                        help='Exercise repeated native saves and immediate edits on a generated 100+ MiB PPTX')
    parser.add_argument('--measure-input', action='store_true',
                        help='PowerPoint only: measure Ctrl+S to a visible glyph on a generated large deck')
    parser.add_argument('--wayland-tools', type=Path,
                        help='Path to the Soda test tools with scripts/run-headless-wayland.sh and tools/weston-capture')
    args = parser.parse_args()
    args.large_deck = args.large_deck or args.measure_input
    if args.large_deck and ('PowerPoint' not in (args.host or ('PowerPoint',)) or not args.mixed_edits):
        parser.error('--large-deck requires PowerPoint and --mixed-edits')
    if args.wayland_tools and args.sandbox:
        parser.error('--wayland-tools currently uses the local mock bridge')
    if args.measure_input and ('PowerPoint' not in (args.host or ('PowerPoint',)) or not args.mixed_edits or not args.wayland_tools):
        parser.error('--measure-input requires PowerPoint, --mixed-edits and --wayland-tools')
    if args.first_quit and (args.host != ['Word'] or args.window_close):
        parser.error('--first-quit requires --host Word and cannot combine with --window-close')
    if args.word_compatibility and args.host != ['Word']:
        parser.error('--word-compatibility requires --host Word')
    if args.word_document and (args.host != ['Word'] or not args.sandbox or args.word_compatibility):
        parser.error('--word-document requires --host Word --sandbox and cannot combine with --word-compatibility')
    if args.word_visible and not args.word_document:
        parser.error('--word-visible requires --word-document')
    hosts = args.host or ['Word', 'Excel', 'PowerPoint']
    if args.mixed_edits and len(hosts) > 1:
        # A stopped Wine Click-to-Run service in a reused prefix can leave
        # the second Office application unable to start through COM.
        reports = []
        for host in hosts:
            options = argparse.Namespace(**vars(args))
            options.host = [host]
            options.measure_input = args.measure_input and host == 'PowerPoint'
            options.large_deck = args.large_deck and host == 'PowerPoint'
            reports.append(run(options))
        report = dict(reports[0], hosts=[entry for r in reports for entry in r['hosts']])
    else:
        report = run(args)
    if args.report:
        args.report.parent.mkdir(parents=True, exist_ok=True)
        args.report.write_text(json.dumps(report, indent=2) + '\n')
    print(json.dumps(report), flush=True)


def run(args):
    input_bytes = None
    if args.word_document:
        if args.word_document.suffix.lower() != '.docx':
            raise ValueError('--word-document accepts only DOCX')
        from twodrive_office.wine import check_office_source
        check_office_source(args.word_document)
        input_bytes = args.word_document.read_bytes()
    source = args.prefix.expanduser().resolve()
    if prefix_running(source):
        raise RuntimeError('Close the source bottle normally before copying it')
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
            raise RuntimeError('Provide --compiler x86_64-w64-mingw32-g++')
        subprocess.run([str(args.compiler), '-std=c++17', '-O2', '-static', '-municode', '-I' + str(PROJECT / 'wine'),
                        str(PROJECT / 'tests/wine/HostSmoke.cpp'), '-o', str(executable),
                        '-lole32', '-loleaut32', '-luuid'], check=True)
        settings = Settings(mount, mount / 'copies', require_mount=False,
                            bridge_token='isolated-wine-test-' + os.urandom(24).hex(), sync_interval_seconds=8)
        server = make_server(Bridge(settings, state_root=root / 'state'), 0)
        settings = replace(settings, bridge_port=server.server_port)
        server.bridge.settings = settings
        destination = install(prefix, args.dll, settings, sandbox_wakeup=args.sandbox)
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
        if args.large_deck:
            env['TWODRIVE_TEST_LARGE_DECK'] = '1'
        if args.measure_input:
            env['TWODRIVE_TEST_PAINT'] = '1'
        if args.mixed_edits:
            env['TWODRIVE_TEST_MIXED_EDITS'] = '1'
            normal_save = server.bridge.save
            def held_reply(key, content, revision=None, **kwargs):
                result = normal_save(key, content, revision, **kwargs)
                directory = server.bridge.sessions[key].plan.target.parent
                if not (directory / 'reply-held').exists():
                    (directory / 'reply-held').touch()
                    deadline = time.monotonic() + 4
                    while not (directory / 'edited-during-transfer').exists():
                        if time.monotonic() >= deadline:
                            (directory / 'transfer-blocked-ui').touch()
                            break
                        time.sleep(0.01)
                return result
            server.bridge.save = held_reply
        if args.first_quit:
            env['TWODRIVE_TEST_FIRST_QUIT'] = '1'
        if args.word_compatibility:
            env['TWODRIVE_TEST_WORD_COMPATIBILITY'] = str(args.word_compatibility)
        if args.word_document:
            env['TWODRIVE_TEST_WORD_DOCUMENT'] = args.word_document.name
        if args.word_visible:
            env['TWODRIVE_TEST_WORD_VISIBLE'] = '1'
        if args.window_close:
            env['TWODRIVE_TEST_WINDOW_CLOSE'] = '1'
        if args.ribbon_image:
            env['TWODRIVE_TEST_RIBBON'] = '1'
        with (root / 'xvfb.log').open('w') as log:
            xvfb = subprocess.Popen([str(args.xvfb.resolve()), display, '-screen', '0', '1280x800x24',
                                     '-noreset', '-nolisten', 'tcp', '-auth', str(auth)], start_new_session=True, stdout=log, stderr=subprocess.STDOUT)
        namespace, weston, unit_name, unit_paths = None, None, None, []
        try:
            time.sleep(0.4)
            assert xvfb.poll() is None, 'Private Xvfb failed to start'
            subprocess.run(['xdpyinfo', '-display', display], env=env, check=True, stdout=subprocess.DEVNULL, stderr=subprocess.PIPE)
            office_env = env.copy()
            if args.wayland_tools:
                runtime = root / 'wayland'
                runtime.mkdir(mode=0o700)
                base = args.wayland_tools.resolve()
                weston_env = dict(env, XDG_RUNTIME_DIR=str(runtime), OFFICE_TEST_SCALE='1',
                                  OFFICE_TEST_WIDTH='1280', OFFICE_TEST_HEIGHT='800')
                weston_env.pop('DISPLAY', None)
                with (root / 'weston.log').open('w') as log:
                    weston = subprocess.Popen([str(base / 'scripts/run-headless-wayland.sh')],
                                              env=weston_env, start_new_session=True, stdout=log, stderr=subprocess.STDOUT)
                limit = time.monotonic() + 5
                while not (runtime / 'wayland-office-test').exists():
                    assert weston.poll() is None and time.monotonic() < limit, 'Private Weston failed to start'
                    time.sleep(.05)
                office_env.pop('DISPLAY', None)
                office_env.update(XDG_RUNTIME_DIR=str(runtime), WAYLAND_DISPLAY='wayland-office-test',
                                  WINEDLLOVERRIDES='winex11.drv=d;winemenubuilder.exe=d')
            if args.sandbox:
                if not args.no_idle_reopen:
                    env['TWODRIVE_TEST_IDLE_REOPEN'] = '1'
                    office_env['TWODRIVE_TEST_IDLE_REOPEN'] = '1'
                script = """
import pathlib,subprocess,sys
subprocess.run(['mount','--make-rprivate','/'],check=True)
subprocess.run(['mount','-t','tmpfs','tmpfs','/run'],check=True)
target='/run/cpak/grants/twodrive-test/mock-mount'
pathlib.Path(target).mkdir(parents=True)
subprocess.run(['mount','--bind',sys.argv[1],target],check=True)
subprocess.run(['ip','link','set','lo','up'],check=True)
print('ready',flush=True)
sys.stdin.read()
"""
                namespace = subprocess.Popen(['unshare', '--user', '--map-root-user', '--net', '--mount',
                                              sys.executable, '-c', script, str(mount)],
                                             stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=subprocess.PIPE,
                                             text=True, start_new_session=True)
                assert namespace.stdout.readline().strip() == 'ready', 'Private Office sandbox failed to start'
                config = root / 'settings.json'; settings.save(config)
                runtime = root / 'runtime'; runtime.mkdir(mode=0o700)
                unit_name, definitions = sandbox_units(prefix, destination, PROJECT, config)
                definitions[unit_name + '.service'] += 'Environment=XDG_RUNTIME_DIR=' + str(runtime) + '\n'
                units = Path.home() / '.config/systemd/user'
                for filename, definition in definitions.items():
                    path = units / filename; path.write_text(definition); unit_paths.append(path)
                subprocess.run(['systemd-analyze', '--user', 'verify', *map(str, unit_paths)], check=True)
                subprocess.run(['systemctl', '--user', 'daemon-reload'], check=True)
                subprocess.run(['systemctl', '--user', 'start', unit_name + '.path'], check=True)
            for host in args.host or ('Word', 'Excel', 'PowerPoint'):
                assert xvfb.poll() is None, 'Private display exited before ' + host
                directory = mount / host; directory.mkdir()
                if args.large_deck and host == 'PowerPoint':
                    from PIL import Image
                    for index in range(1, 12):
                        Image.frombytes('RGB', (2048, 2048), os.urandom(2048 * 2048 * 3)).save(
                            directory / f'media-{index}.png', compress_level=0)
                if input_bytes is not None:
                    (directory / args.word_document.name).write_bytes(input_bytes)
                    (directory / args.word_document.name).chmod(0o600)
                command = [str(args.runner.resolve() / 'wine'), str(executable), host,
                           'Z:' + str(directory if namespace is None else Path('/run/cpak/grants/twodrive-test/mock-mount') / host).replace('/', '\\')]
                if namespace is not None:
                    command = ['nsenter', '-t', str(namespace.pid), '-U', '-n', '-m', '--preserve-credentials',
                               'unshare', '--user', '--map-root-user', *command]
                with (root / (host + '.log')).open('w') as log:
                    process = subprocess.Popen(command, env=office_env if weston else env,
                                               start_new_session=True, stdout=log, stderr=subprocess.STDOUT)
                    deadline, verified = time.monotonic() + (270 if args.large_deck else 110), False
                    if namespace is not None and not args.no_idle_reopen:
                        deadline += 100
                    while process.poll() is None and time.monotonic() < deadline:
                        if (directory / 'host.ready').exists() and not (directory / 'allow-host').exists():
                            verify_editor_environment(prefix, display, root / 'config', namespace, bool(weston))
                            (directory / 'allow-host').touch()
                        if args.measure_input and (directory / 'paint.ready').exists() and not (directory / 'paint.done').exists():
                            verify_editor_environment(prefix, display, root / 'config', namespace, bool(weston))
                            subprocess.run([sys.executable, str(PROJECT / 'tests/wine/paint_probe.py'),
                                            str(directory), str(args.wayland_tools.resolve() / 'tools/weston-capture')],
                                           env=office_env, check=True, timeout=80)
                            (directory / 'paint.done').touch()
                        if (directory / 'empty-idle.ready').exists() and not (directory / 'allow-reopen').exists():
                            state = subprocess.check_output(['systemctl', '--user', 'show', unit_name + '.service',
                                                             '-p', 'MainPID', '--value'], text=True).strip()
                            assert state == '0', 'Unused relay remained after empty-Office idle'
                            assert not server.bridge.sessions, 'Empty Office created a save session'
                            (directory / 'allow-reopen').touch()
                        if (directory / 'ribbon.ready').exists() and not (directory / 'ribbon.captured').exists():
                            args.ribbon_image.parent.mkdir(parents=True, exist_ok=True)
                            subprocess.run([sys.executable, '-c',
                                            'from PIL import ImageGrab; import sys; im=ImageGrab.grab(xdisplay=sys.argv[1]); '
                                            'assert im.getbbox(), "Private Office display is blank"; im.save(sys.argv[2])',
                                            display, str(args.ribbon_image.resolve())], env=env, check=True)
                            (directory / 'ribbon.captured').touch()
                        if (directory / 'staged.ready').exists() and not verified:
                            manifests = list((root / 'state').glob('*/pending.json'))
                            assert len(manifests) == 1, 'Expected one durable local revision'
                            data = json.loads(manifests[0].read_text())
                            with zipfile.ZipFile(manifests[0].parent / data['blob']) as archive:
                                xml = b' '.join(archive.read(name) for name in archive.namelist() if name.endswith('.xml'))
                                assert b'Wine actual unsaved edits' in xml
                                if args.mixed_edits:
                                    assert b'Mixed component first' in xml and b'Mixed component final' not in xml
                                    assert (directory / 'edited-during-transfer').exists()
                                    assert not (directory / 'transfer-blocked-ui').exists(), 'Snapshot reply blocked Office editing'
                                if input_bytes is not None:
                                    verify_input_snapshot(input_bytes, archive)
                                if args.word_compatibility:
                                    for marker in (b'Headers synthetic preserved marker', b'Footers synthetic preserved marker',
                                                   b'Floating textbox preserved marker'):
                                        assert marker in xml, 'Word snapshot lost a non-body part'
                            (directory / 'continue').touch(); verified = True
                        time.sleep(0.1)
                    if process.poll() is None:
                        raise TimeoutError(host + ' timed out')
                text = (root / (host + '.log')).read_text()
                print(text, flush=True)
                assert process.returncode == 0 and verified, host + ' real-host smoke failed'
                extension = {'Word': 'docx', 'Excel': 'xlsx', 'PowerPoint': 'pptx'}[host]
                with zipfile.ZipFile(directory / (args.word_document.name if args.word_document else 'source.' + extension)) as archive:
                    xml = b' '.join(archive.read(name) for name in archive.namelist() if name.endswith('.xml'))
                    assert b'Wine final close edits' in xml
                    if args.large_deck and host == 'PowerPoint':
                        assert b'Replacement 5' in xml and b'Table edit 4' in xml
                        assert (directory / 'source.pptx').stat().st_size > 100 * 1024**2
                        assert archive.testzip() is None
                    if args.mixed_edits:
                        assert b'Mixed component final' in xml
                        if host == 'Excel':
                            assert sum(1 for name in archive.namelist() if name.startswith('xl/worksheets/sheet')
                                       and name.endswith('.xml')) >= 2
                            assert b'SUM(A1:A2)' in xml
                        if host == 'PowerPoint':
                            assert 'ppt/slides/slide2.xml' in archive.namelist()
                assert not server.bridge.local_saves.pending(), 'Successful close left pending content'
                wait_editor_exit(prefix)
                stop_prefix_groups(prefix)
                registry = (prefix / 'system.reg').read_text()
                virtual_key = ('Software\\Microsoft\\Office\\ClickToRun\\REGISTRY\\Machine\\Software\\Microsoft\\Office\\'
                               + host + '\\Addins\\TwoDrive.Office.WineAutoSave').replace('\\', '\\\\').casefold()
                body, collecting = [], False
                for line in registry.splitlines():
                    if line.startswith('[') and ']' in line:
                        collecting = line[1:line.index(']')].casefold() == virtual_key
                    elif collecting:
                        body.append(line)
                assert '"LoadBehavior"=dword:00000003' in body, 'C2R startup flag was lost after normal exit'
                results.append({'host': host, 'passed': True, 'isolated_environment_verified': True,
                                'editor_exited_without_forced_cleanup': True,
                                'exit_method': 'first_Quit_with_unsaved_edits' if args.first_quit else 'private_window_close' if args.window_close else 'Quit',
                                'idle_reactivation_verified': args.sandbox and not args.no_idle_reopen})
                if args.measure_input and host == 'PowerPoint':
                    results[-1]['ctrl_s_to_glyph_ms'] = json.loads((directory / 'paint.json').read_text())
                if args.large_deck and host == 'PowerPoint':
                    results[-1]['native_save_and_edit_ms'] = [int(line.split()[-2]) for line in text.splitlines()
                                                             if line.startswith('NATIVE SAVE AND NEXT COM EDIT')]
                    results[-1]['deck_bytes'] = (directory / 'source.pptx').stat().st_size
                results[-1]['automatic_addin_connection_verified'] = True
                results[-1]['c2r_autoload_persisted_after_exit'] = True
        except Exception:
            for host_log in root.glob('*.log'):
                print(host_log.name + '\n' + host_log.read_text(errors='replace')[-6000:], flush=True)
            raise
        finally:
            try:
                stop_prefix_groups(prefix)
            finally:
                if unit_name:
                    subprocess.run(['systemctl', '--user', 'stop', unit_name + '.path', unit_name + '.service'], check=False)
                for path in unit_paths:
                    path.unlink(missing_ok=True)
                if unit_paths:
                    subprocess.run(['systemctl', '--user', 'daemon-reload'], check=True)
                    subprocess.run(['systemctl', '--user', 'reset-failed', unit_name + '.service', unit_name + '.path'], check=False,
                                   stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
                if namespace is not None:
                    if namespace.poll() is None:
                        os.killpg(namespace.pid, signal.SIGTERM); namespace.wait(timeout=5)
                    namespace.stdin.close(); namespace.stdout.close(); namespace.stderr.close()
                if weston is not None and weston.poll() is None:
                    os.killpg(weston.pid, signal.SIGTERM)
                    weston.wait(timeout=5)
                os.killpg(xvfb.pid, signal.SIGTERM); xvfb.wait(timeout=5)
                server.shutdown(); server.server_close(); server.bridge.local_saves.shutdown()
    assert baseline == {name: hashlib.sha256((source / name).read_bytes()).hexdigest() for name in baseline}, 'Original Wine registry changed'
    report = {'hosts': results, 'source_registry_unchanged': True, 'mock_mount_only': True,
              'interval_seconds': 8, 'private_prefix_and_process_groups_removed': True}
    report['isolated_sandbox_and_path_activation'] = args.sandbox
    report['nested_office_user_namespace_verified'] = args.sandbox
    if args.word_compatibility:
        report['word_compatibility_mode'] = args.word_compatibility
        report['word_snapshot_headers_footers_textbox_verified'] = True
    if args.word_document:
        assert args.word_document.read_bytes() == input_bytes, 'Authorized input was changed'
        report['authorized_input_unchanged'] = True
        report['snapshot_existing_text_and_media_preserved'] = True
    return report

if __name__ == '__main__':
    main()

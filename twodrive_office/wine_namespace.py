"""Scoped loopback transport and live bind-mount resolution for sandboxed Wine.

The relay owns no save state: the ordinary authenticated host bridge still owns
all sessions, mount checks and durable revisions. No host network is shared.
"""
from __future__ import annotations

import argparse
import array
import concurrent.futures
import configparser
import fcntl
import json
import os
from pathlib import Path
import re
import select
import secrets
import socket
import subprocess
import sys
import threading
import time

from .policy import SaveError, Settings


def requested_namespace(prefix, settings):
    """Consume one directory trigger, without launching Wine or an editor."""
    prefix = prefix.expanduser().resolve(strict=True)
    if prefix.stat().st_uid != os.getuid() or not (prefix / 'user.reg').is_file():
        raise SaveError('Wine prefix 无效')
    directories = sorted({p.resolve() for p in (prefix / 'drive_c/users').glob('*/AppData/Local/TwoDrive-Office-Wine')
                          if p.resolve().is_relative_to(prefix) and not p.is_symlink()})
    if len(directories) != 1:
        raise SaveError('无法唯一识别 Wine 保存连接目录')
    directory = directories[0]
    request = directory / 'relay.request'
    if request.is_symlink():
        raise SaveError('拒绝符号链接唤醒请求')
    try:
        request.rmdir()  # directory-only trigger: never overwrite a file/link
    except FileNotFoundError:
        return None
    config = directory / 'bridge.ini'
    if config.is_symlink() or config.stat().st_uid != os.getuid():
        raise SaveError('Wine 保存连接配置无效')
    ini = configparser.ConfigParser()
    ini.read(config, encoding='utf-16')
    if (ini.get('bridge', 'prefix') != str(prefix)
            or ini.getint('bridge', 'port') != settings.bridge_port
            or not secrets.compare_digest(ini.get('bridge', 'token'), settings.bridge_token)
            or not ini.getboolean('bridge', 'sandbox_wakeup', fallback=False)):
        raise SaveError('Wine 唤醒请求与保存服务配置不符')
    namespaces = {}
    for process in Path('/proc').glob('[0-9]*'):
        try:
            if process.stat().st_uid != os.getuid():
                continue
            if (process / 'comm').read_text().strip() not in ('WINWORD.EXE', 'EXCEL.EXE', 'POWERPNT.EXE'):
                continue
            if (process / 'stat').read_text().rsplit(') ', 1)[1].split()[0] in ('Z', 'X'):
                continue
            env = dict(p.split(b'=', 1) for p in (process / 'environ').read_bytes().split(b'\0') if b'=' in p)
            if Path(os.fsdecode(env.get(b'WINEPREFIX', b''))).resolve() != prefix:
                continue
            visible = process / 'root' / str(prefix).lstrip('/')
            if not os.path.samestat(prefix.stat(), visible.stat()):
                continue
            pid = int(process.name)
            namespaces[identity(pid)[1]] = pid
        except (FileNotFoundError, ProcessLookupError):
            continue
    if len(namespaces) > 1:
        raise SaveError('同一 Wine prefix 存在多个网络命名空间，拒绝猜测')
    return next(iter(namespaces.values()), None)


def identity(pid):
    process = Path('/proc') / str(pid)
    if process.stat().st_uid != os.getuid():
        raise SaveError('不能连接其他用户的 Office 容器')
    return ((process / 'stat').read_text().rsplit(') ', 1)[1].split()[19],
            os.readlink(process / 'ns/net'))


def mounts(path):
    def unescape(value):
        return re.sub(r'\\([0-7]{3})', lambda m: chr(int(m[1], 8)), value)
    result = []
    for line in path.read_text().splitlines():
        left, right = line.split(' - ', 1)
        fields, fs = left.split(), right.split()
        result.append((fields[2], Path(unescape(fields[3])),
                       Path(unescape(fields[4])), fs[0]))
    return result


def host_path(namespace_path, process, host_mounts=None):
    """Translate a live bind mount by filesystem/root, then verify inode identity.

    Never save through /proc/PID/root: all later safety checks use the canonical
    host path. A stale grant or ambiguous mapping must fail closed.
    """
    path = Path(namespace_path)
    entries = [m for m in mounts(process / 'mountinfo') if path.is_relative_to(m[2])]
    if not entries:
        raise SaveError('Office 容器文件挂载不存在')
    device, root, point, fs = max(entries, key=lambda m: len(m[2].parts))
    file_root = root / path.relative_to(point)
    visible = process / 'root' / str(path).lstrip('/')
    # Reject symlinks rather than accidentally resolving absolute links in the
    # host namespace. cpak's granted directories and Office files need none.
    cursor = process / 'root'
    for component in path.parts[1:]:
        cursor /= component
        if cursor.is_symlink():
            raise SaveError('Office 容器文档路径包含符号链接')
    observed = visible.stat()
    candidates = set()
    for host_device, host_root, host_point, host_fs in (host_mounts if host_mounts is not None else mounts(Path('/proc/self/mountinfo'))):
        if (device, fs) != (host_device, host_fs) or not file_root.is_relative_to(host_root):
            continue
        try:
            candidate = (host_point / file_root.relative_to(host_root)).resolve(strict=True)
            if os.path.samestat(candidate.stat(), observed):
                candidates.add(candidate)
        except FileNotFoundError:
            continue
    if len(candidates) != 1:
        raise SaveError('Office 容器路径不能唯一对应主机原文件')
    return candidates.pop()


def resolve_granted_path(prefix, path):
    """Only live Office processes using this exact prefix may identify a grant."""
    targets = set()
    for process in Path('/proc').glob('[0-9]*'):
        try:
            if process.stat().st_uid != os.getuid():
                continue
            if (process / 'comm').read_text().strip() not in ('WINWORD.EXE', 'EXCEL.EXE', 'POWERPNT.EXE'):
                continue
            env = dict(p.split(b'=', 1) for p in (process / 'environ').read_bytes().split(b'\0') if b'=' in p)
            if Path(os.fsdecode(env.get(b'WINEPREFIX', b''))).resolve() != prefix:
                continue
            visible_prefix = process / 'root' / str(prefix).lstrip('/')
            if not os.path.samestat(prefix.stat(), visible_prefix.stat()):
                continue
            actual = process / 'root'
            for component in Path(path).parts[1:]:
                exact = actual / component
                if not exact.exists():
                    matches = [p for p in actual.iterdir() if p.name.casefold() == component.casefold()]
                    if len(matches) != 1:
                        raise SaveError('Wine 文档路径不存在或大小写有歧义')
                    exact = matches[0]
                if exact.is_symlink():
                    raise SaveError('Office 容器文档路径包含符号链接')
                actual = exact
            targets.add(host_path('/' + str(actual.relative_to(process / 'root')), process))
        except (FileNotFoundError, ProcessLookupError):
            continue
    if len(targets) != 1:
        raise SaveError('请通过已连接保存服务的 Office 容器打开此文件')
    return str(targets.pop())


LISTENER = """
import array, socket, sys
channel = socket.socket(fileno=int(sys.argv[1]))
listener = socket.socket()
listener.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
listener.bind(('127.0.0.1', int(sys.argv[2])))
listener.listen(16)
channel.sendmsg([b'listener'], [(socket.SOL_SOCKET, socket.SCM_RIGHTS,
                               array.array('i', [listener.fileno()]))])
"""


def namespace_listener(pid, port):
    parent, child = socket.socketpair()
    try:
        try:
            expected = identity(pid)
            with (Path('/proc') / str(pid) / 'ns/net').open('rb') as network:
                # cpak execs Office in a nested user namespace that does not
                # own its inherited network namespace. Entering Office's user
                # namespace first loses the authority needed to enter that net.
                # NS_GET_USERNS returns the actual owner; pin both descriptors
                # instead of finding a container ancestor by process name/PID.
                owner = fcntl.ioctl(network.fileno(), 0xb701)  # NS_GET_USERNS
                try:
                    if identity(pid) != expected:
                        raise SaveError('Office 容器已改变')
                    subprocess.run(['nsenter', '--user=/proc/self/fd/' + str(owner),
                                    '--net=/proc/self/fd/' + str(network.fileno()),
                                    '--preserve-credentials', sys.executable, '-c',
                                    LISTENER, str(child.fileno()), str(port)],
                                   pass_fds=(child.fileno(), owner, network.fileno()),
                                   check=True, timeout=10, stdin=subprocess.DEVNULL,
                                   stdout=subprocess.DEVNULL, stderr=subprocess.PIPE)
                finally:
                    os.close(owner)
        except subprocess.CalledProcessError as exc:
            detail = exc.stderr.decode(errors='replace').strip().splitlines()
            raise SaveError('无法创建容器保存端口：' + (detail[-1] if detail else 'nsenter failed')) from None
        parent.settimeout(1)
        _, ancillary, flags, _ = parent.recvmsg(32, socket.CMSG_SPACE(array.array('i').itemsize))
        descriptors = array.array('i')
        for level, kind, data in ancillary:
            if level == socket.SOL_SOCKET and kind == socket.SCM_RIGHTS:
                descriptors.frombytes(data)
        if len(descriptors) != 1 or flags & socket.MSG_CTRUNC:
            for descriptor in descriptors:
                os.close(descriptor)
            raise SaveError('Office 容器未返回保存端口')
        return socket.socket(fileno=descriptors[0])
    finally:
        parent.close(); child.close()


def forward(client, port, stop):
    with client:
        try:
            with socket.create_connection(('127.0.0.1', port), timeout=5) as upstream:
                client.settimeout(30)
                upstream.settimeout(30)
                peers = {client: upstream, upstream: client}
                last_data = time.monotonic()
                while peers and not stop.is_set():
                    ready, _, _ = select.select(list(peers), [], [], 1)
                    if not ready:
                        if time.monotonic() - last_data >= 30:
                            return
                        continue
                    last_data = time.monotonic()
                    for reader in ready:
                        data = reader.recv(65536)
                        if data:
                            peers[reader].sendall(data)
                        else:
                            peers[reader].shutdown(socket.SHUT_WR)
                            del peers[reader]
        except OSError:
            # Failed transport never acknowledges or discards a save revision.
            pass


def relay(pid, port, ready_fd, *, startup_timeout=60, idle_timeout=30):
    expected = identity(pid)
    directory = Path(os.environ.get('XDG_RUNTIME_DIR', f'/run/user/{os.getuid()}')) / 'twodrive-office'
    directory.mkdir(mode=0o700, parents=True, exist_ok=True)
    if directory.is_symlink() or directory.stat().st_uid != os.getuid() or directory.stat().st_mode & 0o077:
        raise SaveError('保存服务临时目录权限不安全')
    name = expected[1].replace(':', '').replace('[', '').replace(']', '')
    lock_path = directory / f'wine-{name}-{port}.lock'
    descriptor = os.open(lock_path, os.O_CREAT | os.O_RDWR | os.O_NOFOLLOW, 0o600)
    with os.fdopen(descriptor, 'r+') as lease:
        try:
            fcntl.flock(lease, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError:
            deadline = time.monotonic() + 10
            while time.monotonic() < deadline:
                lease.seek(0)
                if lease.read() == 'ready':
                    os.write(ready_fd, b'ready'); os.close(ready_fd)
                    return
                time.sleep(0.05)
            raise SaveError('Office 容器保存端口尚未就绪')
        lease.seek(0); lease.truncate(); lease.write('starting'); lease.flush()
        try:
            with namespace_listener(pid, port) as listener:
                if identity(pid) != expected:
                    raise SaveError('Office 容器已改变')
                listener.settimeout(1)
                lease.seek(0); lease.truncate(); lease.write('ready'); lease.flush()
                os.write(ready_fd, b'ready'); os.close(ready_fd)
                stop = threading.Event()
                with concurrent.futures.ThreadPoolExecutor(max_workers=16) as workers:
                    pending = set()
                    last_client = time.monotonic()
                    connected = False
                    try:
                        while True:
                            pending = {f for f in pending if not f.done()}
                            timeout = idle_timeout if connected else startup_timeout
                            # A container or orphaned Office PID may outlive its
                            # windows. Neither retains an unused Python relay.
                            if not pending and time.monotonic() - last_client >= timeout:
                                break
                            try:
                                if identity(pid) != expected:
                                    break
                                client, _ = listener.accept()
                            except socket.timeout:
                                continue
                            except (FileNotFoundError, ProcessLookupError):
                                break
                            last_client, connected = time.monotonic(), True
                            if len(pending) >= 16:
                                client.close()
                            else:
                                pending.add(workers.submit(forward, client, port, stop))
                    finally:
                        stop.set()
        finally:
            lock_path.unlink(missing_ok=True)


def ensure_relay(pid, port):
    if identity(pid)[1] == os.readlink('/proc/self/ns/net'):
        return
    read_fd, write_fd = os.pipe()
    try:
        child = subprocess.Popen([sys.executable, '-m', 'twodrive_office.wine_namespace', '--pid', str(pid),
                                  '--port', str(port), '--ready-fd', str(write_fd)],
                                 pass_fds=(write_fd,), start_new_session=True,
                                 stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    finally:
        os.close(write_fd)
    try:
        ready, _, _ = select.select([read_fd], [], [], 15)
        if not ready or os.read(read_fd, 1024) != b'ready':
            child.terminate(); child.wait(timeout=5)
            raise SaveError('Office 容器保存端口连接失败')
    finally:
        os.close(read_fd)
    return child


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    target = parser.add_mutually_exclusive_group(required=True)
    target.add_argument('--pid', type=int, help='Owned sandbox init PID, before Office starts')
    target.add_argument('--cpak', type=Path, help='Start/connect the Bottles container, without launching Office')
    target.add_argument('--request-prefix', type=Path, help='Consume a private DLL wakeup trigger and serve its relay')
    parser.add_argument('--config', type=Path, help=argparse.SUPPRESS)
    parser.add_argument('--origin', default='github.com/bottlesdevs/bottles')
    parser.add_argument('--instance', default='')
    parser.add_argument('--port', type=int)
    parser.add_argument('--ready-fd', type=int, help=argparse.SUPPRESS)
    args = parser.parse_args()
    try:
        settings = Settings.load(args.config) if args.port is None or args.request_prefix else None
        port = args.port if args.port is not None else settings.bridge_port
        if not 1024 <= port <= 65535:
            raise SaveError('本地服务端口无效')
        if args.request_prefix:
            if args.ready_fd is not None or args.port is not None:
                raise SaveError('内部唤醒参数无效')
            pid = requested_namespace(args.request_prefix, settings)
            if pid is not None and identity(pid)[1] != os.readlink('/proc/self/ns/net'):
                relay(pid, port, os.open(os.devnull, os.O_WRONLY))
            return
        if args.cpak:
            if args.ready_fd is not None:
                raise SaveError('内部连接参数无效')
            options = ['-i', args.instance] if args.instance else []
            subprocess.run([str(args.cpak), 'run', *options, args.origin, '@true'], check=True, timeout=30)
            containers = json.loads(subprocess.check_output([str(args.cpak), 'ps', '--json'], text=True, timeout=10))
            active = [c for c in containers if c['origin'] == args.origin and c['instance'] == args.instance
                      and c['container'] == 'running']
            if len(active) != 1 or type(active[0]['container_pid']) is not int:
                raise SaveError('无法唯一识别 Office 容器')
            args.pid = active[0]['container_pid']
        if args.ready_fd is None:
            ensure_relay(args.pid, port)
            print('Office 容器保存连接已就绪')
        else:
            relay(args.pid, port, args.ready_fd)
    except (OSError, SaveError, ValueError, configparser.Error, subprocess.SubprocessError) as exc:
        parser.exit(1, str(exc) + '\n')


if __name__ == '__main__':
    main()

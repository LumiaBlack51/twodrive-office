import json
from dataclasses import replace
import fcntl
import os
from pathlib import Path
import signal
import select
import socket
import subprocess
import sys
import tempfile
import threading
import unittest
from unittest.mock import Mock, patch

from twodrive_office.bridge import Bridge, make_server
from twodrive_office.policy import SaveError, Settings
from twodrive_office.wine_namespace import ensure_relay, forward, host_path, mounts, relay, requested_namespace
from twodrive_office.wine import resolve_wine_path


class GrantMappingTests(unittest.TestCase):
    def test_bind_mount_uses_host_original_and_refuses_stale_grant(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            original = root / 'host folder/file.docx'
            original.parent.mkdir(); original.write_bytes(b'original')
            process = root / 'proc'; visible = process / 'root/run/cpak/grants/id/folder/file.docx'
            visible.parent.mkdir(parents=True); os.link(original, visible)
            (process / 'mountinfo').write_text('1 0 8:1 /host\\040folder /run/cpak/grants/id/folder rw - ext4 /dev/test rw\n')
            host_mounts = [('8:1', Path('/'), root, 'ext4')]
            result = host_path('/run/cpak/grants/id/folder/file.docx', process, host_mounts)
            self.assertEqual(result, original)
            alias = root / 'alias'; alias.mkdir(); os.link(original, alias / 'file.docx')
            with self.assertRaises(SaveError):
                host_path('/run/cpak/grants/id/folder/file.docx', process,
                          host_mounts + [('8:1', Path('/host folder'), alias, 'ext4')])
            visible.unlink(); visible.write_bytes(b'stale mount')
            with self.assertRaises(SaveError):
                host_path('/run/cpak/grants/id/folder/file.docx', process, host_mounts)
            visible.unlink(); visible.symlink_to(original)
            with self.assertRaises(SaveError):
                host_path('/run/cpak/grants/id/folder/file.docx', process, host_mounts)

    def test_mountinfo_unescapes_spaces_and_backslashes(self):
        with tempfile.TemporaryDirectory() as temp:
            path = Path(temp) / 'mountinfo'
            path.write_text('1 0 8:1 /with\\040space /with\\134slash rw - ext4 /dev/test rw\n')
            self.assertEqual(mounts(path), [('8:1', Path('/with space'), Path('/with\\slash'), 'ext4')])


class NamespaceRelayTests(unittest.TestCase):
    def test_relay_outlasts_backed_off_office_retries_then_exits(self):
        # A 60-second retry runs on the next Office timer tick. Exercise both
        # the first connection after wakeup and a subsequent backed-off request.
        elapsed = 0
        attempts = [61, 122]

        def accept():
            nonlocal elapsed
            elapsed += 1
            self.assertLess(elapsed, 300, "Unused relay should exit")
            if attempts and elapsed == attempts[0]:
                attempts.pop(0)
                return Mock(), None
            raise socket.timeout

        with tempfile.TemporaryDirectory() as directory, patch.dict(os.environ, XDG_RUNTIME_DIR=directory), \
                patch("twodrive_office.wine_namespace.namespace_listener") as listen, \
                patch("twodrive_office.wine_namespace.time.monotonic", side_effect=lambda: elapsed), \
                patch("twodrive_office.wine_namespace.concurrent.futures.ThreadPoolExecutor") as pool:
            listen.return_value.__enter__.return_value.accept.side_effect = accept
            pool.return_value.__enter__.return_value.submit.return_value.done.return_value = True
            with open(os.devnull, "wb") as sink:
                relay(os.getpid(), 19836, os.dup(sink.fileno()))
            self.assertEqual(attempts, [])
            self.assertFalse(list(Path(directory).rglob("wine-*.lock")))

    def test_real_namespace_idle_relay_exits_while_container_stays_alive(self):
        with tempfile.TemporaryDirectory(prefix='twodrive-idle-relay-test-') as directory:
            root = Path(directory)
            script = "import subprocess,sys; subprocess.run(['ip','link','set','lo','up'],check=True); print('ready',flush=True); sys.stdin.read()"
            namespace = subprocess.Popen(['unshare', '--user', '--map-root-user', '--net',
                                          sys.executable, '-c', script], stdin=subprocess.PIPE,
                                         stdout=subprocess.PIPE, stderr=subprocess.PIPE,
                                         text=True, start_new_session=True)
            child = None
            try:
                self.assertEqual(namespace.stdout.readline().strip(), 'ready')
                for connect in (False, True):
                    with self.subTest(connect=connect):
                        with socket.socket() as free:
                            free.bind(('127.0.0.1', 0)); port = free.getsockname()[1]
                        read_fd, write_fd = os.pipe()
                        try:
                            code = ('import sys; from twodrive_office.wine_namespace import relay; '
                                    'relay(int(sys.argv[1]), int(sys.argv[2]), int(sys.argv[3]), '
                                    'startup_timeout=0.2, idle_timeout=0.2)')
                            child = subprocess.Popen([sys.executable, '-c', code, str(namespace.pid),
                                                      str(port), str(write_fd)], pass_fds=(write_fd,),
                                env=dict(os.environ, XDG_RUNTIME_DIR=str(root), PYTHONDONTWRITEBYTECODE='1'),
                                start_new_session=True, stdout=subprocess.DEVNULL, stderr=subprocess.PIPE)
                        finally:
                            os.close(write_fd)
                        try:
                            self.assertTrue(select.select([read_fd], [], [], 5)[0])
                            self.assertEqual(os.read(read_fd, 16), b'ready')
                            if connect:
                                subprocess.run(['nsenter', '-t', str(namespace.pid), '-U', '-n',
                                    '--preserve-credentials', sys.executable, '-c',
                                    "import socket,sys; socket.create_connection(('127.0.0.1',int(sys.argv[1]))).close()", str(port)],
                                    check=True, timeout=3)
                            child.wait(timeout=5)
                            self.assertEqual(child.returncode, 0, child.stderr.read().decode())
                            self.assertIsNone(namespace.poll(), 'The test container must remain alive')
                            self.assertFalse(list(root.rglob('wine-*.lock')))
                        finally:
                            os.close(read_fd)
                            if child.poll() is None:
                                os.killpg(child.pid, signal.SIGTERM); child.wait(timeout=5)
                            child.stderr.close()
            finally:
                namespace.stdin.close()
                if namespace.poll() is None:
                    os.killpg(namespace.pid, signal.SIGTERM); namespace.wait(timeout=5)
                namespace.stdout.close(); namespace.stderr.close()

    def test_relay_exits_while_target_process_is_still_alive(self):
        for connect in (False, True):
            with self.subTest(connect=connect), tempfile.TemporaryDirectory() as directory:
                listener = socket.socket()
                listener.bind(("127.0.0.1", 0)); listener.listen()
                port = listener.getsockname()[1]
                read_fd, write_fd = os.pipe()
                errors = []
                def run():
                    try:
                        relay(os.getpid(), port, write_fd, startup_timeout=0.1, idle_timeout=0.1)
                    except Exception as error:
                        errors.append(error)
                with patch.dict(os.environ, XDG_RUNTIME_DIR=directory), patch(
                        "twodrive_office.wine_namespace.namespace_listener", return_value=listener), patch(
                        "twodrive_office.wine_namespace.forward", side_effect=lambda client, *args: client.close()):
                    thread = threading.Thread(target=run)
                    thread.start()
                    try:
                        self.assertTrue(select.select([read_fd], [], [], 3)[0])
                        self.assertEqual(os.read(read_fd, 16), b"ready")
                        if connect:
                            with socket.create_connection(("127.0.0.1", port), timeout=2):
                                pass
                        thread.join(timeout=4)
                        self.assertFalse(thread.is_alive(), "An unchanged container PID must not retain the relay")
                        self.assertFalse(errors, errors)
                        self.assertTrue(Path('/proc/self').exists())
                        self.assertFalse(list(Path(directory).rglob('wine-*.lock')))
                    finally:
                        listener.close()
                        thread.join(timeout=3)
                        os.close(read_fd)

    def test_forwarding_stops_even_when_peers_do_not_close(self):
        client, editor = socket.socketpair()
        upstream, host = socket.socketpair()
        stop = threading.Event()
        with patch("twodrive_office.wine_namespace.socket.create_connection", return_value=upstream):
            thread = threading.Thread(target=forward, args=(client, 12345, stop))
            thread.start()
            try:
                editor.sendall(b"request")
                host.settimeout(2)
                self.assertEqual(host.recv(7), b"request")
                stop.set()
                thread.join(timeout=2)
                self.assertFalse(thread.is_alive())
            finally:
                stop.set(); editor.close(); host.close()
                thread.join(timeout=3)

    def test_live_grant_mount_resolves_only_the_matching_private_prefix(self):
        # This is a named Python fixture, not a real Office application. Its
        # private mount namespace exercises the public Wine path resolver.
        with tempfile.TemporaryDirectory(prefix='twodrive-grant-test-') as temp:
            root = Path(temp)
            prefix = root / 'prefix'
            (prefix / 'drive_c').mkdir(parents=True)
            (prefix / 'dosdevices').mkdir()
            (prefix / 'user.reg').touch()
            (prefix / 'dosdevices/z:').symlink_to('/')
            adapter = prefix / 'drive_c/users/test/AppData/Local/TwoDrive-Office-Wine'
            adapter.mkdir(parents=True)
            (prefix / 'drive_c/users/steamuser').symlink_to('test')
            settings = Settings(root, root / 'copies', require_mount=False,
                                bridge_token='private-wakeup-' + 'x' * 32)
            (adapter / 'bridge.ini').write_text(
                f'[bridge]\nport={settings.bridge_port}\ntoken={settings.bridge_token}\n'
                f'prefix={prefix}\nsandbox_wakeup=1\n', encoding='utf-16')
            original = root / 'mock folder' / 'Document.DOCX'
            original.parent.mkdir(); original.write_bytes(b'private path fixture')
            script = """
import ctypes, pathlib, subprocess, sys
subprocess.run(['mount','--make-rprivate','/'],check=True)
subprocess.run(['mount','-t','tmpfs','tmpfs','/run'],check=True)
target='/run/cpak/grants/test/mock folder'
pathlib.Path(target).mkdir(parents=True)
subprocess.run(['mount','--bind',sys.argv[1],target],check=True)
ctypes.CDLL(None).prctl(15,b'POWERPNT.EXE',0,0,0)
print('ready',flush=True)
sys.stdin.read()
"""
            environment = dict(os.environ, WINEPREFIX=str(prefix))
            process = subprocess.Popen(['unshare', '--user', '--map-root-user', '--mount',
                                        sys.executable, '-c', script, str(original.parent)],
                                       env=environment, stdin=subprocess.PIPE, stdout=subprocess.PIPE,
                                       stderr=subprocess.PIPE, text=True, start_new_session=True)
            try:
                self.assertEqual(process.stdout.readline().strip(), 'ready',
                                 'Private mount namespace did not start')
                source = 'Z:\\run\\cpak\\grants\\test\\mock folder\\document.docx'
                self.assertEqual(resolve_wine_path(str(prefix), source), str(original))
                trigger = adapter / 'relay.request'
                protected = root / 'protected'; protected.write_bytes(b'preserve')
                trigger.symlink_to(protected)
                with self.assertRaises(SaveError):
                    requested_namespace(prefix, settings)
                self.assertEqual(protected.read_bytes(), b'preserve')
                trigger.unlink(); trigger.mkdir()
                self.assertEqual(requested_namespace(prefix, settings), process.pid)
                self.assertFalse(trigger.exists())
                trigger.mkdir()
                with self.assertRaises(SaveError):
                    requested_namespace(prefix, replace(settings, bridge_token='different-' + 'y' * 32))
                self.assertFalse(trigger.exists())
                outside = root / 'outside'; outside.write_bytes(b'preserve')
                original.unlink(); original.symlink_to(outside)
                with self.assertRaises(SaveError):
                    resolve_wine_path(str(prefix), source)
                self.assertEqual(outside.read_bytes(), b'preserve')
                process.stdin.close(); process.wait(timeout=5)
                trigger.mkdir()
                self.assertIsNone(requested_namespace(prefix, settings))
                with self.assertRaises(SaveError):
                    resolve_wine_path(str(prefix), source)
            finally:
                if process.poll() is None:
                    os.killpg(process.pid, signal.SIGTERM); process.wait(timeout=5)
                for pipe in (process.stdin, process.stdout, process.stderr):
                    if pipe and not pipe.closed: pipe.close()

    def test_isolated_loopback_auth_save_and_relay_lifetime(self):
        # cpak creates the network in one user namespace, then runs Office in
        # a nested user namespace. Only temporary mock documents/settings are
        # ever passed to the bridge.
        with tempfile.TemporaryDirectory(prefix='twodrive-net-test-') as temp:
            root = Path(temp); mount = root / 'mount'; mount.mkdir()
            runtime = root / 'runtime'; runtime.mkdir(mode=0o700)
            settings = Settings(mount, mount / 'copies', require_mount=False,
                                bridge_token='namespace-test-' + os.urandom(24).hex())
            bridge = Bridge(settings, state_root=root / 'state')
            server = make_server(bridge, 0)
            thread = threading.Thread(target=server.serve_forever); thread.start()
            script = """
import subprocess,sys
subprocess.run(['ip','link','set','lo','up'],check=True)
code="import os,sys; print('ready '+str(os.getpid()),flush=True); sys.stdin.read()"
sys.exit(subprocess.call(['unshare','--user','--map-root-user',sys.executable,'-c',code]))
"""
            namespace = subprocess.Popen(['unshare', '--user', '--map-root-user', '--net',
                                          sys.executable, '-c', script], stdin=subprocess.PIPE,
                                         stdout=subprocess.PIPE, stderr=subprocess.PIPE,
                                         text=True, start_new_session=True)
            relay = None
            try:
                ready = namespace.stdout.readline().strip().split()
                if len(ready) != 2 or ready[0] != 'ready':
                    self.fail('Private user/network namespace unavailable: ' + namespace.stderr.read())
                editor_pid = int(ready[1])
                self.assertNotEqual(os.readlink(f'/proc/{editor_pid}/ns/user'),
                                    os.readlink(f'/proc/{namespace.pid}/ns/user'))
                with open(f'/proc/{editor_pid}/ns/net', 'rb') as network:
                    owner = fcntl.ioctl(network, 0xb701)  # NS_GET_USERNS
                try:
                    self.assertTrue(os.path.samestat(os.fstat(owner),
                                                     os.stat(f'/proc/{namespace.pid}/ns/user')))
                finally:
                    os.close(owner)
                old = subprocess.run(['nsenter', '-t', str(editor_pid), '-U', '-n',
                                      '--preserve-credentials', 'true'], capture_output=True, timeout=5)
                self.assertNotEqual(old.returncode, 0, 'The fixture must reproduce the old namespace failure')
                port = server.server_port
                command = ['nsenter', '-t', str(namespace.pid), '-U', '-n', '--preserve-credentials', sys.executable, '-c']
                before = subprocess.check_output(command + [
                    f"import socket; s=socket.socket(); print(s.connect_ex(('127.0.0.1',{port})))"], text=True)
                self.assertNotEqual(before.strip(), '0')
                with patch.dict(os.environ, XDG_RUNTIME_DIR=str(runtime), PYTHONDONTWRITEBYTECODE='1'):
                    relay = ensure_relay(editor_pid, port)
                    duplicate = ensure_relay(editor_pid, port)
                    duplicate.wait(timeout=5)
                    self.assertEqual(duplicate.returncode, 0)
                # stdin supplies the random credential, keeping it off command
                # lines and diagnostic output. Auth failures remain rejected.
                probe = """
import http.client, io, json, sys, zipfile
port, token, mount = json.load(sys.stdin)
def request(method, path, body=None, authenticated=True):
 c=http.client.HTTPConnection('127.0.0.1',port,timeout=5)
 headers={'Authorization':'Bearer '+token} if authenticated else {}
 c.request(method,path,body=json.dumps(body) if body is not None else None,headers=headers)
 r=c.getresponse(); status=r.status; data=json.loads(r.read()); c.close(); return status,data
assert request('GET','/settings',authenticated=False)[0]==401
status,data=request('GET','/settings'); assert status==200 and data['mount_root']==mount
status,data=request('POST','/sessions',{'title':'isolated','extension':'docx','document_id':'net-test'})
assert status==200 and data['in_place'] is False
key=data['session_id']
out=io.BytesIO()
with zipfile.ZipFile(out,'w') as z:
 z.writestr('[Content_Types].xml','<Types/>')
 z.writestr('word/document.xml','<document>namespace mock save</document>')
c=http.client.HTTPConnection('127.0.0.1',port,timeout=5)
c.request('POST','/sessions/'+key,body=out.getvalue(),headers={'Authorization':'Bearer '+token})
r=c.getresponse(); assert r.status==200; r.read(); c.close()
assert request('DELETE','/sessions/'+key)[0]==200
print('authenticated mock session passed')
"""
                result = subprocess.check_output(command + [probe], input=json.dumps([port, settings.bridge_token, str(mount)]), text=True)
                self.assertIn('passed', result)
                copies = list((mount / 'copies').glob('*.docx'))
                self.assertEqual(len(copies), 1)
                # Optional native probe is cross-compiled from the real DLL
                # transport. It never creates an Office COM application.
                executable = os.environ.get('TWODRIVE_TEST_HTTP_EXE')
                runner = os.environ.get('TWODRIVE_TEST_WINE')
                if executable and runner:
                    prefix = root / 'wine-prefix'
                    environment = dict(os.environ, WINEPREFIX=str(prefix), WINEDEBUG='-all',
                                       TWODRIVE_TEST_TOKEN=settings.bridge_token,
                                       WINEDLLOVERRIDES='winewayland.drv=d;winex11.drv=d;winemenubuilder.exe=d',
                                       XDG_CONFIG_HOME=str(root / 'config'), XDG_DATA_HOME=str(root / 'data'),
                                       XDG_CACHE_HOME=str(root / 'cache'),
                                       WINELOADER=runner, WINESERVER=str(Path(runner).parent / 'wineserver'))
                    for key in ('DISPLAY', 'WAYLAND_DISPLAY', 'DBUS_SESSION_BUS_ADDRESS', 'SESSION_MANAGER'):
                        environment.pop(key, None)
                    try:
                        native = subprocess.Popen(command[:-2] + [runner, executable, str(port)],
                                                  env=environment, start_new_session=True,
                                                  stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True)
                        output, errors = native.communicate(timeout=60)
                        self.assertEqual(native.returncode, 0, output + errors)
                        self.assertIn('WinHTTP failure/reconnect', output)
                    finally:
                        from wine_integration import stop_prefix_groups
                        stop_prefix_groups(prefix)
                        if 'native' in locals():
                            native.wait(timeout=5)
                            native.stdout.close(); native.stderr.close()
                self.assertFalse(bridge.sessions)
                self.assertFalse(bridge.local_saves.pending())
                namespace.stdin.close(); namespace.wait(timeout=5)
                relay.wait(timeout=5)
                self.assertEqual(relay.returncode, 0)
                self.assertFalse(list(runtime.rglob('wine-*.lock')))
            finally:
                if namespace.poll() is None:
                    os.killpg(namespace.pid, signal.SIGTERM); namespace.wait(timeout=5)
                for pipe in (namespace.stdin, namespace.stdout, namespace.stderr):
                    if pipe and not pipe.closed: pipe.close()
                if relay and relay.poll() is None:
                    relay.terminate(); relay.wait(timeout=5)
                server.shutdown(); server.server_close(); thread.join()
                bridge.local_saves.shutdown()


if __name__ == '__main__':
    unittest.main()

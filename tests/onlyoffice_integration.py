#!/usr/bin/env python3
"""Real ONLYOFFICE test; isolated profile/instance port and authenticated TCP Xvfb.

Requires websocket-client, flatpak, xauth, xdpyinfo and Xvfb. Never uses the
user's display or profile. Synthetic input, output and SDK runtime are removed.
"""
import sys, os, json, shutil, socket, subprocess, time, signal, threading, zipfile, urllib.request, shlex, re, base64, fcntl
from pathlib import Path
PROJECT = Path(__file__).resolve().parents[1]
os.chdir(PROJECT)
sys.path.insert(0, str(PROJECT))
import argparse
parser = argparse.ArgumentParser(description='ONLYOFFICE integration test on an authenticated virtual display')
parser.add_argument('--xvfb', default=shutil.which('Xvfb'))
parser.add_argument('--format', choices=['docx', 'xlsx', 'pptx'], default='docx')
parser.add_argument('--rich', action='store_true')
parser.add_argument('--in-place', action='store_true')
parser.add_argument('--untitled', action='store_true')
parser.add_argument('--race', action='store_true', help='Edit again while the bridge delays acknowledgement')
options = parser.parse_args()
# The native debug switch uses a shared loopback port on current Desktop.
# Serialize this suite rather than attaching to another test's editor.
lock_path = PROJECT / '.test-state/onlyoffice-integration.lock'
lock_path.parent.mkdir(exist_ok=True)
integration_lock = lock_path.open('a')
fcntl.flock(integration_lock, fcntl.LOCK_EX)
if options.rich and options.format != 'docx':
    parser.error('--rich is a DOCX fixture option')
if options.untitled and options.in_place:
    parser.error('Untitled documents must be saved as copies')
if options.race and not options.in_place:
    parser.error('--race requires --in-place')
xvfb_path = options.xvfb
if not xvfb_path:
    parser.error('Provide --xvfb /absolute/path/to/Xvfb; the test never uses the normal display')
if not shutil.which('flatpak'):
    parser.error('This test requires the ONLYOFFICE Flatpak')
from twodrive_office.policy import Settings
from twodrive_office.bridge import Bridge, make_server
import websocket
import tempfile, atexit
root = Path(tempfile.mkdtemp(prefix='onlyoffice-'))
atexit.register(shutil.rmtree, root, True)

def stop_group(process):
    if getattr(process, '_test_group_stopped', False):
        return

    def alive():
        for proc in Path('/proc').iterdir():
            if not proc.name.isdigit():
                continue
            try:
                fields = (proc / 'stat').read_text().rsplit(') ', 1)[1].split()
                if int(fields[2]) == process.pid and fields[0] != 'Z':
                    return True
            except (FileNotFoundError, ProcessLookupError, PermissionError):
                pass
        return False

    if alive():
        try:
            os.killpg(process.pid, signal.SIGTERM)
        except ProcessLookupError:
            pass
        deadline = time.monotonic() + 5
        while alive() and time.monotonic() < deadline:
            time.sleep(0.1)
        if alive():
            os.killpg(process.pid, signal.SIGKILL)
            deadline = time.monotonic() + 3
            while alive() and time.monotonic() < deadline:
                time.sleep(0.1)
    process.wait(timeout=3)
    assert not alive(), 'The dedicated integration-test process group did not exit'
    process._test_group_stopped = True

def port():
    with socket.socket() as s:
        s.bind(('127.0.0.1', 0))
        return s.getsockname()[1]

def text_markers(binary):
    # Undo can regenerate serialization IDs. Compare the fixture's actual text,
    # rather than assuming two equivalent models have identical native bytes.
    data = base64.b64decode(binary.split(';', 3)[3])
    markers = ['ONLYOFFICE actual unsaved edits', 'Saved while acknowledgement is delayed',
               'New edits during the delayed save', 'Edit saved after a lost reply', 'Disabled edits']
    return {text for text in markers if text.encode('utf-16le') in data or text.encode() in data}
config = root / 'config'
data = root / 'data'
cache = root / 'cache'
mount = root / 'mount'
mount.mkdir(exist_ok=True)
settings = Settings(mount, mount / 'Office AutoSave', require_mount=False, bridge_token='isolated-test-token-only-' + 'x' * 32)
bridge = make_server(Bridge(settings), 0)
delay_ack, ack_written, release_ack = threading.Event(), threading.Event(), threading.Event()
base_handler = bridge.RequestHandlerClass

class DelayedAcknowledgement(base_handler):
    def respond(self, status, data):
        if data.get('status') == 'saved_local' and delay_ack.is_set():
            delay_ack.clear()
            ack_written.set()
            assert release_ack.wait(20), 'Test did not release the committed save acknowledgement'
        return super().respond(status, data)

bridge.RequestHandlerClass = DelayedAcknowledgement
threading.Thread(target=bridge.serve_forever, daemon=True).start()
guid = '{F2C67A12-E94D-4EF6-8C32-20D71F3B0294}'
# Desktop 10 moved the native user plugin registry under the data/ subtree.
app_version = subprocess.check_output(['flatpak', 'info', 'org.onlyoffice.desktopeditors'], text=True, env={**os.environ, 'LC_ALL': 'C'})
major = int(re.search(r'Version:\s*(\d+)', app_version).group(1))
plugins = data / ('onlyoffice/desktopeditors/data/sdkjs-plugins' if major >= 10 else 'onlyoffice/desktopeditors/sdkjs-plugins') / guid
shutil.copytree('onlyoffice', plugins, dirs_exist_ok=True)
(plugins / 'settings.js').write_text('window.TWODRIVE_BRIDGE=' + json.dumps({'url': f'http://127.0.0.1:{bridge.server_port}', 'token': settings.bridge_token}) + ';')
extension = options.format
fixture = (mount if options.in_place else root) / ('source.' + extension)
with zipfile.ZipFile(fixture, 'w') as z:
    z.writestr('[Content_Types].xml', '<?xml version="1.0"?><Types xmlns="http://schemas.openxmlformats.org/package/2006/content-types"><Default Extension="rels" ContentType="application/vnd.openxmlformats-package.relationships+xml"/><Default Extension="xml" ContentType="application/xml"/><Override PartName="/word/document.xml" ContentType="application/vnd.openxmlformats-officedocument.wordprocessingml.document.main+xml"/></Types>')
    z.writestr('_rels/.rels', '<?xml version="1.0"?><Relationships xmlns="http://schemas.openxmlformats.org/package/2006/relationships"><Relationship Id="rId1" Type="http://schemas.openxmlformats.org/officeDocument/2006/relationships/officeDocument" Target="word/document.xml"/></Relationships>')
    z.writestr('word/document.xml', '<?xml version="1.0"?><w:document xmlns:w="http://schemas.openxmlformats.org/wordprocessingml/2006/main"><w:body><w:p><w:r><w:t>TwoDrive isolated original</w:t></w:r></w:p><w:sectPr/></w:body></w:document>')
if options.rich:
    import zlib, struct

    def chunk(kind, data):
        return struct.pack('>I', len(data)) + kind + data + struct.pack('>I', zlib.crc32(kind + data) & 4294967295)
    png = b'\x89PNG\r\n\x1a\n' + chunk(b'IHDR', struct.pack('>IIBBBBB', 1, 1, 8, 2, 0, 0, 0)) + chunk(b'IDAT', zlib.compress(b'\x00\xff\x00\x00')) + chunk(b'IEND', b'')
    with zipfile.ZipFile(fixture) as z:
        entries = {name: z.read(name) for name in z.namelist()}
    entries['[Content_Types].xml'] = entries['[Content_Types].xml'].replace(b'</Types>', b'<Default Extension="png" ContentType="image/png"/></Types>')
    entries['word/_rels/document.xml.rels'] = b'<Relationships xmlns="http://schemas.openxmlformats.org/package/2006/relationships"><Relationship Id="image1" Type="http://schemas.openxmlformats.org/officeDocument/2006/relationships/image" Target="media/image1.png"/></Relationships>'
    entries['word/media/image1.png'] = png
    drawing = '<w:p><w:r><w:drawing><wp:inline xmlns:wp="http://schemas.openxmlformats.org/drawingml/2006/wordprocessingDrawing"><wp:extent cx="914400" cy="914400"/><wp:docPr id="1" name="Fixture picture"/><a:graphic xmlns:a="http://schemas.openxmlformats.org/drawingml/2006/main"><a:graphicData uri="http://schemas.openxmlformats.org/drawingml/2006/picture"><pic:pic xmlns:pic="http://schemas.openxmlformats.org/drawingml/2006/picture"><pic:nvPicPr><pic:cNvPr id="0" name="image1.png"/><pic:cNvPicPr/></pic:nvPicPr><pic:blipFill><a:blip xmlns:r="http://schemas.openxmlformats.org/officeDocument/2006/relationships" r:embed="image1"/><a:stretch><a:fillRect/></a:stretch></pic:blipFill><pic:spPr><a:xfrm><a:off x="0" y="0"/><a:ext cx="914400" cy="914400"/></a:xfrm><a:prstGeom prst="rect"><a:avLst/></a:prstGeom></pic:spPr></pic:pic></a:graphicData></a:graphic></wp:inline></w:drawing></w:r></w:p>'
    entries['word/document.xml'] = entries['word/document.xml'].replace(b'<w:sectPr/>', drawing.encode() + b'<w:sectPr/>')
    with zipfile.ZipFile(fixture, 'w') as z:
        for name, content in entries.items():
            z.writestr(name, content)
if extension != 'docx':
    app_root = Path(subprocess.check_output(['flatpak', 'info', '--show-location', 'org.onlyoffice.desktopeditors'], text=True).strip())
    template = app_root / 'files/bin/opt/onlyoffice/desktopeditors/converter/empty/en-US' / ('new.' + extension)
    shutil.copyfile(template, fixture)
original_bytes = fixture.read_bytes()
auth = root / 'xauthority'
cookie = os.urandom(16).hex()
if auth.exists():
    auth.unlink()
number = None
for candidate in range(100, 200):
    with socket.socket() as probe:
        try:
            probe.bind(('127.0.0.1', 6000 + candidate))
            number = candidate
            break
        except OSError:
            pass
assert number is not None, 'no private display port available'
display = '127.0.0.1:' + str(number)
subprocess.run(['xauth', '-f', str(auth), 'add', display, 'MIT-MAGIC-COOKIE-1', cookie], check=True, capture_output=True)
xf = open(root / 'xvfb.log', 'w')
xvfb = subprocess.Popen([xvfb_path, ':' + str(number), '-screen', '0', '1280x900x24', '-listen', 'tcp', '-auth', str(auth)], stdout=xf, stderr=xf, start_new_session=True)
atexit.register(stop_group, xvfb)
for n in range(40):
    check = subprocess.run(['xdpyinfo'], env={**os.environ, 'DISPLAY': display, 'XAUTHORITY': str(auth)}, capture_output=True, text=True)
    if check.returncode == 0:
        break
    time.sleep(0.1)
assert check.returncode == 0 and '1280x900 pixels' in check.stdout
print('Authenticated virtual display verified:', display, flush=True)
opts = ['nice', '-n', '10', 'flatpak', 'run', '--nosocket=wayland', '--nosocket=pulseaudio', '--nosocket=x11', '--env=DISPLAY=' + display, '--env=XDG_CURRENT_DESKTOP=', '--env=NO_AT_BRIDGE=1', '--command=sh', 'org.onlyoffice.desktopeditors']

def launch_args(args):
    script = 'unset WAYLAND_DISPLAY SESSION_MANAGER DBUS_SESSION_BUS_ADDRESS; export DISPLAY=' + shlex.quote(display) + ' XAUTHORITY=' + shlex.quote(str(auth)) + ' XDG_CONFIG_HOME=' + shlex.quote(str(config)) + ' XDG_DATA_HOME=' + shlex.quote(str(data)) + ' XDG_CACHE_HOME=' + shlex.quote(str(cache)) + '; exec desktopeditors ' + shlex.join(args)
    return opts + ['-c', script]

def close_test_window():
    """Send the window manager's close request only on our authenticated display."""
    environment = {**os.environ, 'DISPLAY': display, 'XAUTHORITY': str(auth)}
    tree = subprocess.check_output(['xwininfo', '-root', '-tree'], env=environment, text=True)
    windows = re.findall(r'^     (0x[0-9a-f]+).*\("DesktopEditors" "ONLYOFFICE"\)', tree, re.M)
    assert len(windows) == 1, 'Could not uniquely identify the isolated native editor window: ' + tree
    import ctypes
    from ctypes.util import find_library
    x11 = ctypes.CDLL(find_library('X11'))
    x11.XOpenDisplay.argtypes = [ctypes.c_char_p]
    x11.XOpenDisplay.restype = ctypes.c_void_p
    x11.XInternAtom.argtypes = [ctypes.c_void_p, ctypes.c_char_p, ctypes.c_int]
    x11.XInternAtom.restype = ctypes.c_ulong
    x11.XSendEvent.argtypes = [ctypes.c_void_p, ctypes.c_ulong, ctypes.c_int, ctypes.c_long, ctypes.c_void_p]
    x11.XFlush.argtypes = [ctypes.c_void_p]
    x11.XCloseDisplay.argtypes = [ctypes.c_void_p]

    class ClientMessage(ctypes.Structure):
        _fields_ = [('type', ctypes.c_int), ('serial', ctypes.c_ulong), ('send_event', ctypes.c_int),
                    ('display', ctypes.c_void_p), ('window', ctypes.c_ulong), ('message_type', ctypes.c_ulong),
                    ('format', ctypes.c_int), ('data', ctypes.c_long * 5)]

    class Event(ctypes.Union):
        _fields_ = [('message', ClientMessage), ('pad', ctypes.c_long * 24)]

    old_auth = os.environ.get('XAUTHORITY')
    os.environ['XAUTHORITY'] = str(auth)
    connection = x11.XOpenDisplay(display.encode())
    if old_auth is None:
        os.environ.pop('XAUTHORITY', None)
    else:
        os.environ['XAUTHORITY'] = old_auth
    assert connection, 'Could not authenticate the isolated close request'
    try:
        event = Event()
        event.message.type = 33  # ClientMessage
        event.message.display = connection
        event.message.window = int(windows[0], 16)
        event.message.message_type = x11.XInternAtom(connection, b'WM_PROTOCOLS', 0)
        event.message.format = 32
        event.message.data[0] = x11.XInternAtom(connection, b'WM_DELETE_WINDOW', 0)
        assert x11.XSendEvent(connection, event.message.window, 0, 0, ctypes.byref(event))
        x11.XFlush(connection)
    finally:
        x11.XCloseDisplay(connection)

instance = port()
debug = port()
app = None
tested = False
try:
    subprocess.run(launch_args(['--set-instapp-port=' + str(instance)]), check=True, timeout=15, capture_output=True)
    assert (config / 'onlyoffice/DesktopEditors.conf').exists(), 'profile redirection failed'
    assert 'instAppPort=' + str(instance) in (config / 'onlyoffice/DesktopEditors.conf').read_text(), 'instance port not isolated'
    print('Isolated profile and native instance port verified', flush=True)
    log = open(root / 'app.log', 'w')
    open_argument = '--new:' + {'docx': 'word', 'xlsx': 'cell', 'pptx': 'slide'}[extension] if options.untitled else str(fixture)
    app = subprocess.Popen(launch_args(['--ascdesktop-support-debug-info', '--remote-debugging-port=' + str(debug), '--disable-gpu', open_argument]), stdout=log, stderr=log, start_new_session=True)
    atexit.register(stop_group, app)
    time.sleep(1)
    actual_environment = False
    for proc in Path('/proc').iterdir():
        if not proc.name.isdigit():
            continue
        try:
            if (proc / 'comm').read_text().strip() != 'DesktopEditors':
                continue
            if os.getpgid(int(proc.name)) != app.pid:
                continue
            environment = dict((part.split(b'=', 1) for part in (proc / 'environ').read_bytes().split(b'\x00') if b'=' in part))
            assert environment.get(b'DISPLAY') == display.encode(), 'Editor attempted to use an unexpected display'
            assert environment.get(b'XDG_CONFIG_HOME') == str(config).encode(), 'Editor profile is not isolated'
            assert environment.get(b'XDG_DATA_HOME') == str(data).encode(), 'Editor data is not isolated'
            actual_environment = True
        except (FileNotFoundError, PermissionError):
            continue
    assert actual_environment, 'Could not verify the editor process environment'
    base = f'http://127.0.0.1:{debug}'
    pages = []
    for n in range(90):
        try:
            match = re.search(r'DevTools listening on ws://127\.0\.0\.1:(\d+)', (root / 'app.log').read_text())
            if match:
                base = 'http://127.0.0.1:' + match.group(1)
            pages = json.load(urllib.request.urlopen(base + '/json', timeout=2))
            if len(pages) > 1:
                break
        except Exception:
            pass
        time.sleep(0.5)
    assert pages, 'ONLYOFFICE debug target not available'
    (root / 'debug.json').write_text(json.dumps({'debug_port': debug, 'pages': pages}))
    tested = False
    for p in pages:
        if 'editors/' not in p.get('url', ''):
            continue
        ws = websocket.create_connection(p['webSocketDebuggerUrl'], suppress_origin=True, timeout=10)
        seq = 0
        contexts = {}

        def call(method, params):
            global seq
            seq += 1
            ws.send(json.dumps({'id': seq, 'method': method, 'params': params}))
            while True:
                r = json.loads(ws.recv())
                if r.get('method') == 'Runtime.executionContextCreated':
                    c = r['params']['context']
                    contexts[c['id']] = c
                if r.get('id') == seq:
                    return r

        def ev(expr, cid=None):
            params = {'expression': expr, 'returnByValue': True, 'awaitPromise': True}
            if cid:
                params['contextId'] = cid
            return call('Runtime.evaluate', params)
        call('Runtime.enable', {})
        call('Network.enable', {})
        time.sleep(5)
        call('Page.getFrameTree', {})
        editor_context = None
        for cid in list(contexts):
            result = ev('!!(window.Asc&&Asc.editor)', cid)
            if result.get('result', {}).get('result', {}).get('value'):
                editor_context = cid
        if not editor_context:
            ws.close()
            continue
        assert ev('AscDesktopEditor.LocalFileGetSaved()?AscDesktopEditor.LocalFileGetSourcePath():""', editor_context)['result']['result']['value'] == ('' if options.untitled else str(fixture)), 'Debug target is not the isolated test document'
        ev('Array.from(document.querySelectorAll("iframe")).map(f=>({id:f.id,src:f.src}))', editor_context)
        call('Page.getFrameTree', {})
        plugin_context = None
        for attempt in range(20):
            for cid in list(contexts):
                result = ev('!!document.getElementById("enabled")', cid)
                if result.get('result', {}).get('result', {}).get('value'):
                    plugin_context = cid
            if plugin_context:
                break
            time.sleep(0.5)
            call('Page.getFrameTree', {})
        if not plugin_context:
            print('Isolated editor frames:', ev('Array.from(document.querySelectorAll("iframe")).map(f=>({id:f.id,src:f.src}))', editor_context), flush=True)
            print('Isolated plugin registry:', ev('typeof AscDesktopEditor.GetPlugins === "function" ? AscDesktopEditor.GetPlugins() : "no registry method"', editor_context), flush=True)
            print('Native plugin methods:', ev('Object.keys(AscDesktopEditor).filter(k=>/plugin/i.test(k))', editor_context), flush=True)
            print('Generated plugin configurations:', [str(p.relative_to(root)) for p in root.rglob('config.json')], flush=True)
            print('Isolated contexts:', list(contexts.values()), flush=True)
        assert plugin_context, 'plugin iframe did not load'
        print('Initial plugin state:', ev('document.getElementById("status").textContent', plugin_context), flush=True)
        detected = ev('document.getElementById("source").value', plugin_context)['result']['result']['value']
        assert detected == ('' if options.untitled else str(fixture)), 'native path detection failed: ' + detected
        print('PASS current source path is detected automatically', flush=True)
        for attempt in range(30):
            startup = ev('({enabled:document.getElementById("enabled").checked,busy:document.getElementById("enabled").disabled,status:document.getElementById("status").textContent})', plugin_context)['result']['result']['value']
            if not startup['busy'] and not startup['status'].startswith('正在'):
                break
            time.sleep(0.1)
        assert startup['enabled'] == options.in_place, 'Wrong automatic startup policy: ' + str(startup)
        assert not list(mount.rglob('*.' + extension)) or options.in_place, 'Startup wrote an outside/untitled copy'
        if options.in_place:
            assert fixture.read_bytes() == original_bytes, 'Opening an unchanged document rewrote it'
        print('PASS background starts without a manual plugin launch; only TwoDrive files enable automatically', flush=True)

        def open_panel():
            result = ev('Asc.plugin.event_onToolbarMenuClick("twodrive-panel")', plugin_context)
            assert not result.get('result', {}).get('exceptionDetails'), result
            for attempt in range(30):
                time.sleep(0.1)
                call('Page.getFrameTree', {})
                for cid in list(contexts):
                    result = ev('!!(window.Asc&&Asc.plugin.windowID&&document.getElementById("enabled"))', cid)
                    if result.get('result', {}).get('result', {}).get('value'):
                        for ready in range(20):
                            status = ev('document.getElementById("status").textContent', cid)['result']['result']['value']
                            if not status.startswith('正在连接'):
                                return cid
                            time.sleep(0.1)
            frames = ev('Array.from(document.querySelectorAll("iframe")).map(frame=>frame.src)', editor_context)
            raise AssertionError('The real toolbar could not open a synchronized control panel: ' + str(frames))

        if extension == 'pptx':
            panel_context = open_panel()
            assert ev('document.getElementById("enabled").checked', panel_context)['result']['result']['value'] == options.in_place
            ev('Asc.plugin.button()', panel_context)
            time.sleep(0.2)
            assert ev('document.getElementById("enabled").checked', plugin_context)['result']['result']['value'] == options.in_place
            print('PASS real toolbar panel reflects state; closing it preserves the background switch', flush=True)
        ev('window.testNativeDirty=null;window.testNativeSignals=[];window.testNativeModified=AscDesktopEditor.onDocumentModifiedChanged.bind(AscDesktopEditor);AscDesktopEditor.onDocumentModifiedChanged=function(value){window.testNativeDirty=value;window.testNativeSignals.push(value);return window.testNativeModified(value);}', editor_context)
        setup = 'document.getElementById("copy-directory").value=' + json.dumps(str(mount / 'Chosen by user')) + ';document.getElementById("enabled").checked=true;document.getElementById("enabled").dispatchEvent(new Event("change"));'
        if not options.in_place:
            ev(setup, plugin_context)
        commands = {'docx': 'var doc=Api.GetDocument();var p=Api.CreateParagraph();p.AddText("ONLYOFFICE actual unsaved edits");doc.Push(p);', 'xlsx': 'Api.GetActiveSheet().GetRange("A1").SetValue("ONLYOFFICE actual unsaved edits");Api.GetActiveSheet().GetRange("B1").SetValue("=1+2");', 'pptx': 'var fill=Api.CreateSolidFill(Api.CreateRGBColor(255,255,255));var shape=Api.CreateShape("rect",2500000,500000,fill,Api.CreateStroke(0,fill));shape.GetDocContent().GetElement(0).AddText("ONLYOFFICE actual unsaved edits");Api.GetPresentation().GetSlideByIndex(0).AddObject(shape);'}
        command = commands[extension]
        ev('new Promise(resolve=>Asc.plugin.callCommand(function(){' + command + '},false,true,()=>resolve(true)))', plugin_context)
        ev('if(typeof Asc.editor.UpdateInterfaceState === "function")Asc.editor.UpdateInterfaceState()', editor_context)
        for i in range(40):
            time.sleep(0.5)
            result = ev('({status:document.getElementById("status").textContent,target:document.getElementById("target").textContent,enabled:document.getElementById("enabled").checked})', plugin_context)
            files = [fixture] if options.in_place else list((mount / 'Chosen by user').glob('*.' + extension))
            saved_status = result.get('result', {}).get('result', {}).get('value', {}).get('status', '')
            if files and saved_status.startswith('已保存到'):
                with zipfile.ZipFile(files[0]) as z:
                    if any((b'ONLYOFFICE actual unsaved edits' in z.read(n) for n in z.namelist() if n.endswith('.xml'))):
                        print('PASS real ONLYOFFICE saves current edits ' + ('in place' if options.in_place else 'to selected copy path'), flush=True)
                        if options.rich:
                            images = [n for n in z.namelist() if n.startswith('word/media/') and n.endswith('.png')]
                            assert images and any((z.read(n) == png for n in images)), 'image media was lost'
                            print('PASS embedded picture bytes preserved', flush=True)
                        break
        else:
            raise AssertionError('real ONLYOFFICE save failed: ' + str(result))
        state = ev('({modified:Asc.editor.isDocumentModified(),nativeDirty:window.testNativeDirty})', editor_context)['result']['result']['value']
        assert state == {'modified': not options.in_place, 'nativeDirty': not options.in_place}, 'Wrong editor/native saved state: ' + str(state) + ' ' + saved_status
        if options.in_place:
            assert '编辑器状态已更新' in saved_status, saved_status
            print('PASS both editor dirty state and real native star notification are cleared', flush=True)
        else:
            assert '副本' in saved_status and '原文档' in saved_status, saved_status
            print('PASS copy save explicitly retains the original document warning', flush=True)
        # Polling, cursor events and retries can export the exact same model
        # under a new HTTP revision. They must not reconvert/replace its file.
        idle_fingerprint = (files[0].stat().st_ino, files[0].stat().st_mtime_ns, files[0].read_bytes())
        for _ in range(12):
            time.sleep(1)
        after_idle = (files[0].stat().st_ino, files[0].stat().st_mtime_ns, files[0].read_bytes())
        if after_idle != idle_fingerprint:
            (PROJECT / ('.test-state/idle-before.' + extension)).write_bytes(idle_fingerprint[2])
            (PROJECT / ('.test-state/idle-after.' + extension)).write_bytes(after_idle[2])
        assert after_idle == idle_fingerprint, 'idle autosave rewrote the same document'
        print('PASS unchanged native document remains byte/mtime/inode stable across idle polling', flush=True)
        if not options.in_place and (not options.untitled):
            with zipfile.ZipFile(fixture) as z:
                assert not any((b'ONLYOFFICE actual unsaved edits' in z.read(n) for n in z.namelist() if n.endswith('.xml')))
            print('PASS outside original remains unchanged', flush=True)
        if options.race:
            delay_ack.set()
            first = command.replace('ONLYOFFICE actual unsaved edits', 'Saved while acknowledgement is delayed')
            second = command.replace('ONLYOFFICE actual unsaved edits', 'New edits during the delayed save')
            ev('new Promise(resolve=>Asc.plugin.callCommand(function(){' + first + '},false,true,()=>resolve(true)))', plugin_context)
            ev('Asc.editor.UpdateInterfaceState()', editor_context)
            ev('document.getElementById("save-now").click()', plugin_context)
            assert ack_written.wait(15), 'Bridge did not reach the committed save acknowledgement'
            ev('new Promise(resolve=>Asc.plugin.callCommand(function(){' + second + '},false,true,()=>resolve(true)))', plugin_context)
            ev('Asc.editor.UpdateInterfaceState()', editor_context)
            ev('window.testNativeSignals=[]', editor_context)
            release_ack.set()
            for attempt in range(20):
                time.sleep(0.05)
                status = ev('document.getElementById("status").textContent', plugin_context)['result']['result']['value']
                if '新修改仍待保存' in status:
                    break
            else:
                raise AssertionError('The stale acknowledgement incorrectly reported the latest edit as saved: ' + status)
            state = ev('({modified:Asc.editor.isDocumentModified(),nativeDirty:window.testNativeDirty,cleared:window.testNativeSignals.includes(false)})', editor_context)['result']['result']['value']
            assert state == {'modified': True, 'nativeDirty': True, 'cleared': False}, 'Stale save cleared new edits: ' + str(state)
            print('PASS delayed acknowledgement never clears a newer real edit', flush=True)
            for attempt in range(40):
                time.sleep(0.25)
                status = ev('document.getElementById("status").textContent', plugin_context)['result']['result']['value']
                if '编辑器状态已更新' in status:
                    break
            else:
                raise AssertionError('New edit was not retried: ' + status)
            with zipfile.ZipFile(files[0]) as z:
                assert any(b'New edits during the delayed save' in z.read(name) for name in z.namelist() if name.endswith('.xml'))
            print('PASS newer edit is subsequently committed and acknowledged', flush=True)
            # The bridge writes successfully, but the caller loses the reply.
            # A retry without further typing must use a fresh submission version.
            ev('window.testFetch=window.fetch;window.fetch=async function(...args){const response=await window.testFetch(...args);if(String(args[0]).endsWith("/snapshot")&&response.ok){window.fetch=window.testFetch;throw new Error("Test: committed save reply lost");}return response;}', plugin_context)
            lost_reply_edit = command.replace('ONLYOFFICE actual unsaved edits', 'Edit saved after a lost reply')
            ev('new Promise(resolve=>Asc.plugin.callCommand(function(){' + lost_reply_edit + '},false,true,()=>resolve(true)))', plugin_context)
            ev('Asc.editor.UpdateInterfaceState()', editor_context)
            ev('document.getElementById("save-now").click()', plugin_context)
            for attempt in range(40):
                time.sleep(0.1)
                status = ev('document.getElementById("status").textContent', plugin_context)['result']['result']['value']
                if status.startswith('保存失败'):
                    break
            else:
                raise AssertionError('Lost-reply test did not leave the save pending: ' + status)
            state = ev('({modified:Asc.editor.isDocumentModified(),nativeDirty:window.testNativeDirty})', editor_context)['result']['result']['value']
            assert state == {'modified': True, 'nativeDirty': True}, 'A failed acknowledgement cleared the warning'
            for attempt in range(60):
                time.sleep(0.1)
                status = ev('document.getElementById("status").textContent', plugin_context)['result']['result']['value']
                if '编辑器状态已更新' in status:
                    break
            else:
                raise AssertionError('Lost-reply retry could not acknowledge without another edit: ' + status)
            with zipfile.ZipFile(files[0]) as z:
                assert any(b'Edit saved after a lost reply' in z.read(name) for name in z.namelist() if name.endswith('.xml'))
            print('PASS lost acknowledgement retains the warning and retries without further edits', flush=True)
        before = files[0].read_bytes()
        saved_markers = text_markers(ev('Asc.editor.asc_nativeGetFile()', editor_context)['result']['result']['value'])
        assert saved_markers and 'Disabled edits' not in saved_markers
        if extension == 'pptx':
            panel_context = open_panel()
            ev('document.getElementById("enabled").checked=false;document.getElementById("enabled").dispatchEvent(new Event("change"));', panel_context)
            for attempt in range(20):
                time.sleep(0.1)
                if not ev('document.getElementById("enabled").checked', plugin_context)['result']['result']['value']:
                    break
            else:
                raise AssertionError('The control panel could not pause the background worker')
            ev('Asc.plugin.button()', panel_context)
            ev('Asc.plugin.onEvent("onDocumentContentReady",{})', plugin_context)
        else:
            ev('document.getElementById("enabled").checked=false;document.getElementById("enabled").dispatchEvent(new Event("change"));', plugin_context)
        disabled_edit = command.replace('ONLYOFFICE actual unsaved edits', 'Disabled edits') if extension != 'xlsx' else 'Api.GetActiveSheet().GetRange("A1").SetValue("Disabled edits");'
        ev('new Promise(resolve=>Asc.plugin.callCommand(function(){' + disabled_edit + '},false,true,()=>resolve(true)))', plugin_context)
        time.sleep(2)
        assert files[0].read_bytes() == before, 'disabled saves still wrote data'
        print('PASS switching off prevents further writes', flush=True)
        if options.in_place:
            # Remove the disabled edit, then move away from and back to the saved
            # point. These are real SDK undo/redo actions, not synthetic flag writes.
            for attempt in range(5):
                ev('Asc.editor.' + ('asc_Undo' if extension == 'xlsx' else 'Undo') + '()', editor_context)
                time.sleep(0.3)
                if not ev('Asc.editor.isDocumentModified()', editor_context)['result']['result']['value']:
                    break
            else:
                raise AssertionError('Undo did not reach the saved point: ' + str(ev('JSON.stringify(Object.fromEntries(Object.entries(AscCommon.History).filter(([k,v])=>typeof v==="number"||typeof v==="boolean"||v===null)))', editor_context)))
            undo_markers = text_markers(ev('Asc.editor.asc_nativeGetFile()', editor_context)['result']['result']['value'])
            assert undo_markers == saved_markers, 'Undo reached a clean point with different fixture text: ' + str(undo_markers)
            state = ev('({modified:Asc.editor.isDocumentModified(),nativeDirty:window.testNativeDirty})', editor_context)['result']['result']['value']
            assert state == {'modified': False, 'nativeDirty': False}, 'Undo back to the saved snapshot was not clean: ' + str(state)
            for action, dirty in [('Undo', True), ('Redo', False)]:
                ev('Asc.editor.' + ('asc_' if extension == 'xlsx' else '') + action + '()', editor_context)
                time.sleep(0.3)
                state = ev('({modified:Asc.editor.isDocumentModified(),nativeDirty:window.testNativeDirty})', editor_context)['result']['result']['value']
                assert state == {'modified': dirty, 'nativeDirty': dirty}, action + ' lost the saved history point: ' + str(state)
            print('PASS real undo/redo preserves the saved point and native warning', flush=True)
            close_test_window()
            for attempt in range(30):
                time.sleep(0.2)
                try:
                    remaining = json.load(urllib.request.urlopen(base + '/json', timeout=2))
                except (OSError, ValueError):
                    remaining = []
                if not any('editors/' in page.get('url', '') for page in remaining):
                    break
            else:
                raise AssertionError('Native close left the saved document open (possible save confirmation)')
            print('PASS native window close exits the saved document without a save confirmation', flush=True)
        else:
            close_test_window()
            time.sleep(1)
            remaining = json.load(urllib.request.urlopen(base + '/json', timeout=2))
            assert any('editors/' in page.get('url', '') for page in remaining), 'Closing a copy save discarded the unsaved original'
            print('PASS native close still protects the unsaved original after a copy save', flush=True)
        tested = True
        ws.close()
        break
    assert tested, 'No editor completed the actual save test'
    if not pages:
        print((root / 'app.log').read_text()[-3000:], flush=True)
    time.sleep(1)
finally:
    release_ack.set()
    if not tested and (root / 'app.log').exists():
        print('Isolated application diagnostics:', (root / 'app.log').read_text()[-2500:], flush=True)
    if app:
        stop_group(app)
    stop_group(xvfb)
    bridge.shutdown()
    bridge.server_close()
    shutil.rmtree(root)
    print('Only isolated test processes stopped; temporary data removed.', flush=True)

#!/usr/bin/env python3
"""Real ONLYOFFICE test; isolated profile/instance port and authenticated TCP Xvfb.

Requires websocket-client, flatpak, xauth, xdpyinfo and Xvfb. Never uses the
user's display or profile. Synthetic input, output and SDK runtime are removed.
"""
import sys, os, json, shutil, socket, subprocess, time, signal, threading, zipfile, urllib.request, shlex, re
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
options = parser.parse_args()
if options.rich and options.format != 'docx':
    parser.error('--rich is a DOCX fixture option')
if options.untitled and options.in_place:
    parser.error('Untitled documents must be saved as copies')
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
    if process.poll() is None:
        try:
            os.killpg(process.pid, signal.SIGTERM)
            process.wait(timeout=10)
        except ProcessLookupError:
            pass
        except subprocess.TimeoutExpired:
            os.killpg(process.pid, signal.SIGKILL)
            process.wait()

def port():
    with socket.socket() as s:
        s.bind(('127.0.0.1', 0))
        return s.getsockname()[1]
config = root / 'config'
data = root / 'data'
cache = root / 'cache'
mount = root / 'mount'
mount.mkdir(exist_ok=True)
settings = Settings(mount, mount / 'Office AutoSave', require_mount=False, bridge_token='isolated-test-token-only-' + 'x' * 32)
bridge = make_server(Bridge(settings), 0)
threading.Thread(target=bridge.serve_forever, daemon=True).start()
guid = '{F2C67A12-E94D-4EF6-8C32-20D71F3B0294}'
plugins = data / 'onlyoffice/desktopeditors/sdkjs-plugins' / guid
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
instance = port()
debug = port()
app = None
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
            match = re.search('DevTools listening on ws://127.0.0.1:(\\d+)', (root / 'app.log').read_text())
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
        ev('Asc.editor.asc_pluginRun("asc.' + guid + '",0)', editor_context)
        time.sleep(3)
        ev('Array.from(document.querySelectorAll("iframe")).map(f=>({id:f.id,src:f.src}))', editor_context)
        call('Page.getFrameTree', {})
        plugin_context = None
        for cid in list(contexts):
            result = ev('!!document.getElementById("enabled")', cid)
            if result.get('result', {}).get('result', {}).get('value'):
                plugin_context = cid
        assert plugin_context, 'plugin iframe did not load'
        print('Initial plugin state:', ev('document.getElementById("status").textContent', plugin_context), flush=True)
        detected = ev('document.getElementById("source").value', plugin_context)['result']['result']['value']
        assert detected == ('' if options.untitled else str(fixture)), 'native path detection failed: ' + detected
        print('PASS current source path is detected automatically', flush=True)
        setup = 'document.getElementById("copy-directory").value=' + json.dumps(str(mount / 'Chosen by user')) + ';document.getElementById("enabled").checked=true;document.getElementById("enabled").dispatchEvent(new Event("change"));'
        ev(setup, plugin_context)
        commands = {'docx': 'var doc=Api.GetDocument();var p=Api.CreateParagraph();p.AddText("ONLYOFFICE actual unsaved edits");doc.Push(p);', 'xlsx': 'Api.GetActiveSheet().GetRange("A1").SetValue("ONLYOFFICE actual unsaved edits");Api.GetActiveSheet().GetRange("B1").SetValue("=1+2");', 'pptx': 'var fill=Api.CreateSolidFill(Api.CreateRGBColor(255,255,255));var shape=Api.CreateShape("rect",2500000,500000,fill,Api.CreateStroke(0,fill));shape.GetDocContent().GetElement(0).AddText("ONLYOFFICE actual unsaved edits");Api.GetPresentation().GetSlideByIndex(0).AddObject(shape);'}
        command = commands[extension]
        ev('Asc.plugin.callCommand(function(){' + command + '},false,true);', plugin_context)
        for i in range(40):
            time.sleep(0.5)
            result = ev('({status:document.getElementById("status").textContent,target:document.getElementById("target").textContent,enabled:document.getElementById("enabled").checked})', plugin_context)
            files = [fixture] if options.in_place else list((mount / 'Chosen by user').glob('*.' + extension))
            if files:
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
        if not options.in_place and (not options.untitled):
            with zipfile.ZipFile(fixture) as z:
                assert not any((b'ONLYOFFICE actual unsaved edits' in z.read(n) for n in z.namelist() if n.endswith('.xml')))
            print('PASS outside original remains unchanged', flush=True)
        before = files[0].read_bytes()
        ev('document.getElementById("enabled").checked=false;document.getElementById("enabled").dispatchEvent(new Event("change"));', plugin_context)
        ev('Asc.plugin.callCommand(function(){' + command.replace('ONLYOFFICE actual unsaved edits', 'Disabled edits') + '},false,true);', plugin_context)
        time.sleep(2)
        assert files[0].read_bytes() == before, 'disabled saves still wrote data'
        print('PASS switching off prevents further writes', flush=True)
        tested = True
        ws.close()
    assert tested, 'No editor completed the actual save test'
    if not pages:
        print((root / 'app.log').read_text()[-3000:], flush=True)
    time.sleep(1)
finally:
    if app:
        try:
            os.killpg(app.pid, signal.SIGTERM)
            app.wait(timeout=10)
        except ProcessLookupError:
            pass
        except subprocess.TimeoutExpired:
            os.killpg(app.pid, signal.SIGKILL)
            app.wait()
    try:
        os.killpg(xvfb.pid, signal.SIGTERM)
        xvfb.wait(timeout=5)
    except ProcessLookupError:
        pass
    bridge.shutdown()
    bridge.server_close()
    shutil.rmtree(root)
    print('Only isolated test processes stopped; temporary data removed.', flush=True)

"""Ctrl+S to visible text in the integration runner's private Wayland seat only."""
import socket
import subprocess
import json
import os
from pathlib import Path
import sys
import time
import zipfile

import numpy as np
from PIL import Image


def measure(directory, capture):
    # Require the runner's private authentication file, prefix, document and
    # allow-host barrier even if this helper is invoked independently.
    root = directory.parent.parent
    assert directory == root / 'mock-mount/PowerPoint' and (directory / 'allow-host').is_file()
    assert os.environ['WINEPREFIX'] == str(root / 'prefix')
    runtime = root / 'wayland'
    assert os.environ['XDG_RUNTIME_DIR'] == str(runtime)
    assert os.environ['WAYLAND_DISPLAY'] == 'wayland-office-test' and not os.environ.get('DISPLAY')
    seat = socket.socket(socket.AF_UNIX, socket.SOCK_DGRAM)
    seat.connect(str(runtime / 'office-headless-input'))

    def key(code, states=(1, 0)):
        for state in states:
            seat.send(f'key {code} {state}'.encode())

    left, top, right, bottom = map(int, (directory / 'paint-bounds').read_text().split())
    assert 0 <= left < right < 1280 and 0 <= top < bottom < 800
    seat.send(f'move {(left + right) // 2} {(top + bottom) // 2}'.encode())
    time.sleep(.05)
    for action in (b'down', b'up'):
        seat.send(action)
    time.sleep(.2)

    def screen():
        output = directory / 'frame.png'
        subprocess.run([capture, str(output)], check=True, stderr=subprocess.DEVNULL, timeout=3)
        with Image.open(output) as frame:
            return np.asarray(frame.convert('L'))[max(0, top-4):bottom+6, max(0, left-4):right+68]

    key(107)  # End collapses the selected text to its end.
    time.sleep(0.35)
    empty = screen()
    key(17)
    time.sleep(0.35)
    with_w = screen()
    key(14)  # BackSpace
    time.sleep(0.35)
    without_w = screen()
    mask = (empty > 180) & (without_w > 180) & (with_w < 110)
    y, x = np.where(mask)
    print('Calibration', (left, top, right, bottom), len(x), flush=True)
    assert len(x) >= 8 and 3 <= np.ptp(x) <= 70 and 3 <= np.ptp(y) <= 70, 'Could not localize the typed glyph'
    timings = []
    for trial in range(7):
        (directory / f'prepare-{trial}').touch()
        deadline = time.monotonic() + 5
        while not (directory / f'dirty-{trial}').exists():
            assert time.monotonic() < deadline, 'Office did not confirm trial edit'
            time.sleep(.01)
        assert (screen()[mask] > 180).mean() > 0.8, 'Glyph already visible before save'
        started = time.perf_counter()
        key(29, (1,))  # Ctrl
        key(31)
        key(29, (0,))
        key(17)
        while (screen()[mask] < 130).mean() < 0.8:
            if time.perf_counter() - started > 8:
                raise RuntimeError('Save did not return to the slide textbox')
        timings.append(round((time.perf_counter() - started) * 1000, 1))
        deadline = time.monotonic() + 8
        while True:
            try:
                with zipfile.ZipFile(directory / 'source.pptx') as archive:
                    if any(f'Paint trial {trial}'.encode() in archive.read(name) for name in archive.namelist()
                           if name.startswith('ppt/slides/slide') and name.endswith('.xml')):
                        break
            except (FileNotFoundError, zipfile.BadZipFile):
                pass
            assert time.monotonic() < deadline, 'Native save did not persist the trial edit'
            time.sleep(.03)
        if trial < 6:
            key(14)
            time.sleep(0.35)
    (directory / 'paint.json').write_text(json.dumps(timings))
    print('PowerPoint Ctrl+S to visible glyph (ms)', timings, flush=True)


if __name__ == '__main__':
    measure(Path(sys.argv[1]), sys.argv[2])

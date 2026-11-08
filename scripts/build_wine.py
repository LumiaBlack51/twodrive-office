#!/usr/bin/env python3
"""Cross-compile the thin x64 Wine COM adapter; never starts Wine/Office."""
import argparse
import os
import shutil
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]

def build(compiler=None, output=None):
    compiler = compiler or os.environ.get('CXX_WINE') or shutil.which('x86_64-w64-mingw32-g++')
    if not compiler:
        raise SystemExit('Set CXX_WINE to an x86_64-w64-mingw32-g++ compiler')
    output = output or ROOT / 'dist/wine/TwoDrive.Wine.dll'
    output.parent.mkdir(parents=True, exist_ok=True)
    subprocess.run([str(compiler), '-std=c++17', '-O2', '-static', '-shared',
                    '-Wall', '-Wextra', '-Wno-unused-parameter', '-I' + str(ROOT / 'wine'),
                    str(ROOT / 'wine/AddIn.cpp'), '-o', str(output),
                    '-lole32', '-loleaut32', '-luuid', '-lwinhttp', '-ladvapi32'], check=True)
    return output

if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--compiler')
    parser.add_argument('--output', type=Path)
    args = parser.parse_args()
    print(build(args.compiler, args.output))

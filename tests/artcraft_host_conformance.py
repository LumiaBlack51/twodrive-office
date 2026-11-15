#!/usr/bin/env python3
"""Opt-in conformance with pinned upstream transport/dialog components, NOT a native UI test.

Compiles only process.rs, push.rs and dialog.rs with a minimal Manifest/lock shim.
No Office binary is compiled, launched or installed. Downloads and targets are disposable.
"""
import argparse
import hashlib
import io
import json
import os
import subprocess
import tempfile
import urllib.request
import zipfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]

HARNESS = r'''
use serde_json::{json, Value};
use std::{fs, path::Path, sync::{Mutex, MutexGuard}, time::{Duration, Instant}};
#[derive(serde::Deserialize)] pub struct Run { command: String, args: Vec<String> }
#[derive(serde::Deserialize)] pub struct Manifest { id: String, run: Run }
fn lock<T>(m: &Mutex<T>) -> MutexGuard<'_, T> { m.lock().unwrap_or_else(|p| p.into_inner()) }
fn valid_id(s: &str) -> bool {
    s.chars().next().is_some_and(|c| c.is_ascii_alphanumeric()) && s.len() <= 128
        && s.chars().all(|c| c.is_ascii_alphanumeric() || matches!(c, '.' | '-' | '_'))
}
const HOST_ID: &str = APP_ID;
mod process;
mod push;
mod dialog;
fn ask(c: &mut process::Connection, method: &str, params: Value) -> Value {
    let result = c.request(method, params, Duration::from_secs(5)).unwrap();
    assert!(result.get("commands").is_none());
    assert!(result.get("open").is_none());
    if let Some(d) = result.get("dialog") {
        let parsed = DIALOG_PARSE(d).unwrap();
        for command in parsed.commands() { assert_eq!(command, "apply"); }
    }
    result
}
fn main() {
    let args: Vec<String> = std::env::args().collect();
    let dir = Path::new(&args[1]);
    let source = Path::new(&args[2]);
    let mount = Path::new(&args[3]);
    let new_bytes = fs::read(&args[4]).unwrap();
    let before = fs::read(source).unwrap();
    let m: Manifest = serde_json::from_slice(&fs::read(dir.join("plugin.json")).unwrap()).unwrap();
    let mut c = process::Connection::start(&m, dir).unwrap();
    let initialized = ask(&mut c, "initialize", json!({"protocolVersion":1,
        "host":{"id":HOST_ID,"protocol":"2.2","features":["dialogs","document","events"]}}));
    assert_eq!(initialized["savedVersionsOnly"], true);
    let doc = json!({"id":"doc-7","path":source,"name":"Protocol test","dirty":true});
    let context = json!({"document":doc});
    let form = ask(&mut c, "commands/run", json!({"command":"settings","context":context}));
    assert_eq!(form["dialog"]["fields"][0]["kind"], "checkbox");
    let mut params = form["dialog"]["params"].clone();
    params["fields"] = json!({"enabled":true,"directory":mount.join("Copies"),"interval":1});
    let applied = ask(&mut c,"commands/run",json!({"command":"apply","params":params,"context":context}));
    let target = applied["result"]["target"].as_str().unwrap();
    assert_ne!(Path::new(target), source);
    ask(&mut c,"commands/run",json!({"command":"sync","context":context}));
    let deadline = Instant::now() + Duration::from_secs(4);
    while fs::read(target).ok() != Some(before.clone()) {
        assert!(Instant::now() < deadline, "initial copy timed out");
        std::thread::sleep(Duration::from_millis(50));
    }
    assert_eq!(fs::read(source).unwrap(), before);
    // A native disk save without documentSaved must still be seen by polling.
    fs::write(source, &new_bytes).unwrap();
    ask(&mut c,"commands/run",json!({"command":"sync","context":context}));
    let deadline = Instant::now() + Duration::from_secs(4);
    while fs::read(target).ok() != Some(new_bytes.clone()) {
        assert!(Instant::now() < deadline, "changed disk copy timed out");
        std::thread::sleep(Duration::from_millis(50));
    }
    let state = ask(&mut c,"commands/run",json!({"command":"status","context":context}));
    assert_eq!(state["result"]["pending"], false);
    assert_eq!(state["result"]["error"], "");
    assert_eq!(ask(&mut c,"events/documentClosed",doc), json!({}));
    c.shutdown();
    assert_eq!(fs::read(source).unwrap(), new_bytes);
    println!("{}: upstream transport + native dialog parser round trip passed", HOST_ID);
}
'''


def document(extension, text):
    stream = io.BytesIO()
    main = {"docx": "word/document.xml", "xlsx": "xl/workbook.xml", "pptx": "ppt/presentation.xml"}[extension]
    with zipfile.ZipFile(stream, "w") as archive:
        archive.writestr("[Content_Types].xml", "<Types/>")
        archive.writestr(main, "<document>" + text + "</document>")
    return stream.getvalue()


def run(sources=None, cargo_home=None, plugin_dir=None):
    pins = json.loads((ROOT / "artcraft/protocol-sources.json").read_text())
    with tempfile.TemporaryDirectory(prefix="twodrive-upstream-conformance-") as temporary:
        work = Path(temporary)
        env = {**os.environ, "CARGO_TARGET_DIR": str(work / "target"), "PYTHONDONTWRITEBYTECODE": "1"}
        if cargo_home:
            env["CARGO_HOME"] = str(cargo_home)
        for app, pin in pins["hosts"].items():
            crate = work / app
            src = crate / "src"
            src.mkdir(parents=True)
            for path, digest in pin["files"].items():
                if sources:
                    content = (sources / app / path).read_bytes()
                else:
                    url = f"https://raw.githubusercontent.com/{pin['repo']}/{pin['sha']}/{path}"
                    with urllib.request.urlopen(url, timeout=30) as response:
                        content = response.read(1024 * 1024)
                if hashlib.sha256(content).hexdigest() != digest:
                    raise RuntimeError("upstream source hash mismatch: " + path)
                (src / Path(path).name).write_bytes(content)
            parser = "dialog::parse_dialog" if app == "wordcraft" else "dialog::Dialog::parse"
            (src / "main.rs").write_text(HARNESS.replace("APP_ID", json.dumps(app)).replace("DIALOG_PARSE", parser))
            (crate / "Cargo.toml").write_text('[package]\nname="twodrive-host-conformance"\nversion="0.1.0"\nedition="2024"\n'
                '[dependencies]\nserde={version="1", features=["derive"]}\nserde_json="1"\nlog="0.4"\n')
            case = work / (app + "-data")
            case.mkdir()
            mount = case / "mock-mount"
            mount.mkdir()
            config = case / "config/twodrive-office"
            config.mkdir(parents=True)
            (config / "settings.json").write_text(json.dumps({"mount_root": str(mount),
                "copy_directory": str(mount / "Copies"), "require_mount": False, "sync_interval_seconds": 1}))
            extension = {"wordcraft": "docx", "gridcraft": "xlsx", "deckcraft": "pptx"}[app]
            source, changed = case / ("source." + extension), case / ("changed." + extension)
            source.write_bytes(document(extension, "original saved version"))
            changed.write_bytes(document(extension, "second native disk save"))
            env.update({"XDG_CONFIG_HOME": str(case / "config"), "XDG_STATE_HOME": str(case / "state")})
            # No graphical runtime or host document/profile is used by this harness.
            env.pop("DISPLAY", None)
            env.pop("WAYLAND_DISPLAY", None)
            subprocess.run(["cargo", "run", "--offline", "--quiet", "--manifest-path", str(crate / "Cargo.toml"),
                "--", str(plugin_dir or ROOT / "artcraft"), str(source), str(mount), str(changed)],
                env=env, check=True, timeout=120)


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--sources", type=Path, help="Already fetched pinned sources (optional)")
    parser.add_argument("--cargo-home", type=Path, help="Disposable cached registry; use offline")
    parser.add_argument("--plugin-dir", type=Path, help="Test an extracted release package instead")
    args = parser.parse_args()
    run(args.sources, args.cargo_home, args.plugin_dir)

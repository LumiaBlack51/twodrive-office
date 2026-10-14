const test = require("node:test");
const assert = require("node:assert/strict");
const {readFileSync} = require("node:fs");
const {join} = require("node:path");
const vm = require("node:vm");
const {AutoSave} = require("../onlyoffice/autosave.js");

async function startup({source = "/mount/deck.pptx", inPlace = true, failure = null} = {}) {
    const controls = new Map(), calls = [], events = {}, timers = [];
    let engine;
    function control(id) {
        if (!controls.has(id)) controls.set(id, {
            value: id === "title" ? "Untitled" : "", checked: false, disabled: false,
            listeners: {}, addEventListener(name, listener) { this.listeners[name] = listener; },
            dispatchEvent(event) { this.listeners[event.type]?.(event); }
        });
        return controls.get(id);
    }
    const plugin = {info: {editorType: "slide"}, guid: "test-guid", attachEvent(name, callback) { events[name] = callback; },
        attachToolbarMenuClickEvent() {}, executeMethod() {}};
    const window = {Asc: {plugin}, TWODRIVE_BRIDGE: {url: "http://127.0.0.1:19836", token: "test-token"},
        parent: {AscDesktopEditor: {LocalFileGetSaved: () => !!source, LocalFileGetSourcePath: () => source},
            Asc: {editor: {isDocumentModified: () => false}}},
        TwoDriveAutoSave: {AutoSave: class extends AutoSave { constructor(...args) { super(...args); engine = this; } }},
        addEventListener() {}};
    vm.runInNewContext(readFileSync(join(__dirname, "../onlyoffice/plugin.js"), "utf8"), {
        window, document: {getElementById: control}, crypto: {randomUUID: () => "document-id"},
        URL, AbortSignal, Event, setInterval(callback) { timers.push(callback); return timers.length; }, clearInterval() {},
        async fetch(url, options) {
            const path = new URL(url).pathname;
            calls.push({path, method: options.method || "GET"});
            const data = path === "/settings" ? {copy_directory: "/mount/Copies"} :
                failure && path === "/sessions" ? {error: failure} :
                {session_id: "session-id", target: "/mount/deck.pptx", in_place: inPlace, extension: "pptx"};
            return {ok: !(failure && path === "/sessions"), json: async () => data};
        }
    });
    plugin.init();
    await new Promise(setImmediate);
    return {controls, calls, events, timers, engine};
}

test("TwoDrive startup enables an unchanged document without any snapshot writes", async () => {
    const state = await startup();
    assert.equal(state.engine.enabled, true);
    assert.equal(state.controls.get("enabled").checked, true);
    for (let i = 0; i < 20; i++) for (const timer of state.timers) await timer();
    assert.equal(state.engine.dueAt, null);
    assert.deepEqual(state.calls.map(call => call.path), ["/settings", "/sessions"]);
});

test("canonical bridge classification rejects automatic copy sessions and closes them", async () => {
    // Includes aliases whose displayed path looks inside the mount but resolves
    // outside it. Only the bridge's canonical in_place decision is trusted.
    const state = await startup({inPlace: false});
    assert.equal(state.engine.enabled, false);
    assert.equal(state.controls.get("enabled").checked, false);
    assert.deepEqual(state.calls.at(-1), {path: "/sessions/session-id", method: "DELETE"});
    assert.equal(state.calls.some(call => call.path.endsWith("/snapshot")), false);
});

test("untitled documents and rejected mounts never start automatic writes", async () => {
    const blank = await startup({source: ""});
    assert.equal(blank.engine.enabled, false);
    assert.equal(blank.calls.some(call => call.path === "/sessions"), false);
    const rejected = await startup({failure: "TwoDrive 挂载已断开"});
    assert.equal(rejected.engine.enabled, false);
    assert.match(rejected.controls.get("status").textContent, /挂载已断开/);
    assert.equal(rejected.calls.some(call => call.path.endsWith("/snapshot")), false);
});

test("manual pause survives later content-ready events and timer polling", async () => {
    const state = await startup();
    const enabled = state.controls.get("enabled");
    enabled.checked = false;
    enabled.dispatchEvent(new Event("change"));
    await new Promise(setImmediate);
    state.events.onDocumentContentReady();
    for (const timer of state.timers) await timer();
    assert.equal(state.engine.enabled, false);
    assert.equal(state.calls.filter(call => call.path === "/sessions").length, 1);
});

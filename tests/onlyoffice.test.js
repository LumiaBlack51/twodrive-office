const test = require("node:test");
const assert = require("node:assert/strict");
const {AutoSave} = require("../onlyoffice/autosave.js");
const {markSaved} = require("../onlyoffice/native-state.js");
const content = text => new TextEncoder().encode(text).buffer;

test("opening an unchanged document enables scheduling without exporting or rewriting it", async () => {
    let now = 0, exports = 0, writes = 0;
    const engine = new AutoSave(async () => { exports++; return content("edited"); }, async () => writes++, () => {}, () => now);
    engine.start(false);
    for (now = 0; now < 20000; now += 1000) await engine.tick();
    assert.equal(engine.enabled, true);
    assert.equal(exports, 0);
    assert.equal(writes, 0);
    engine.changed(); now += 1000; await engine.tick();
    assert.equal(writes, 1);
});

test("coalesces typing and saves latest export once, with maximum delay", async () => {
    let now = 0, writes = 0;
    const engine = new AutoSave(async () => content("current edit"), async () => writes++, () => {}, () => now);
    engine.start();
    for (now = 0; now < 5000; now += 500) { engine.changed(); await engine.tick(); }
    assert.equal(writes, 0);
    await engine.tick();
    assert.equal(writes, 1);
    engine.changed(); now += 1000; await engine.tick();
    assert.equal(writes, 1, "unchanged binary is not written again");
});

test("failed save retains pending changes and retries with backoff", async () => {
    let now = 0, calls = 0;
    const engine = new AutoSave(async () => content("current"), async () => { if (++calls === 1) throw new Error("mount disconnected"); }, () => {}, () => now);
    engine.start(); now = 1000; await engine.tick();
    assert.equal(engine.savedRevision, 0);
    now = 2000; await engine.tick(); assert.equal(calls, 1);
    now = 3000; await engine.tick(); assert.equal(calls, 2);
    assert.equal(engine.savedRevision, engine.revision);
});

test("disabled during export cannot write a stale snapshot", async () => {
    let finish, writes = 0;
    const engine = new AutoSave(() => new Promise(resolve => { finish = resolve; }), async () => writes++, () => {});
    engine.start(); const running = engine.tick(true);
    engine.stop(); finish(content("stale")); await running;
    assert.equal(writes, 0);
});

test("edits during export remain pending and save again", async () => {
    let now = 0, finish;
    const engine = new AutoSave(() => new Promise(resolve => { finish = resolve; }), async () => {}, () => {}, () => now);
    engine.start(); const running = engine.tick(true);
    now = 500; engine.changed(); finish(content("snapshot")); await running;
    assert.ok(engine.revision > engine.savedRevision);
    assert.equal(engine.dueAt, 1500);
});

test("busy export is never reentered and invalid export is never written", async () => {
    let finish, exports = 0, writes = 0;
    const engine = new AutoSave(() => { exports++; return new Promise(resolve => { finish = resolve; }); }, async () => writes++, () => {});
    engine.start(); const running = engine.tick(true);
    await engine.tick(true); assert.equal(exports, 1);
    finish(new ArrayBuffer(0)); await running;
    assert.equal(writes, 0); assert.equal(engine.savedRevision, 0);
});

test("editor acknowledgement requires a successful write and a still-enabled generation", async () => {
    let finish, acknowledged = 0;
    const engine = new AutoSave(async () => content("snapshot"), () => new Promise(resolve => { finish = resolve; }),
        () => {}, () => 0, () => { acknowledged++; });
    engine.start(); const running = engine.tick(true);
    await Promise.resolve();
    assert.equal(acknowledged, 0);
    engine.stop(); finish({status: "saved_local"}); await running;
    assert.equal(acknowledged, 0, "a disabled or replaced session cannot clear the editor warning");
});

test("failed writes never acknowledge the editor and can be retried", async () => {
    let failed = true, acknowledged = 0;
    const engine = new AutoSave(async () => content("snapshot"), async () => {
        if (failed) throw new Error("mount disconnected");
        return {status: "saved_local"};
    }, () => {}, () => 0, (_, result) => { assert.equal(result.status, "saved_local"); acknowledged++; });
    engine.start(); await engine.tick(true);
    assert.equal(acknowledged, 0);
    assert.equal(engine.savedRevision, 0);
    failed = false; await engine.tick(true);
    assert.equal(acknowledged, 1);
    assert.equal(engine.savedRevision, engine.revision);
});

test("snapshot comparison can reschedule edits missed by editor events without claiming everything is saved", async () => {
    const reports = [];
    const engine = new AutoSave(async () => content("snapshot"), async () => ({}), message => reports.push(message),
        () => 0, () => { engine.changed(); return "saved"; });
    engine.start(); await engine.tick(true);
    assert.ok(engine.savedRevision < engine.revision);
    assert.equal(engine.dueAt, 1000);
    assert.match(reports.at(-1), /新修改仍待保存/);
});

function desktopFixture() {
    function History() {
        this.recovery = 0; this.index = 1; this.userMode = true; this.saved = 0; this.force = false;
    }
    // Equivalent to Desktop's compiled Reset_SavedIndex, with deliberately
    // different field names. This method must never be called by the adapter.
    History.prototype.reset = function(a) {
        this.recovery = null === this.recovery && -1 === this.index ? null : this.index;
        !0 === this.userMode ? !0 === a && (this.saved = this.index, this.force = !1) : this.force = !1;
    };
    const history = new History();
    const desktop = {nativeDirty: true};
    const editor = {
        ready: true, frame: false, modified: true, binary: "saved snapshot", notifications: [],
        asc_nativeGetFile() { return this.binary; },
        isDocumentModified: function() { return !this.ready || this.frame ? true : this.modified; },
        SetDocumentModified(value) {
            this.modified = value;
            desktop.nativeDirty = history.index !== history.saved || history.force;
            this.notifications.push(value);
        },
        _onUpdateDocumentCanSave() { this.canSaveIndicator = this.isDocumentModified(); }
    };
    return {Asc: {editor}, AscCommon: {History: history}, AscDesktopEditor: desktop};
}

test("matching committed snapshot clears native and editor warnings while preserving recovery and undo/redo", () => {
    const window = desktopFixture(), editor = window.Asc.editor, history = window.AscCommon.History;
    history.force = true;
    assert.equal(markSaved(window, "saved snapshot"), "saved");
    assert.equal(history.recovery, 0, "Desktop's recovery checkpoint is not advanced by an external write");
    assert.equal(history.saved, 1);
    assert.equal(editor.isDocumentModified(), false);
    assert.equal(window.AscDesktopEditor.nativeDirty, false);
    assert.equal(editor.canSaveIndicator, false);
    history.index--; editor.SetDocumentModified(history.index !== history.saved);
    assert.equal(editor.isDocumentModified(), true, "undo away from the saved point must warn");
    history.index++; editor.SetDocumentModified(history.index !== history.saved);
    assert.equal(editor.isDocumentModified(), false, "redo back to the saved point must be clean");
});

test("editing within the same history point during a write cannot be acknowledged", () => {
    const window = desktopFixture();
    window.Asc.editor.binary = "new edits, same undo point";
    assert.equal(markSaved(window, "saved snapshot"), "pending");
    assert.equal(window.AscCommon.History.saved, 0);
    assert.equal(window.AscCommon.History.force, true);
    assert.deepEqual(window.Asc.editor.notifications, [true]);
    assert.equal(window.AscDesktopEditor.nativeDirty, true);
});

test("a native save in flight or an active editor frame preserves the unsaved warning", () => {
    for (const property of ["ready", "frame"]) {
        const window = desktopFixture();
        window.Asc.editor[property] = property === "frame";
        assert.equal(markSaved(window, "saved snapshot"), "pending");
        assert.equal(window.AscCommon.History.saved, 0);
        assert.equal(window.AscDesktopEditor.nativeDirty, true);
        assert.deepEqual(window.Asc.editor.notifications, []);
    }
});

test("uncommitted cell input cannot momentarily clear the native warning", () => {
    const window = desktopFixture(), editor = window.Asc.editor;
    editor.cellEditMode = () => true;
    editor.asc_isDocumentModified = function() { return !this.ready || this.cellEditMode() || this.frame ? true : this.modified; };
    assert.equal(markSaved(window, "saved snapshot"), "pending");
    assert.equal(window.AscCommon.History.saved, 0);
    assert.deepEqual(editor.notifications, []);
});

test("unknown or ambiguous SDK history layouts never clear the warning", () => {
    const window = desktopFixture();
    Object.getPrototypeOf(window.AscCommon.History).reset = function() { throw new Error("must not be invoked"); };
    assert.equal(markSaved(window, "saved snapshot"), "unsupported");
    assert.equal(window.AscCommon.History.saved, 0);
    assert.deepEqual(window.Asc.editor.notifications, []);
    const ambiguous = desktopFixture();
    const prototype = Object.getPrototypeOf(ambiguous.AscCommon.History);
    prototype.anotherReset = prototype.reset;
    assert.equal(markSaved(ambiguous, "saved snapshot"), "unsupported");
});
